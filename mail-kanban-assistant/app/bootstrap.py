from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from app.application.dtos import RunDailyResultDTO
from app.application.llm_input import LlmTextPolicy
from app.application.policies import TaskAutomationPolicy
from app.application.use_cases import (
    BuildMorningDigestUseCase,
    ExtractTasksUseCase,
    IngestMessagesUseCase,
    TriageMessagesUseCase,
)
from app.application.use_cases.kanban_sync import (
    ExportLocalKanbanBoardUseCase,
    ListKanbanSyncStatusUseCase,
    PreviewKanbanSyncCandidatesUseCase,
    ResyncChangedFingerprintsKanbanUseCase,
    RetryFailedKanbanSyncUseCase,
    ShowKanbanTaskSyncUseCase,
    SyncApprovedTasksToKanbanUseCase,
)
from app.application.use_cases.process_apple_mail_drop import ProcessAppleMailDropUseCase
from app.application.use_cases.approve_review_item import ApproveReviewItemUseCase
from app.application.use_cases.enqueue_review_items import EnqueueReviewItemsUseCase
from app.application.use_cases.list_pending_reviews import ListPendingReviewsUseCase
from app.application.use_cases.reject_review_item import RejectReviewItemUseCase
from app.config import AppSettings
from app.infrastructure.clock import SystemClock
from app.application.ports import KanbanPort
from app.infrastructure.kanban.factory import make_kanban_port
from app.infrastructure.llm.client import LmStudioStructuredClient
from app.infrastructure.logging.logger import StructuredLoggerAdapter
from app.infrastructure.mail.eml_reader import EmlDirectoryReader
from app.infrastructure.mail.mbox_reader import MboxFileReader
from app.infrastructure.fs.maildrop_filesystem import OsMaildropFilesystem
from app.infrastructure.mail.apple_mail_drop_reader import AppleMailDropIncomingScanner
from app.infrastructure.storage.repositories import (
    SqliteDigestContextRepository,
    SqliteMessageRepository,
    SqliteMorningDigestRepository,
    SqlitePipelineRunRepository,
    SqliteReviewRepository,
    SqliteTaskRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_ingested_artifact_repository import SqliteIngestedArtifactRepository
from app.infrastructure.storage.sqlite_kanban_sync_repository import SqliteKanbanSyncRepository
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from app.utils.ids import new_run_id

_SCHEMA_PATH = Path(__file__).parent / "infrastructure" / "storage" / "schema.sql"


def init_database(settings: AppSettings) -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_connection(settings.database_path)
    try:
        initialize_database(conn, _SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        conn.close()


def _llm_text_policy(settings: AppSettings) -> LlmTextPolicy:
    return LlmTextPolicy(
        max_input_chars=int(settings.llm_max_input_chars),
        truncate_strategy=settings.message_body_truncate_strategy,
    )


def make_lm_studio_client(settings: AppSettings, logger: StructuredLoggerAdapter) -> LmStudioStructuredClient:
    return LmStudioStructuredClient(
        base_url=settings.lm_studio_base_url,
        model=settings.lm_studio_model,
        timeout_seconds=settings.lm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_output_tokens=int(settings.llm_max_output_tokens),
        llm_text_policy=_llm_text_policy(settings),
        logger=logger,
    )


class KanbanCliWiring(NamedTuple):
    kanban_port: KanbanPort
    preview: PreviewKanbanSyncCandidatesUseCase
    sync: SyncApprovedTasksToKanbanUseCase
    retry: RetryFailedKanbanSyncUseCase
    status: ListKanbanSyncStatusUseCase
    export: ExportLocalKanbanBoardUseCase
    resync_changed: ResyncChangedFingerprintsKanbanUseCase
    show_task_sync: ShowKanbanTaskSyncUseCase


def build_kanban_wiring(
    conn,
    clock: SystemClock,
    logger: StructuredLoggerAdapter,
    settings: AppSettings,
    tasks_repo: SqliteTaskRepository,
) -> KanbanCliWiring:
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    kanban = make_kanban_port(settings, logger)
    preview = PreviewKanbanSyncCandidatesUseCase(tasks=tasks_repo, sync=sync_repo, logger=logger, settings=settings)
    sync_uc = SyncApprovedTasksToKanbanUseCase(
        tasks=tasks_repo,
        sync=sync_repo,
        kanban=kanban,
        logger=logger,
        settings=settings,
    )
    retry_uc = RetryFailedKanbanSyncUseCase(
        tasks=tasks_repo,
        sync=sync_repo,
        kanban=kanban,
        logger=logger,
        settings=settings,
    )
    status_uc = ListKanbanSyncStatusUseCase(sync=sync_repo, settings=settings)
    export_uc = ExportLocalKanbanBoardUseCase(settings=settings, logger=logger)
    resync_uc = ResyncChangedFingerprintsKanbanUseCase(
        tasks=tasks_repo,
        sync=sync_repo,
        kanban=kanban,
        logger=logger,
        settings=settings,
    )
    show_uc = ShowKanbanTaskSyncUseCase(tasks=tasks_repo, sync=sync_repo, settings=settings)
    return KanbanCliWiring(
        kanban_port=kanban,
        preview=preview,
        sync=sync_uc,
        retry=retry_uc,
        status=status_uc,
        export=export_uc,
        resync_changed=resync_uc,
        show_task_sync=show_uc,
    )


class AppWiring(NamedTuple):
    llm: LmStudioStructuredClient
    messages: SqliteMessageRepository
    triage_repo: SqliteTriageRepository
    tasks_repo: SqliteTaskRepository
    digests: SqliteMorningDigestRepository
    pipeline: SqlitePipelineRunRepository
    reviews: SqliteReviewRepository
    digest_ctx: SqliteDigestContextRepository
    enqueue_reviews: EnqueueReviewItemsUseCase
    triage_uc: TriageMessagesUseCase
    extract_uc: ExtractTasksUseCase
    digest_uc: BuildMorningDigestUseCase
    list_reviews_uc: ListPendingReviewsUseCase
    approve_review_uc: ApproveReviewItemUseCase
    reject_review_uc: RejectReviewItemUseCase


def build_process_apple_mail_drop_use_case(
    conn,
    clock: SystemClock,
    logger: StructuredLoggerAdapter,
) -> ProcessAppleMailDropUseCase:
    messages = SqliteMessageRepository(conn, clock)
    artifacts = SqliteIngestedArtifactRepository(conn, clock)
    fs = OsMaildropFilesystem(logger)
    scanner = AppleMailDropIncomingScanner()
    return ProcessAppleMailDropUseCase(
        messages=messages,
        artifacts=artifacts,
        fs=fs,
        scanner=scanner,
        logger=logger,
    )


def build_wiring(conn, clock: SystemClock, logger: StructuredLoggerAdapter, settings: AppSettings) -> AppWiring:
    llm = make_lm_studio_client(settings, logger)
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    tasks_repo = SqliteTaskRepository(conn, clock)
    digests = SqliteMorningDigestRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    digest_ctx = SqliteDigestContextRepository(conn)
    kanban_sync_repo = SqliteKanbanSyncRepository(conn, clock)

    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)
    triage_uc = TriageMessagesUseCase(
        messages=messages,
        triage=triage_repo,
        llm=llm,
        logger=logger,
        enqueue_reviews=enqueue,
        review_threshold=float(settings.review_confidence_threshold),
    )
    kanban = make_kanban_port(settings, logger)
    extract_uc = ExtractTasksUseCase(
        messages=messages,
        triage_repo=triage_repo,
        tasks_llm=llm,
        tasks=tasks_repo,
        kanban=kanban,
        logger=logger,
        enqueue_reviews=enqueue,
        review_threshold=float(settings.review_confidence_threshold),
    )
    digest_uc = BuildMorningDigestUseCase(
        digest_context=digest_ctx,
        digests=digests,
        clock=clock,
        logger=logger,
        settings=settings,
        kanban_sync=kanban_sync_repo,
    )

    list_reviews_uc = ListPendingReviewsUseCase(reviews=reviews)
    kb_wiring = build_kanban_wiring(conn, clock, logger, settings, tasks_repo)

    def _on_task_approved(task_id: int) -> None:
        kb_wiring.sync.execute(
            run_id=new_run_id(),
            provider=settings.kanban_provider,
            dry_run=False,
            limit=max(10, int(settings.kanban_sync_batch_size)),
            only_task_id=task_id,
        )

    approve_review_uc = ApproveReviewItemUseCase(
        reviews=reviews,
        messages=messages,
        triage=triage_repo,
        tasks=tasks_repo,
        logger=logger,
        on_task_approved=_on_task_approved if settings.kanban_auto_sync else None,
    )
    reject_review_uc = RejectReviewItemUseCase(
        reviews=reviews,
        messages=messages,
        triage=triage_repo,
        tasks=tasks_repo,
        logger=logger,
    )

    return AppWiring(
        llm=llm,
        messages=messages,
        triage_repo=triage_repo,
        tasks_repo=tasks_repo,
        digests=digests,
        pipeline=pipeline,
        reviews=reviews,
        digest_ctx=digest_ctx,
        enqueue_reviews=enqueue,
        triage_uc=triage_uc,
        extract_uc=extract_uc,
        digest_uc=digest_uc,
        list_reviews_uc=list_reviews_uc,
        approve_review_uc=approve_review_uc,
        reject_review_uc=reject_review_uc,
    )


def format_run_daily_stdout_summary(
    *,
    run_id: str,
    pipeline_db_id: int,
    inserted_total: int,
    duplicates_total: int,
    apple_mail_drop_ingested: int = 0,
    apple_mail_drop_duplicates: int = 0,
    triage: object,
    extract: object,
    digest_id: int,
    kanban_synced: int | None = None,
    kanban_skipped: int | None = None,
    kanban_failed: int | None = None,
) -> str:
    lines = [
        f"run_id={run_id}",
        f"pipeline_run_db_id={pipeline_db_id}",
        f"ingest.inserted_total={inserted_total}",
        f"ingest.duplicates_total={duplicates_total}",
        f"ingest.apple_mail_drop.ingested={apple_mail_drop_ingested}",
        f"ingest.apple_mail_drop.duplicates={apple_mail_drop_duplicates}",
        f"triage.processed={getattr(triage, 'processed')}",
        f"triage.failures={getattr(triage, 'failures')}",
        f"triage.reviews_enqueued={getattr(triage, 'reviews_enqueued')}",
        f"extract.messages_processed={getattr(extract, 'messages_processed')}",
        f"extract.tasks_created={getattr(extract, 'tasks_created')}",
        f"extract.failures={getattr(extract, 'failures')}",
        f"extract.reviews_enqueued={getattr(extract, 'reviews_enqueued')}",
        f"digest.digest_id={digest_id}",
    ]
    if kanban_synced is not None:
        lines.append(f"kanban.synced={kanban_synced}")
    if kanban_skipped is not None:
        lines.append(f"kanban.skipped={kanban_skipped}")
    if kanban_failed is not None:
        lines.append(f"kanban.failed={kanban_failed}")
    return "\n".join(lines) + "\n"


def run_daily(*, settings: AppSettings, digest_output: Path | None = None) -> RunDailyResultDTO:
    """Run ingest (optional) → triage → extract → digest."""

    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)

    run_id = new_run_id()
    logger.info("run_daily.start", run_id=run_id, app_env=settings.app_env)
    pipeline_db_id = w.pipeline.start_run(run_id=run_id, command="run-daily")
    policy = TaskAutomationPolicy(
        confidence_threshold=settings.task_confidence_threshold,
        auto_create_kanban=settings.auto_create_kanban_tasks,
    )

    ingest_uc = IngestMessagesUseCase(messages=w.messages, pipeline_runs=w.pipeline, logger=logger)
    drop_uc = build_process_apple_mail_drop_use_case(conn, clock, logger)

    inserted_total = 0
    duplicates_total = 0
    apple_mail_drop_ingested = 0
    apple_mail_drop_duplicates = 0
    try:
        if settings.mail_eml_dir is not None:
            res = ingest_uc.execute(
                EmlDirectoryReader(settings.mail_eml_dir),
                run_id=run_id,
                command="run-daily:ingest-eml",
                record_pipeline=False,
            )
            inserted_total += res.inserted
            duplicates_total += res.duplicates

        if settings.mail_mbox_path is not None:
            res = ingest_uc.execute(
                MboxFileReader(settings.mail_mbox_path),
                run_id=run_id,
                command="run-daily:ingest-mbox",
                record_pipeline=False,
            )
            inserted_total += res.inserted
            duplicates_total += res.duplicates

        drop_res = drop_uc.execute(maildrop_root=settings.maildrop_root, run_id=run_id)
        inserted_total += drop_res.ingested
        duplicates_total += drop_res.duplicate
        apple_mail_drop_ingested = drop_res.ingested
        apple_mail_drop_duplicates = drop_res.duplicate

        triage_res = w.triage_uc.execute(run_id=run_id, batch_limit=int(settings.triage_batch_size))
        extract_res = w.extract_uc.execute(
            run_id=run_id,
            policy=policy,
            batch_limit=int(settings.task_extraction_batch_size),
        )

        kanban_synced: int | None = None
        kanban_skipped: int | None = None
        kanban_failed: int | None = None
        if settings.kanban_auto_sync:
            kb_sync_run = SqliteKanbanSyncRepository(conn, clock)
            ksync = SyncApprovedTasksToKanbanUseCase(
                tasks=w.tasks_repo,
                sync=kb_sync_run,
                kanban=make_kanban_port(settings, logger),
                logger=logger,
                settings=settings,
            )
            kres = ksync.execute(
                run_id=f"{run_id}:kanban-auto",
                provider=settings.kanban_provider,
                dry_run=False,
                limit=int(settings.kanban_sync_batch_size),
                only_task_id=None,
            )
            kanban_synced = kres.synced
            kanban_skipped = kres.skipped
            kanban_failed = kres.failed

        pipeline_stats: dict[str, object] = {
            "ingest.inserted_total": inserted_total,
            "ingest.duplicates_total": duplicates_total,
            "ingest.apple_mail_drop.ingested": apple_mail_drop_ingested,
            "ingest.apple_mail_drop.duplicates": apple_mail_drop_duplicates,
            "triage.processed": triage_res.processed,
            "triage.failures": triage_res.failures,
            "triage.reviews_enqueued": triage_res.reviews_enqueued,
            "extract.messages_processed": extract_res.messages_processed,
            "extract.tasks_created": extract_res.tasks_created,
            "extract.failures": extract_res.failures,
            "extract.reviews_enqueued": extract_res.reviews_enqueued,
        }
        if kanban_synced is not None:
            pipeline_stats["kanban.synced"] = kanban_synced
            pipeline_stats["kanban.skipped"] = kanban_skipped or 0
            pipeline_stats["kanban.failed"] = kanban_failed or 0

        digest_res = w.digest_uc.execute(
            run_id=run_id,
            pipeline_run_db_id=pipeline_db_id,
            pipeline_stats=pipeline_stats,
        )

        w.pipeline.finish_run(
            pipeline_db_id,
            status="ok",
            metadata=json.dumps(
                {
                    "inserted": inserted_total,
                    "duplicates": duplicates_total,
                    "digest_id": digest_res.digest_id,
                },
                ensure_ascii=False,
            ),
        )

        if digest_output is not None:
            digest_output.parent.mkdir(parents=True, exist_ok=True)
            digest_output.write_text(digest_res.markdown, encoding="utf-8")

        stdout_summary = format_run_daily_stdout_summary(
            run_id=run_id,
            pipeline_db_id=pipeline_db_id,
            inserted_total=inserted_total,
            duplicates_total=duplicates_total,
            apple_mail_drop_ingested=apple_mail_drop_ingested,
            apple_mail_drop_duplicates=apple_mail_drop_duplicates,
            triage=triage_res,
            extract=extract_res,
            digest_id=digest_res.digest_id,
            kanban_synced=kanban_synced,
            kanban_skipped=kanban_skipped,
            kanban_failed=kanban_failed,
        )

        return RunDailyResultDTO(
            run_id=run_id,
            digest_markdown=digest_res.markdown,
            stdout_summary=stdout_summary,
            digest_id=digest_res.digest_id,
        )
    except Exception:
        w.pipeline.finish_run(
            pipeline_db_id,
            status="error",
            metadata=json.dumps({"error": "run-daily failed"}, ensure_ascii=False),
        )
        raise
    finally:
        w.llm.close()
        conn.close()

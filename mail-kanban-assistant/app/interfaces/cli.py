from __future__ import annotations

import dataclasses
import json
import logging
import sys
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

import typer

from app.application.llm_input import LlmTextPolicy
from app.application.reply_context_builder import SqliteReplyContextBuilder
from app.application.reply_draft_action_center_wiring import build_action_center_snapshot_with_reply_pins
from app.application.reply_draft_explain import explain_reply_draft_lines
from app.application.reply_draft_export_files import LocalReplyDraftExporter, default_export_path
from app.application.reply_draft_policy import assert_regenerate_preconditions
from app.application.reply_thread_resolution import infer_reply_state_for_thread, resolve_thread_message_ids
from app.application.use_cases.reply_draft_generate import GenerateReplyDraftUseCase
from app.application.use_cases.reply_draft_lifecycle import ApproveReplyDraftUseCase, ExportReplyDraftUseCase, RejectReplyDraftUseCase
from app.application.action_center_explain import (
    count_reply_critical_items,
    explain_action_item_lines,
    explain_message_lines,
    explain_thread_lines,
    find_action_item,
    find_thread_summary_for_message,
    snapshot_lite_from_summary,
)
from app.application.digest_markdown import compose_action_center_markdown_export
from app.application.doctor_report import DoctorEnvironmentUseCase, DoctorLineDTO, DoctorReportDTO
from app.application.dtos import DailyDigestContextDTO, DailyDigestStatsDTO
from app.application.launchd_plist import LaunchdPlistSpecDTO, render_launchd_plist_xml
from app.application.policies import TaskAutomationPolicy
from app.application.use_cases import IngestMessagesUseCase
from app.application.use_cases.prepare_maildrop import PrepareMaildropUseCase
from app.application.use_cases.kanban_sync import SyncApprovedTasksToKanbanUseCase
from app.application.use_cases.yougile_workspace import (
    YougileDiscoverWorkspaceUseCase,
    YougileSmokeSyncUseCase,
    build_yougile_env_fragment,
    render_yougile_discovery_text,
    run_yougile_deep_doctor,
    run_yougile_live_status_probe,
    yougile_cleanup_note_text,
)
from app.bootstrap import (
    build_kanban_wiring,
    build_process_apple_mail_drop_use_case,
    build_wiring,
    init_database,
    run_daily,
)
from app.config import AppSettings
from app.domain.enums import KanbanProvider, ReplyTone
from app.domain.reply_draft_errors import ReplyDraftError
from app.infrastructure.clock import SystemClock
from app.infrastructure.fs.maildrop_filesystem import OsMaildropFilesystem
from app.infrastructure.http.http_probe import UrllibHttpProbe
from app.infrastructure.logging.logger import StructuredLoggerAdapter
from app.infrastructure.mail.eml_reader import EmlDirectoryReader
from app.infrastructure.mail.mbox_reader import MboxFileReader
from app.infrastructure.storage.repositories import (
    SqliteDigestContextRepository,
    SqliteMessageRepository,
    SqlitePipelineRunRepository,
    SqliteReviewRepository,
    SqliteTaskRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_reply_draft_repository import SqliteReplyDraftRepository
from app.infrastructure.storage.sqlite_db import open_connection
from app.infrastructure.storage.sqlite_kanban_sync_repository import SqliteKanbanSyncRepository
from app.infrastructure.kanban.factory import make_kanban_port
from app.utils.ids import new_run_id

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _parse_kanban_provider(value: Optional[str], default: KanbanProvider) -> KanbanProvider:
    if value is None or str(value).strip() == "":
        return default
    return KanbanProvider(str(value).strip().lower())


def _parse_reply_tone(value: Optional[str], default: ReplyTone) -> ReplyTone:
    if value is None or str(value).strip() == "":
        return default
    try:
        return ReplyTone(str(value).strip().lower())
    except ValueError as exc:
        raise typer.BadParameter(f"unknown tone: {value!r}") from exc


def _reply_draft_bundle(settings: AppSettings, conn, clock: SystemClock):
    end = clock.now()
    start = end - timedelta(hours=int(settings.action_center_lookback_hours))
    digest_ctx = SqliteDigestContextRepository(conn)
    bundle = digest_ctx.load_action_center_raw_bundle(
        window_start=start,
        window_end=end,
        max_message_rows=int(settings.action_center_max_messages),
        kanban_provider=settings.kanban_provider,
    )
    kb_sync = SqliteKanbanSyncRepository(conn, clock)
    kb = kb_sync.load_kanban_digest_section(
        provider=settings.kanban_provider,
        auto_sync_enabled=settings.kanban_auto_sync,
    )
    return bundle.model_copy(
        update={
            "approved_ready_to_sync": kb.approved_ready_to_sync,
            "manual_resync_backlog": kb.manual_resync_pending,
        }
    )


def _make_reply_draft_generate_uc(
    conn,
    clock: SystemClock,
    logger: StructuredLoggerAdapter,
    settings: AppSettings,
    llm,
) -> GenerateReplyDraftUseCase:
    messages = SqliteMessageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    triage = SqliteTriageRepository(conn, clock)
    policy = LlmTextPolicy(
        max_input_chars=int(settings.llm_max_input_chars),
        truncate_strategy=settings.message_body_truncate_strategy,
    )
    builder = SqliteReplyContextBuilder(
        messages=messages,
        tasks=tasks,
        reviews=reviews,
        triage_get=triage.get_triage,
        settings=settings,
        llm_text_policy=policy,
    )
    drafts = SqliteReplyDraftRepository(conn, clock)
    return GenerateReplyDraftUseCase(drafts=drafts, llm=llm, builder=builder, clock=clock, logger=logger, settings=settings)


def _load_action_center_snapshot(settings: AppSettings, conn, clock: SystemClock):
    """Build action center from SQLite (same rules as morning digest)."""
    end = clock.now()
    bundle = _reply_draft_bundle(settings, conn, clock)
    snap, _pins = build_action_center_snapshot_with_reply_pins(conn, clock, settings, bundle, end)
    return snap, bundle


@app.callback()
def _configure_logging(log_level: str = typer.Option("INFO", "--log-level", help="Python logging level name.")) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")


@app.command("init-db")
def init_db() -> None:
    settings = AppSettings()
    init_database(settings)
    typer.echo(f"Database initialized at {settings.database_path.resolve()}")


@app.command("ingest-eml")
def ingest_eml(path: Path = typer.Option(..., "--path", exists=True, help="Directory with .eml files.")) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    uc = IngestMessagesUseCase(messages=w.messages, pipeline_runs=pipeline, logger=logger)
    run_id = new_run_id()
    try:
        res = uc.execute(EmlDirectoryReader(path), run_id=run_id, command="ingest-eml", record_pipeline=True)
        typer.echo(
            f"ingest-eml done: inserted={res.inserted} duplicates={res.duplicates} failures={res.failures} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("ingest-mbox")
def ingest_mbox(path: Path = typer.Option(..., "--path", exists=True, help="Path to .mbox file.")) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    uc = IngestMessagesUseCase(messages=w.messages, pipeline_runs=pipeline, logger=logger)
    run_id = new_run_id()
    try:
        res = uc.execute(MboxFileReader(path), run_id=run_id, command="ingest-mbox", record_pipeline=True)
        typer.echo(
            f"ingest-mbox done: inserted={res.inserted} duplicates={res.duplicates} failures={res.failures} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("triage")
def triage() -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    run_id = new_run_id()
    try:
        res = w.triage_uc.execute(run_id=run_id, batch_limit=int(settings.triage_batch_size))
        typer.echo(
            f"triage done: processed={res.processed} failures={res.failures} "
            f"reviews_enqueued={res.reviews_enqueued} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("extract-tasks")
def extract_tasks() -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    policy = TaskAutomationPolicy(
        confidence_threshold=settings.task_confidence_threshold,
        auto_create_kanban=settings.auto_create_kanban_tasks,
    )
    run_id = new_run_id()
    try:
        res = w.extract_uc.execute(
            run_id=run_id,
            policy=policy,
            batch_limit=int(settings.task_extraction_batch_size),
        )
        typer.echo(
            "extract-tasks done: "
            f"messages_processed={res.messages_processed} tasks_created={res.tasks_created} failures={res.failures} "
            f"reviews_enqueued={res.reviews_enqueued} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("build-digest")
def build_digest(
    out: Path | None = typer.Option(None, "--out", help="Optional path to write digest markdown."),
    compact: bool = typer.Option(False, "--compact", help="Shorter digest lists."),
    include_informational: bool = typer.Option(
        False,
        "--include-informational",
        help="Include informational-only action center bucket when present.",
    ),
    json_out: Path | None = typer.Option(
        None,
        "--json-out",
        help="Write run_id, digest_id, markdown JSON to this path (stdout may omit markdown when set).",
    ),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    run_id = new_run_id()
    try:
        res = w.digest_uc.execute(
            run_id=run_id,
            pipeline_run_db_id=None,
            pipeline_stats=None,
            compact=compact,
            include_informational=include_informational,
        )
        if json_out is not None:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(
                json.dumps({"run_id": run_id, "digest_id": res.digest_id, "markdown": res.markdown}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(res.markdown, encoding="utf-8")
        if json_out is None and out is None:
            typer.echo(res.markdown)
        else:
            if out is not None:
                typer.echo(f"wrote digest markdown: {out.resolve()}")
            if json_out is not None:
                typer.echo(f"wrote digest json: {json_out.resolve()}")
    finally:
        w.llm.close()
        conn.close()


@app.command("action-center")
def action_center_cmd(
    compact: bool = typer.Option(False, "--compact", help="Fewer lines per category."),
    as_json: bool = typer.Option(False, "--json", help="Emit snapshot JSON."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        snap, _bundle = _load_action_center_snapshot(settings, conn, clock)
        if as_json:
            typer.echo(json.dumps(snap.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return
        typer.echo(f"Action center (items capped by ACTION_CENTER_MAX_ITEMS={settings.action_center_max_items})")
        cap = 5 if compact else 12
        for sec in snap.category_sections:
            typer.echo("")
            typer.echo(f"## {sec.category.value}")
            for it in sec.items[:cap]:
                rs = f" reply={it.reply_state.value}" if it.reply_state else ""
                typer.echo(f"- {it.item_id} score={it.priority_score}{rs}")
                typer.echo(f"    {it.title}")
                typer.echo(f"    why: {it.reason}")
                typer.echo(f"    next: {it.recommended_next_step}")
            if len(sec.items) > cap:
                typer.echo(f"  … {len(sec.items) - cap} more")
    finally:
        w.llm.close()
        conn.close()


@app.command("action-center-export")
def action_center_export_cmd(
    out: Path = typer.Option(..., "--out", help="Path to write action center markdown."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        snap, bundle = _load_action_center_snapshot(settings, conn, clock)
        ctx = DailyDigestContextDTO(
            window_start=bundle.window_start,
            window_end=bundle.window_end,
            stats=DailyDigestStatsDTO(
                messages_in_window=0,
                messages_capped=0,
                pending_reviews=0,
                candidate_tasks=0,
            ),
            messages=(),
            candidate_tasks=(),
            pending_reviews=(),
            action_center=snap,
        )
        text = compose_action_center_markdown_export(ctx=ctx)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out.resolve()}")
    finally:
        w.llm.close()
        conn.close()


@app.command("explain-message")
def explain_message_cmd(
    message_id: int = typer.Option(..., "--message-id", min=1),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        msg = w.messages.get_message_by_id(message_id)
        if msg is None:
            typer.echo(f"No message m{message_id}", err=True)
            raise typer.Exit(code=1)
        triage = w.triage_repo.get_triage(message_id)
        snap, _b = _load_action_center_snapshot(settings, conn, clock)
        summary = find_thread_summary_for_message(snap, message_id)
        lite = snapshot_lite_from_summary(summary) if summary is not None else None
        for line in explain_message_lines(message_id=message_id, triage=triage, snapshot=lite):
            typer.echo(line)
    finally:
        w.llm.close()
        conn.close()


@app.command("explain-thread")
def explain_thread_cmd(
    thread_id: str = typer.Option(..., "--thread-id", help="Thread id from action-center / digest (e.g. t-hint:... or t-heur-...)."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        snap, _b = _load_action_center_snapshot(settings, conn, clock)
        summary = next((t for t in snap.threads if t.thread_id == thread_id), None)
        if summary is None:
            typer.echo(f"Thread not in current action-center window: {thread_id!r}", err=True)
            raise typer.Exit(code=1)
        for line in explain_thread_lines(summary=summary):
            typer.echo(line)
    finally:
        w.llm.close()
        conn.close()


@app.command("explain-action-item")
def explain_action_item_cmd(
    item_id: str = typer.Option(..., "--item-id", help="Item id from action-center (e.g. ac:thread:...)."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        snap, _b = _load_action_center_snapshot(settings, conn, clock)
        item = find_action_item(snap, item_id)
        if item is None:
            typer.echo(f"Action item not found in current window: {item_id!r}", err=True)
            raise typer.Exit(code=1)
        for line in explain_action_item_lines(item=item):
            typer.echo(line)
    finally:
        w.llm.close()
        conn.close()


@app.command("review-list")
def review_list(limit: int = typer.Option(50, "--limit", min=1, max=500)) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        items = w.list_reviews_uc.execute(limit=limit)
        if not items:
            typer.echo("No pending reviews.")
            return
        for it in items:
            typer.echo(
                f"r{it.id}\t{it.review_kind.value}\tm{it.related_message_id}"
                + (f"\tt{it.related_task_id}" if it.related_task_id is not None else "")
                + f"\tconf={it.confidence:.2f}\t{it.reason_code}\t{it.reason_text}"
            )
    finally:
        w.llm.close()
        conn.close()


@app.command("review-approve")
def review_approve(
    review_id: int = typer.Option(..., "--review-id"),
    note: str | None = typer.Option(None, "--note"),
    decided_by: str = typer.Option("manual_cli", "--decided-by"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        w.approve_review_uc.execute(review_id=review_id, decided_by=decided_by, note=note)
        typer.echo(f"approved review r{review_id}")
    finally:
        w.llm.close()
        conn.close()


@app.command("review-reject")
def review_reject(
    review_id: int = typer.Option(..., "--review-id"),
    note: str | None = typer.Option(None, "--note"),
    decided_by: str = typer.Option("manual_cli", "--decided-by"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        w.reject_review_uc.execute(review_id=review_id, decided_by=decided_by, note=note)
        typer.echo(f"rejected review r{review_id}")
    finally:
        w.llm.close()
        conn.close()


@app.command("review-export")
def review_export(
    out: Path = typer.Option(..., "--out", help="Write pending reviews as JSON to this path."),
    limit: int = typer.Option(200, "--limit", min=1, max=2000),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        items = w.list_reviews_uc.execute(limit=limit)
        payload = [it.model_dump(mode="json") for it in items]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"wrote {len(payload)} pending reviews to {out.resolve()}")
    finally:
        w.llm.close()
        conn.close()


@app.command("prepare-maildrop")
def prepare_maildrop_cmd(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        help="Maildrop root directory (creates incoming/processed/failed/exported). Defaults to MAILDROP_ROOT.",
    ),
) -> None:
    settings = AppSettings()
    root = path.resolve() if path is not None else settings.maildrop_root.resolve()
    logger = StructuredLoggerAdapter()
    uc = PrepareMaildropUseCase(fs=OsMaildropFilesystem(logger))
    typer.echo(uc.execute(root))


@app.command("ingest-apple-mail-drop")
def ingest_apple_mail_drop_cmd(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        help="Maildrop root (reads incoming/*.json). Defaults to MAILDROP_ROOT from settings.",
    ),
) -> None:
    settings = AppSettings()
    root = path.resolve() if path is not None else settings.maildrop_root.resolve()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    uc = build_process_apple_mail_drop_use_case(conn, clock, logger)
    run_id = new_run_id()
    try:
        res = uc.execute(maildrop_root=root, run_id=run_id)
        typer.echo(
            "ingest-apple-mail-drop done: "
            f"found={res.found} ingested={res.ingested} duplicate={res.duplicate} failed={res.failed} "
            f"moved_processed={res.moved_processed} moved_failed={res.moved_failed} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("doctor")
def doctor_cmd(
    repo_root: Optional[Path] = typer.Option(
        None,
        "--repo-root",
        help="Repository root for relative checks (defaults to current working directory).",
    ),
    wrapper: Optional[Path] = typer.Option(
        None,
        "--wrapper",
        help=(
            "Optional path to launchd / scheduled wrapper (defaults to "
            "<repo>/scripts/macos/run-mail-assistant-daily.sh; same script as run-mail-assistant.command)."
        ),
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON with structured doctor lines."),
    yougile_probe: bool = typer.Option(
        False,
        "--yougile-probe",
        help="Append live YouGile API checks when YOUGILE_API_KEY is set.",
    ),
) -> None:
    settings = AppSettings()
    rr = repo_root.resolve() if repo_root is not None else Path.cwd().resolve()
    wr = wrapper.resolve() if wrapper is not None else (rr / "scripts" / "macos" / "run-mail-assistant-daily.sh")
    log = StructuredLoggerAdapter()
    uc = DoctorEnvironmentUseCase(http=UrllibHttpProbe())
    report = uc.execute(settings, repo_root=rr, wrapper_script=wr, kanban_port=make_kanban_port(settings, log))
    lines = list(report.lines)
    try:
        export_dir = settings.reply_draft_export_dir.resolve()
        export_dir.mkdir(parents=True, exist_ok=True)
        probe = export_dir / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        lines.append(DoctorLineDTO("OK", f"REPLY_DRAFT_EXPORT_DIR writable: {export_dir}"))
    except OSError as exc:
        lines.append(DoctorLineDTO("WARN", f"REPLY_DRAFT_EXPORT_DIR not ready ({settings.reply_draft_export_dir}): {exc}"))
    lines.append(
        DoctorLineDTO(
            "OK",
            "Reply draft LLM: uses same LM Studio gateway as triage/tasks (on-demand `reply-draft-generate` only).",
        )
    )
    lines.append(
        DoctorLineDTO(
            "OK",
            f"REPLY_DRAFT_REQUIRE_APPROVAL_BEFORE_EXPORT={'on' if settings.reply_draft_require_approval_before_export else 'off'}",
        )
    )
    lines.append(DoctorLineDTO("OK", f"REPLY_DRAFT_MARK_STALE_ON_THREAD_CHANGE={'on' if settings.reply_draft_mark_stale_on_thread_change else 'off'}"))
    lines.append(DoctorLineDTO("OK", f"REPLY_DRAFT_DEFAULT_TONE={settings.reply_draft_default_tone!r}"))
    if settings.database_path.exists():
        conn = open_connection(settings.database_path)
        try:
            dr = SqliteReplyDraftRepository(conn, SystemClock())
            counts = dr.count_by_status()
            ready = int(counts.get("generated", 0))
            stale = int(counts.get("stale", 0))
            appr = int(counts.get("approved", 0))
            lines.append(
                DoctorLineDTO(
                    "OK",
                    f"Reply draft DB snapshot: generated={ready} stale={stale} approved={appr} (full map: {counts!r})",
                )
            )
        finally:
            conn.close()
    if yougile_probe and settings.yougile_api_key.strip():
        lines.extend(run_yougile_deep_doctor(settings, log))
    merged = DoctorReportDTO(lines=tuple(lines))
    typer.echo(merged.render_json() if as_json else merged.render_text())


@app.command("kanban-preview")
def kanban_preview_cmd(
    provider: Optional[str] = typer.Option(None, "--provider", help="Override KANBAN_PROVIDER."),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    tasks = SqliteTaskRepository(conn, clock)
    kb = build_kanban_wiring(conn, clock, logger, settings, tasks)
    prov = _parse_kanban_provider(provider, settings.kanban_provider)
    res = kb.preview.execute(provider=prov, limit=limit)
    typer.echo(
        f"provider={res.provider.value} approved_ready={res.approved_ready} "
        f"skip_same_fingerprint={res.would_skip_already_synced} "
        f"planned_create={res.planned_creates} planned_update={res.planned_updates} "
        f"skip_manual_resync={res.planned_skip_manual_resync} "
        f"would_write={res.would_sync_or_retry} sample_task_ids={list(res.sample_task_ids)}"
    )
    conn.close()


@app.command("kanban-sync")
def kanban_sync_cmd(
    provider: Optional[str] = typer.Option(None, "--provider", help="Override KANBAN_PROVIDER."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, max=500),
    only_task_id: Optional[int] = typer.Option(None, "--only-task-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    include_resync: bool = typer.Option(True, "--include-resync/--no-include-resync", help="Allow UPDATE_EXISTING plans."),
    changed_only: bool = typer.Option(False, "--changed-only", help="Only tasks whose fingerprint differs from last record."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    tasks = SqliteTaskRepository(conn, clock)
    kb = build_kanban_wiring(conn, clock, logger, settings, tasks)
    prov = _parse_kanban_provider(provider, settings.kanban_provider)
    res = kb.sync.execute(
        run_id=new_run_id(),
        provider=prov,
        dry_run=dry_run,
        limit=limit,
        only_task_id=only_task_id,
        include_resync=include_resync,
        changed_only=changed_only,
    )
    typer.echo(
        f"kanban-sync done: found={res.found} synced={res.synced} updated={res.updated} "
        f"skipped={res.skipped} failed={res.failed} dry_run={res.dry_run} dry_run_planned={res.dry_run_planned} "
        f"skip_provider_config={res.skip_provider_config} fail_precondition={res.fail_precondition} "
        f"skip_manual_resync={res.skip_manual_resync} run_id={res.run_id}"
    )
    conn.close()


@app.command("kanban-retry-failed")
def kanban_retry_failed_cmd(
    provider: Optional[str] = typer.Option(None, "--provider", help="Override KANBAN_PROVIDER."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, max=500),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    tasks = SqliteTaskRepository(conn, clock)
    kb = build_kanban_wiring(conn, clock, logger, settings, tasks)
    prov = _parse_kanban_provider(provider, settings.kanban_provider)
    res = kb.retry.execute(run_id=new_run_id(), provider=prov, limit=limit)
    typer.echo(
        f"kanban-retry-failed done: attempted={res.attempted} synced={res.synced} updated={res.updated} "
        f"skipped={res.skipped} failed={res.failed} run_id={res.run_id}"
    )
    conn.close()


@app.command("kanban-resync-changed")
def kanban_resync_changed_cmd(
    provider: Optional[str] = typer.Option(None, "--provider", help="Override KANBAN_PROVIDER."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, max=500),
    only_task_id: Optional[int] = typer.Option(None, "--only-task-id"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    tasks = SqliteTaskRepository(conn, clock)
    kb = build_kanban_wiring(conn, clock, logger, settings, tasks)
    prov = _parse_kanban_provider(provider, settings.kanban_provider)
    res = kb.resync_changed.execute(
        run_id=new_run_id(),
        provider=prov,
        dry_run=dry_run,
        limit=limit,
        only_task_id=only_task_id,
    )
    typer.echo(
        f"kanban-resync-changed done: found={res.found} updated={res.updated} skipped={res.skipped} "
        f"failed={res.failed} dry_run={res.dry_run} dry_run_planned={res.dry_run_planned} "
        f"skip_manual_resync={res.skip_manual_resync} run_id={res.run_id}"
    )
    conn.close()


@app.command("kanban-show-task-sync")
def kanban_show_task_sync_cmd(
    task_id: int = typer.Option(..., "--task-id", min=1),
    provider: Optional[str] = typer.Option(None, "--provider", help="Override KANBAN_PROVIDER."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON inspection."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    tasks = SqliteTaskRepository(conn, clock)
    kb = build_kanban_wiring(conn, clock, StructuredLoggerAdapter(), settings, tasks)
    prov = _parse_kanban_provider(provider, settings.kanban_provider)
    dto = kb.show_task_sync.execute(task_id=task_id, provider=prov)
    if as_json:
        typer.echo(json.dumps(dto.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"task_id={dto.task_id} provider={dto.provider.value} local_status={dto.local_task_status}")
        typer.echo(
            f"  sync_status={dto.sync_status} planned_action={dto.planned_outbound_action} reason={dto.planned_reason_code}"
        )
        typer.echo(f"  fingerprint(record)={dto.card_fingerprint} draft_fingerprint={dto.current_draft_fingerprint}")
        typer.echo(f"  external_id={dto.external_card_id} url={dto.external_card_url}")
        typer.echo(
            f"  last_action={dto.last_outbound_action} note={dto.last_operation_note} retries={dto.retry_count} last_error={dto.last_error}"
        )
        typer.echo(f"  update_possible={dto.update_existing_possible} manual_resync_required={dto.manual_resync_required}")
    conn.close()


@app.command("kanban-status")
def kanban_status_cmd(
    provider: Optional[str] = typer.Option(None, "--provider", help="Override KANBAN_PROVIDER."),
    probe: bool = typer.Option(
        False,
        "--probe",
        help="For YouGile: run a few live GET checks (boards/column) when API key is set.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON summary."),
    with_work_hints: bool = typer.Option(
        False,
        "--with-work-hints",
        help="Run a local action-center snapshot and print reply/critical counts (extra SQLite work).",
    ),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    tasks = SqliteTaskRepository(conn, clock)
    kb = build_kanban_wiring(conn, clock, logger, settings, tasks)
    prov = _parse_kanban_provider(provider, settings.kanban_provider)
    st = kb.status.execute(provider=prov)
    if as_json:

        def _json_default(o: object) -> object:
            if isinstance(o, Enum):
                return o.value
            raise TypeError(type(o))

        typer.echo(json.dumps(dataclasses.asdict(st), default=_json_default, ensure_ascii=False, indent=2))
    else:
        typer.echo(
            f"provider={st.provider.value} pending={st.pending} synced={st.synced} failed={st.failed} skipped={st.skipped}"
        )
        typer.echo(
            f"  manual_resync_pending={st.manual_resync_pending} outbound_updates_24h={st.outbound_updates_last_24h} "
            f"last_actions={list(st.last_outbound_actions)}"
        )
        if prov == KanbanProvider.YOUGILE:
            typer.echo(
                f"  yougile_update_existing={st.yougile_update_existing_enabled} "
                f"done_col={st.yougile_done_column_configured} blocked_col={st.yougile_blocked_column_configured}"
            )
        if st.provider_readiness:
            typer.echo(f"  readiness: {st.provider_readiness}")
        if st.next_step_hint:
            typer.echo(f"  next: {st.next_step_hint}")
        if probe and prov == KanbanProvider.YOUGILE:
            for line in run_yougile_live_status_probe(settings, logger):
                typer.echo(f"  {line}")
        elif probe and prov != KanbanProvider.YOUGILE:
            typer.echo("  probe: skipped (only meaningful for KANBAN_PROVIDER=yougile)")
        for err in st.last_errors:
            typer.echo(f"  err: {err[:300]}")
        if with_work_hints:
            snap, _b = _load_action_center_snapshot(settings, conn, clock)
            crit = count_reply_critical_items(snap)
            typer.echo(
                f"  work_hints: action_center_items={len(snap.items)} reply_or_critical_bucket≈{crit} "
                f"(see `action-center` / README; window={settings.action_center_lookback_hours}h)"
            )
    conn.close()


@app.command("kanban-export-local")
def kanban_export_local_cmd() -> None:
    settings = AppSettings()
    if settings.kanban_provider != KanbanProvider.LOCAL_FILE:
        typer.echo(f"WARN: KANBAN_PROVIDER is {settings.kanban_provider.value}; export still writes under KANBAN_ROOT_DIR.")
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    tasks = SqliteTaskRepository(conn, clock)
    kb = build_kanban_wiring(conn, clock, logger, settings, tasks)
    path = kb.export.execute()
    typer.echo(f"wrote {path}")
    conn.close()


def _yougile_smoke_use_case(conn, clock, logger: StructuredLoggerAdapter, settings: AppSettings) -> YougileSmokeSyncUseCase:
    tasks = SqliteTaskRepository(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    sync_uc = SyncApprovedTasksToKanbanUseCase(
        tasks=tasks,
        sync=sync_repo,
        kanban=make_kanban_port(settings, logger),
        logger=logger,
        settings=settings,
    )
    return YougileSmokeSyncUseCase(tasks=tasks, sync=sync_repo, sync_uc=sync_uc, settings=settings)


@app.command("yougile-discover")
def yougile_discover_cmd(
    as_json: bool = typer.Option(False, "--json", help="Print discovery as JSON."),
    compact: bool = typer.Option(False, "--compact", help="One-line board/column rows."),
    force: bool = typer.Option(False, "--force", help="Run even if KANBAN_PROVIDER is not yougile."),
) -> None:
    settings = AppSettings()
    if settings.kanban_provider != KanbanProvider.YOUGILE and not force:
        typer.echo(
            "WARN: KANBAN_PROVIDER is not yougile; discovery still runs using YOUGILE_* env. "
            "Use --force to silence this hint.",
            err=True,
        )
    log = StructuredLoggerAdapter()
    uc = YougileDiscoverWorkspaceUseCase(settings=settings, logger=log)
    dto = uc.execute()
    if as_json:
        typer.echo(json.dumps(dto.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(render_yougile_discovery_text(dto, compact=compact, base_url_for_env=str(settings.yougile_base_url)))


@app.command("yougile-print-env")
def yougile_print_env_cmd(
    board_id: Optional[str] = typer.Option(None, "--board-id", help="Override YOUGILE_BOARD_ID for the template."),
    column_todo: Optional[str] = typer.Option(None, "--column-todo", help="Override YOUGILE_COLUMN_ID_TODO for the template."),
) -> None:
    settings = AppSettings()
    typer.echo(build_yougile_env_fragment(settings, board_id=board_id, column_todo=column_todo))


@app.command("yougile-doctor")
def yougile_doctor_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit JSON lines array."),
) -> None:
    settings = AppSettings()
    log = StructuredLoggerAdapter()
    lines = run_yougile_deep_doctor(settings, log)
    if as_json:
        typer.echo(json.dumps({"lines": [{"level": l.level, "message": l.message} for l in lines]}, ensure_ascii=False, indent=2))
    else:
        typer.echo("YouGile operational doctor")
        typer.echo("")
        for line in lines:
            typer.echo(f"[{line.level}] {line.message}")


@app.command("yougile-config-check")
def yougile_config_check_cmd() -> None:
    settings = AppSettings()
    typer.echo("--- Suggested .env fragment (fill secrets / ids) ---\n")
    typer.echo(build_yougile_env_fragment(settings, board_id=None, column_todo=None))
    typer.echo("--- Live API checks ---\n")
    log = StructuredLoggerAdapter()
    for line in run_yougile_deep_doctor(settings, log):
        typer.echo(f"[{line.level}] {line.message}")


@app.command("yougile-smoke-sync")
def yougile_smoke_sync_cmd(
    task_id: int = typer.Option(..., "--task-id", help="Internal extracted_tasks.id (must be approved)."),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Perform one real sync; default is dry-run preview only.",
    ),
) -> None:
    settings = AppSettings()
    if settings.kanban_provider != KanbanProvider.YOUGILE:
        typer.echo("FAIL: KANBAN_PROVIDER must be yougile for smoke sync.", err=True)
        raise typer.Exit(code=1)
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    try:
        uc = _yougile_smoke_use_case(conn, clock, logger, settings)
        run_id = new_run_id()
        res = uc.execute(task_id=task_id, dry_run=not execute, run_id=run_id)
        typer.echo(f"task_id={res.task_id} approved={res.task_approved} dry_run={res.dry_run} plan={res.plan}")
        typer.echo(res.message)
        if res.external_task_id:
            typer.echo(f"external_task_id={res.external_task_id}")
        if res.external_url:
            typer.echo(f"external_url={res.external_url}")
        if not res.task_approved:
            raise typer.Exit(code=1)
        if res.failed and not res.dry_run:
            raise typer.Exit(code=1)
    finally:
        conn.close()


@app.command("yougile-cleanup-note")
def yougile_cleanup_note_cmd() -> None:
    typer.echo(yougile_cleanup_note_text())


def _default_launchd_log_paths(repo_root: Path) -> tuple[Path, Path]:
    if sys.platform == "darwin":
        base = Path.home() / "Library/Logs/mail-assistant"
        return base / "stdout.log", base / "stderr.log"
    return repo_root / "data" / "logs" / "launchd-stdout.log", repo_root / "data" / "logs" / "launchd-stderr.log"


@app.command("print-launchd")
def print_launchd_cmd(
    repo_root: Optional[Path] = typer.Option(None, "--repo-root", help="Checkout root (absolute paths in plist)."),
    wrapper: Optional[Path] = typer.Option(None, "--wrapper", help="Wrapper script path."),
    digest_out: Optional[Path] = typer.Option(None, "--digest-out", help="Digest output path for run-daily."),
    hour: int = typer.Option(7, "--hour", min=0, max=23),
    minute: int = typer.Option(0, "--minute", min=0, max=59),
) -> None:
    settings = AppSettings()
    rr = repo_root.resolve() if repo_root is not None else Path.cwd().resolve()
    wr = wrapper.resolve() if wrapper is not None else (rr / "scripts" / "macos" / "run-mail-assistant-daily.sh")
    digest = digest_out.resolve() if digest_out is not None else (rr / "data" / "digest.md")
    out_log, err_log = _default_launchd_log_paths(rr)
    run_log = rr / "data" / "logs" / "launchd-daily.log"
    spec = LaunchdPlistSpecDTO(
        label=settings.launchd_label,
        wrapper_script=wr,
        working_directory=rr,
        digest_out=digest,
        stdout_path=out_log,
        stderr_path=err_log,
        hour=hour,
        minute=minute,
        maildrop_root=settings.maildrop_root.resolve(),
        run_log_path=run_log,
    )
    typer.echo(render_launchd_plist_xml(spec))


@app.command("install-launchd")
def install_launchd_cmd(
    output: Path = typer.Option(..., "--output", help="Where to write the LaunchAgent plist."),
    repo_root: Optional[Path] = typer.Option(None, "--repo-root", help="Checkout root (absolute paths in plist)."),
    wrapper: Optional[Path] = typer.Option(None, "--wrapper", help="Wrapper script path."),
    digest_out: Optional[Path] = typer.Option(None, "--digest-out", help="Digest output path for run-daily."),
    hour: int = typer.Option(7, "--hour", min=0, max=23),
    minute: int = typer.Option(0, "--minute", min=0, max=59),
) -> None:
    settings = AppSettings()
    rr = repo_root.resolve() if repo_root is not None else Path.cwd().resolve()
    wr = wrapper.resolve() if wrapper is not None else (rr / "scripts" / "macos" / "run-mail-assistant-daily.sh")
    digest = digest_out.resolve() if digest_out is not None else (rr / "data" / "digest.md")
    out_log, err_log = _default_launchd_log_paths(rr)
    run_log = rr / "data" / "logs" / "launchd-daily.log"
    spec = LaunchdPlistSpecDTO(
        label=settings.launchd_label,
        wrapper_script=wr,
        working_directory=rr,
        digest_out=digest,
        stdout_path=out_log,
        stderr_path=err_log,
        hour=hour,
        minute=minute,
        maildrop_root=settings.maildrop_root.resolve(),
        run_log_path=run_log,
    )
    xml = render_launchd_plist_xml(spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(xml, encoding="utf-8")
    typer.echo(f"Wrote plist to {output.resolve()}")
    if sys.platform == "darwin":
        typer.echo("Next (user LaunchAgent):")
        typer.echo("  mkdir -p ~/Library/LaunchAgents")
        typer.echo(f"  cp {output.resolve()} ~/Library/LaunchAgents/{settings.launchd_label}.plist")
        typer.echo(
            f"  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/{settings.launchd_label}.plist 2>/dev/null || true"
        )
        typer.echo(f"  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{settings.launchd_label}.plist")
    else:
        typer.echo("Note: launchctl steps above apply to macOS only.")


def _default_reply_tone(settings: AppSettings) -> ReplyTone:
    try:
        return ReplyTone(str(settings.reply_draft_default_tone))
    except ValueError:
        return ReplyTone.NEUTRAL


@app.command("reply-draft-generate")
def reply_draft_generate_cmd(
    thread_id: str = typer.Option(..., "--thread-id", help="Thread id from action-center (e.g. t-hint-…)."),
    tone: Optional[str] = typer.Option(None, "--tone", help="neutral|warm|concise|formal|direct"),
    force: bool = typer.Option(False, "--force", help="Override conservative reply_state blocks."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        bundle = _reply_draft_bundle(settings, conn, clock)
        uc = _make_reply_draft_generate_uc(conn, clock, logger, settings, w.llm)
        tone_e = _parse_reply_tone(tone, _default_reply_tone(settings))
        res = uc.execute(
            run_id=new_run_id(),
            thread_id=thread_id,
            bundle=bundle,
            tone=tone_e,
            force=force,
            explicit_regenerate=False,
        )
        typer.echo(
            f"reply-draft-generate: draft_id={res.draft_id} reused_without_llm={res.reused_without_llm} "
            f"fingerprint={res.generation_fingerprint[:16]}… mode={res.generation_mode.value} "
            f"subject={res.subject_suggestion[:120]!r}"
        )
    except ReplyDraftError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        w.llm.close()
        conn.close()


@app.command("reply-draft-list")
def reply_draft_list_cmd(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by reply draft status."),
    thread_id: Optional[str] = typer.Option(None, "--thread-id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    repo = SqliteReplyDraftRepository(conn, clock)
    rows = repo.list_reply_drafts(status=status, thread_id=thread_id, limit=200)
    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(r) for r in rows], default=str, ensure_ascii=False, indent=2))
        conn.close()
        return
    if not rows:
        typer.echo("(no reply drafts)")
    for r in rows:
        typer.echo(f"d{r.id} thread={r.thread_id} status={r.status.value} fp={r.generation_fingerprint[:12]}… subject={r.subject_suggestion[:80]!r}")
    conn.close()


@app.command("reply-draft-show")
def reply_draft_show_cmd(
    draft_id: int = typer.Option(..., "--draft-id", min=1),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    repo = SqliteReplyDraftRepository(conn, clock)
    d = repo.get_reply_draft(draft_id)
    if d is None:
        typer.echo(f"draft {draft_id} not found", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Subject: {d.subject_suggestion}")
    typer.echo(f"Status: {d.status.value} tone={d.tone.value} mode={d.generation_mode.value}")
    typer.echo(f"Stale vs thread: compare fingerprint {d.generation_fingerprint[:16]}… to current context (reply-draft-explain).")
    typer.echo("")
    typer.echo("Body:")
    typer.echo(d.body_text)
    typer.echo("")
    typer.echo("Rationale:")
    typer.echo(d.short_rationale)
    typer.echo("")
    typer.echo("Missing information:")
    for x in d.missing_information:
        typer.echo(f"- {x}")
    if not d.missing_information:
        typer.echo("- (none)")
    conn.close()


@app.command("reply-draft-approve")
def reply_draft_approve_cmd(
    draft_id: int = typer.Option(..., "--draft-id", min=1),
    note: Optional[str] = typer.Option(None, "--note"),
    decided_by: str = typer.Option("cli", "--by"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    repo = SqliteReplyDraftRepository(conn, clock)
    uc = ApproveReplyDraftUseCase(drafts=repo, clock=clock)
    try:
        uc.execute(draft_id, decided_by=decided_by, note=note)
        typer.echo(f"approved d{draft_id}")
    except ReplyDraftError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@app.command("reply-draft-reject")
def reply_draft_reject_cmd(
    draft_id: int = typer.Option(..., "--draft-id", min=1),
    note: Optional[str] = typer.Option(None, "--note"),
    decided_by: str = typer.Option("cli", "--by"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    repo = SqliteReplyDraftRepository(conn, clock)
    uc = RejectReplyDraftUseCase(drafts=repo, clock=clock)
    try:
        uc.execute(draft_id, decided_by=decided_by, note=note)
        typer.echo(f"rejected d{draft_id}")
    except ReplyDraftError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@app.command("reply-draft-regenerate")
def reply_draft_regenerate_cmd(
    draft_id: int = typer.Option(..., "--draft-id", min=1),
    tone: Optional[str] = typer.Option(None, "--tone"),
    force: bool = typer.Option(False, "--force", help="Required to regenerate approved/exported drafts."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        repo = SqliteReplyDraftRepository(conn, clock)
        d = repo.get_reply_draft(draft_id)
        if d is None:
            typer.echo(f"draft {draft_id} not found", err=True)
            raise typer.Exit(code=1)
        assert_regenerate_preconditions(d, force=force)
        repo.mark_reply_draft_stale(draft_id, now_iso=clock.now().isoformat())
        bundle = _reply_draft_bundle(settings, conn, clock)
        uc = _make_reply_draft_generate_uc(conn, clock, logger, settings, w.llm)
        tone_e = _parse_reply_tone(tone, d.tone)
        res = uc.execute(
            run_id=new_run_id(),
            thread_id=d.thread_id,
            bundle=bundle,
            tone=tone_e,
            force=True,
            explicit_regenerate=True,
        )
        typer.echo(
            f"reply-draft-regenerate: new_draft_id={res.draft_id} reused_without_llm={res.reused_without_llm} "
            f"subject={res.subject_suggestion[:120]!r}"
        )
    except ReplyDraftError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        w.llm.close()
        conn.close()


@app.command("reply-draft-export")
def reply_draft_export_cmd(
    draft_id: int = typer.Option(..., "--draft-id", min=1),
    out: Optional[Path] = typer.Option(None, "--out", help="Output file path."),
    as_markdown: bool = typer.Option(True, "--markdown/--plain", help="Export markdown (default) or plain text."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    repo = SqliteReplyDraftRepository(conn, clock)
    exporter = LocalReplyDraftExporter()
    uc = ExportReplyDraftUseCase(drafts=repo, exporter=exporter, clock=clock, settings=settings)
    d = repo.get_reply_draft(draft_id)
    if d is None:
        typer.echo(f"draft {draft_id} not found", err=True)
        raise typer.Exit(code=1)
    suffix = "md" if as_markdown else "txt"
    path = out or default_export_path(export_dir=settings.reply_draft_export_dir, draft=d, suffix=suffix)
    try:
        written = uc.execute(draft_id, out_path=path, as_markdown=as_markdown)
        typer.echo(f"exported to {written.resolve()}")
    except ReplyDraftError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


@app.command("reply-draft-explain")
def reply_draft_explain_cmd(draft_id: int = typer.Option(..., "--draft-id", min=1)) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    repo = SqliteReplyDraftRepository(conn, clock)
    d = repo.get_reply_draft(draft_id)
    if d is None:
        typer.echo(f"draft {draft_id} not found", err=True)
        raise typer.Exit(code=1)
    messages = SqliteMessageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    triage = SqliteTriageRepository(conn, clock)
    policy = LlmTextPolicy(
        max_input_chars=int(settings.llm_max_input_chars),
        truncate_strategy=settings.message_body_truncate_strategy,
    )
    builder = SqliteReplyContextBuilder(
        messages=messages,
        tasks=tasks,
        reviews=reviews,
        triage_get=triage.get_triage,
        settings=settings,
        llm_text_policy=policy,
    )
    bundle = _reply_draft_bundle(settings, conn, clock)
    end = clock.now()
    rs = infer_reply_state_for_thread(bundle, settings=settings, now=end, thread_id=d.thread_id)
    step = None
    snap, _ = _load_action_center_snapshot(settings, conn, clock)
    for it in snap.items:
        if it.thread_id == d.thread_id and it.source_type == "thread":
            step = it.recommended_next_step
            break
    try:
        mids = resolve_thread_message_ids(bundle, settings=settings, now=end, thread_id=d.thread_id)
    except ReplyDraftError:
        mids = d.source_message_ids
    ctx = builder.build_for_thread(
        thread_id=d.thread_id,
        message_ids=mids,
        primary_message_id=d.primary_message_id,
        reply_state=rs,
        action_center_next_step=step,
    )
    for line in explain_reply_draft_lines(draft=d, context=ctx):
        typer.echo(line)
    conn.close()


@app.command("run-daily")
def run_daily_cmd(
    digest_out: Path | None = typer.Option(
        None,
        "--digest-out",
        help="Optional path to write digest markdown (full digest is not printed to stdout).",
    ),
) -> None:
    settings = AppSettings()
    result = run_daily(settings=settings, digest_output=digest_out)
    typer.echo(result.stdout_summary)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

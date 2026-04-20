from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.application.dtos import (
    KanbanPreviewSummaryDTO,
    KanbanRetryBatchResultDTO,
    KanbanStatusSummaryDTO,
    KanbanSyncBatchResultDTO,
)
from app.application.kanban_mapping import KanbanMappingOptions, build_kanban_card_draft
from app.application.ports import KanbanPort, KanbanSyncRepositoryPort, LoggerPort, TaskRepositoryPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider, TaskStatus
from app.domain.models import KanbanCardDraft


def _draft_payload_json(draft: KanbanCardDraft) -> str:
    d = asdict(draft)
    d["priority"] = draft.priority.value
    d["card_status"] = draft.card_status.value
    return json.dumps(d, ensure_ascii=False)


def mapping_options_from_settings(settings: AppSettings) -> KanbanMappingOptions:
    return KanbanMappingOptions(
        max_title_chars=int(settings.kanban_max_title_chars),
        max_desc_chars=int(settings.kanban_max_desc_chars),
        include_review_metadata=bool(settings.kanban_include_review_metadata),
        include_message_metadata=bool(settings.kanban_include_message_metadata),
        default_card_status=settings.kanban_default_status,
    )


@dataclass(frozen=True, slots=True)
class PreviewKanbanSyncCandidatesUseCase:
    tasks: TaskRepositoryPort
    sync: KanbanSyncRepositoryPort
    logger: LoggerPort
    settings: AppSettings

    def execute(self, *, provider: KanbanProvider, limit: int) -> KanbanPreviewSummaryDTO:
        opts = mapping_options_from_settings(self.settings)
        contexts = list(self.tasks.list_approved_tasks_for_kanban(limit=limit))
        ready = 0
        skip = 0
        sync_n = 0
        sample: list[int] = []
        for ctx in contexts:
            if ctx.task.status != TaskStatus.APPROVED:
                continue
            ready += 1
            draft = build_kanban_card_draft(ctx, opts)
            if self.sync.maybe_skip_if_already_synced_same_fingerprint(
                task_id=ctx.task.id, provider=provider, fingerprint=draft.fingerprint
            ):
                skip += 1
                continue
            sync_n += 1
            if len(sample) < 10:
                sample.append(ctx.task.id)
        self.logger.info(
            "kanban.preview",
            provider=provider.value,
            approved_ready=ready,
            would_skip=skip,
            would_sync=sync_n,
        )
        return KanbanPreviewSummaryDTO(
            provider=provider,
            approved_ready=ready,
            would_skip_already_synced=skip,
            would_sync_or_retry=sync_n,
            sample_task_ids=tuple(sample),
        )


@dataclass(frozen=True, slots=True)
class SyncApprovedTasksToKanbanUseCase:
    tasks: TaskRepositoryPort
    sync: KanbanSyncRepositoryPort
    kanban: KanbanPort
    logger: LoggerPort
    settings: AppSettings

    def execute(
        self,
        *,
        run_id: str,
        provider: KanbanProvider,
        dry_run: bool = False,
        limit: int | None = None,
        only_task_id: int | None = None,
    ) -> KanbanSyncBatchResultDTO:
        started = time.perf_counter()
        lim = int(limit) if limit is not None else int(self.settings.kanban_sync_batch_size)
        opts = mapping_options_from_settings(self.settings)
        contexts = list(self.tasks.list_approved_tasks_for_kanban(limit=lim))

        found = 0
        synced = 0
        skipped = 0
        failed = 0
        dry_planned = 0

        if provider == KanbanProvider.STUB:
            self.logger.info("kanban.sync.stub_provider_noop", run_id=run_id)
            for ctx in contexts:
                if only_task_id is not None and ctx.task.id != only_task_id:
                    continue
                if ctx.task.status == TaskStatus.APPROVED:
                    found += 1
                    skipped += 1
            return KanbanSyncBatchResultDTO(
                run_id=run_id, found=found, synced=0, skipped=skipped, failed=0, dry_run=dry_run, dry_run_planned=0
            )

        for ctx in contexts:
            if only_task_id is not None and ctx.task.id != only_task_id:
                continue
            if ctx.task.status != TaskStatus.APPROVED:
                continue
            found += 1
            draft = build_kanban_card_draft(ctx, opts)
            if self.sync.maybe_skip_if_already_synced_same_fingerprint(
                task_id=ctx.task.id, provider=provider, fingerprint=draft.fingerprint
            ):
                skipped += 1
                continue
            if dry_run:
                dry_planned += 1
                continue

            rid = self.sync.upsert_pending_sync_record(
                task_id=ctx.task.id,
                provider=provider,
                fingerprint=draft.fingerprint,
                payload_json=_draft_payload_json(draft),
            )
            result = self.kanban.create_card(draft)
            if result.success:
                self.sync.mark_sync_success(
                    record_id=rid,
                    fingerprint=draft.fingerprint,
                    external_card_id=result.external_card_id,
                    external_card_url=result.external_card_url,
                )
                self.tasks.update_task_status(ctx.task.id, TaskStatus.SYNCED)
                synced += 1
                self.logger.info("kanban.sync.task_synced", run_id=run_id, task_id=ctx.task.id, record_id=rid)
            else:
                msg = result.error_message or "unknown_error"
                self.sync.mark_sync_failed(record_id=rid, error=msg)
                failed += 1
                self.logger.warning("kanban.sync.task_failed", run_id=run_id, task_id=ctx.task.id, error=msg)

        duration_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "kanban.sync.end",
            run_id=run_id,
            duration_ms=duration_ms,
            found=found,
            synced=synced,
            skipped=skipped,
            failed=failed,
            dry_run=dry_run,
        )
        return KanbanSyncBatchResultDTO(
            run_id=run_id,
            found=found,
            synced=synced,
            skipped=skipped,
            failed=failed,
            dry_run=dry_run,
            dry_run_planned=dry_planned,
        )


@dataclass(frozen=True, slots=True)
class RetryFailedKanbanSyncUseCase:
    tasks: TaskRepositoryPort
    sync: KanbanSyncRepositoryPort
    kanban: KanbanPort
    logger: LoggerPort
    settings: AppSettings

    def execute(self, *, run_id: str, provider: KanbanProvider, limit: int | None = None) -> KanbanRetryBatchResultDTO:
        lim = int(limit) if limit is not None else int(self.settings.kanban_sync_batch_size)
        max_retry = int(self.settings.kanban_retry_limit)
        opts = mapping_options_from_settings(self.settings)
        records = list(self.sync.list_failed_sync_records(provider, limit=lim, max_retry=max_retry))
        attempted = 0
        synced = 0
        failed = 0

        if provider == KanbanProvider.STUB:
            return KanbanRetryBatchResultDTO(run_id=run_id, attempted=0, synced=0, failed=0)

        for rec in records:
            ctx = self.tasks.get_task_kanban_context(rec.task_id)
            if ctx is None or ctx.task.status != TaskStatus.APPROVED:
                self.logger.warning("kanban.retry.skip_task", task_id=rec.task_id, reason="not_approved_or_missing")
                continue
            attempted += 1
            draft = build_kanban_card_draft(ctx, opts)
            rid = self.sync.upsert_pending_sync_record(
                task_id=rec.task_id,
                provider=provider,
                fingerprint=draft.fingerprint,
                payload_json=_draft_payload_json(draft),
            )
            result = self.kanban.create_card(draft)
            if result.success:
                self.sync.mark_sync_success(
                    record_id=rid,
                    fingerprint=draft.fingerprint,
                    external_card_id=result.external_card_id,
                    external_card_url=result.external_card_url,
                )
                self.tasks.update_task_status(rec.task_id, TaskStatus.SYNCED)
                synced += 1
            else:
                self.sync.mark_sync_failed(record_id=rid, error=result.error_message or "retry_failed")
                failed += 1

        self.logger.info("kanban.retry.end", run_id=run_id, attempted=attempted, synced=synced, failed=failed)
        return KanbanRetryBatchResultDTO(run_id=run_id, attempted=attempted, synced=synced, failed=failed)


@dataclass(frozen=True, slots=True)
class ListKanbanSyncStatusUseCase:
    sync: KanbanSyncRepositoryPort
    settings: AppSettings

    def execute(self, *, provider: KanbanProvider | None = None) -> KanbanStatusSummaryDTO:
        p = provider or self.settings.kanban_provider
        return self.sync.load_status_summary(p)


@dataclass(frozen=True, slots=True)
class ExportLocalKanbanBoardUseCase:
    settings: AppSettings
    logger: LoggerPort

    def execute(self) -> Path:
        root = self.settings.kanban_root_dir.resolve()
        cards = root / "cards"
        cards.mkdir(parents=True, exist_ok=True)
        out = root / "board.md"
        lines: list[str] = ["# Local Kanban board", "", f"Root: `{root}`", ""]
        paths = sorted(cards.glob("task_*.json"))
        if not paths:
            lines.append("_No cards yet._")
        else:
            lines.append("| Task id | Title | Priority | Due |")
            lines.append("|---:|---|---|---|")
            for p in paths:
                try:
                    doc = json.loads(p.read_text(encoding="utf-8"))
                    lines.append(
                        f"| {doc.get('internal_task_id', '')} | {doc.get('title', '')[:80]} | "
                        f"{doc.get('priority', '')} | {doc.get('due_at') or ''} |"
                    )
                except json.JSONDecodeError:
                    lines.append(f"| (broken) | `{p.name}` | | |")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.logger.info("kanban.export.local", path=str(out))
        return out

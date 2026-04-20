from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.application.dtos import (
    KanbanPreviewSummaryDTO,
    KanbanRetryBatchResultDTO,
    KanbanStatusSummaryDTO,
    KanbanSyncBatchResultDTO,
)
from app.application.kanban_mapping import KanbanMappingOptions, build_kanban_card_draft
from app.application.kanban_resync_policy import KanbanOutboundPlan, plan_kanban_outbound
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


def kanban_status_readiness_hint(settings: AppSettings, provider: KanbanProvider) -> str | None:
    if provider != KanbanProvider.YOUGILE:
        return None
    missing: list[str] = []
    if not settings.yougile_api_key.strip():
        missing.append("YOUGILE_API_KEY")
    if not settings.yougile_column_id_todo.strip():
        missing.append("YOUGILE_COLUMN_ID_TODO")
    if missing:
        return "YouGile: FAIL — " + ", ".join(missing)
    parts: list[str] = ["YouGile: required settings present"]
    if not settings.yougile_board_id.strip():
        parts.append("YOUGILE_BOARD_ID empty (adapter healthcheck will be weak)")
    if not settings.yougile_enable_update_existing:
        parts.append("in-place updates after fingerprint change disabled (YOUGILE_ENABLE_UPDATE_EXISTING=false)")
    return "; ".join(parts)


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
        skip_same = 0
        skip_manual = 0
        creates = 0
        updates = 0
        sample: list[int] = []
        for ctx in contexts:
            if ctx.task.status != TaskStatus.APPROVED:
                continue
            ready += 1
            draft = build_kanban_card_draft(ctx, opts)
            plan = plan_kanban_outbound(
                provider=provider,
                settings=self.settings,
                sync=self.sync,
                task_id=ctx.task.id,
                draft=draft,
            )
            if plan == KanbanOutboundPlan.SKIP_SAME_FINGERPRINT:
                skip_same += 1
            elif plan == KanbanOutboundPlan.SKIP_MANUAL_RESYNC:
                skip_manual += 1
            elif plan == KanbanOutboundPlan.UPDATE_EXISTING:
                updates += 1
                if len(sample) < 10:
                    sample.append(ctx.task.id)
            else:
                creates += 1
                if len(sample) < 10:
                    sample.append(ctx.task.id)
        self.logger.info(
            "kanban.preview",
            provider=provider.value,
            approved_ready=ready,
            skip_same_fp=skip_same,
            skip_manual=skip_manual,
            planned_creates=creates,
            planned_updates=updates,
        )
        return KanbanPreviewSummaryDTO(
            provider=provider,
            approved_ready=ready,
            would_skip_already_synced=skip_same,
            would_sync_or_retry=creates + updates,
            sample_task_ids=tuple(sample),
            planned_creates=creates,
            planned_updates=updates,
            planned_skip_manual_resync=skip_manual,
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
        updated = 0
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
                run_id=run_id,
                found=found,
                synced=0,
                updated=0,
                skipped=skipped,
                failed=0,
                dry_run=dry_run,
                dry_run_planned=0,
            )

        for ctx in contexts:
            if only_task_id is not None and ctx.task.id != only_task_id:
                continue
            if ctx.task.status != TaskStatus.APPROVED:
                continue
            found += 1
            draft = build_kanban_card_draft(ctx, opts)
            plan = plan_kanban_outbound(
                provider=provider,
                settings=self.settings,
                sync=self.sync,
                task_id=ctx.task.id,
                draft=draft,
            )
            if plan == KanbanOutboundPlan.SKIP_SAME_FINGERPRINT:
                skipped += 1
                continue
            if plan == KanbanOutboundPlan.SKIP_MANUAL_RESYNC:
                existing = self.sync.get_sync_record_for_task(ctx.task.id, provider)
                if existing is not None:
                    self.sync.mark_sync_skipped(
                        record_id=existing.id,
                        reason="fingerprint_changed_policy_skip_yougile_updates_disabled",
                    )
                skipped += 1
                self.logger.info("kanban.sync.skip_manual_resync", run_id=run_id, task_id=ctx.task.id)
                continue
            if dry_run:
                dry_planned += 1
                continue

            if plan == KanbanOutboundPlan.UPDATE_EXISTING:
                existing = self.sync.get_sync_record_for_task(ctx.task.id, provider)
                ext_before = (existing.external_card_id or "").strip() if existing is not None else ""
                if not ext_before:
                    rid = self.sync.upsert_pending_sync_record(
                        task_id=ctx.task.id,
                        provider=provider,
                        fingerprint=draft.fingerprint,
                        payload_json=_draft_payload_json(draft),
                    )
                    result = self.kanban.create_card(draft)
                else:
                    rid = existing.id
                    result = self.kanban.update_card(draft, external_card_id=ext_before)
                if result.success:
                    ext_final = (result.external_card_id or "").strip() or ext_before or None
                    self.sync.mark_sync_success(
                        record_id=rid,
                        fingerprint=draft.fingerprint,
                        external_card_id=ext_final,
                        external_card_url=result.external_card_url,
                    )
                    self.tasks.update_task_status(ctx.task.id, TaskStatus.SYNCED)
                    if ext_before:
                        updated += 1
                        self.logger.info("kanban.sync.task_updated", run_id=run_id, task_id=ctx.task.id, record_id=rid)
                    else:
                        synced += 1
                        self.logger.info("kanban.sync.task_synced", run_id=run_id, task_id=ctx.task.id, record_id=rid)
                else:
                    msg = result.error_message or "unknown_error"
                    self.sync.mark_sync_failed(record_id=rid, error=msg)
                    failed += 1
                    self.logger.warning("kanban.sync.task_update_failed", run_id=run_id, task_id=ctx.task.id, error=msg)
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
            updated=updated,
            skipped=skipped,
            failed=failed,
            dry_run=dry_run,
        )
        return KanbanSyncBatchResultDTO(
            run_id=run_id,
            found=found,
            synced=synced,
            updated=updated,
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
            ext = (rec.external_card_id or "").strip()
            if provider in (KanbanProvider.YOUGILE, KanbanProvider.TRELLO) and ext and draft.fingerprint == rec.card_fingerprint:
                result = self.kanban.update_card(draft, external_card_id=ext)
                rid = rec.id
            else:
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
        base = self.sync.load_status_summary(p)
        hint = kanban_status_readiness_hint(self.settings, p)
        return replace(base, provider_readiness=hint)


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

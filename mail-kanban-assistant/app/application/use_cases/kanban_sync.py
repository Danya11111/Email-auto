from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.application.dtos import (
    KanbanPreviewSummaryDTO,
    KanbanRetryBatchResultDTO,
    KanbanStatusSummaryDTO,
    KanbanSyncBatchResultDTO,
    KanbanTaskSyncInspectionDTO,
)
from app.application.kanban_mapping import KanbanMappingOptions, build_kanban_card_draft
from app.application.outbound_kanban_planner import (
    OutboundKanbanAction,
    plan_outbound_kanban_action,
    plan_resync_changed_action,
    yougile_kanban_config_ready,
)
from app.application.yougile_kanban_policies import resolve_yougile_assignee
from app.application.ports import KanbanPort, KanbanSyncRepositoryPort, LoggerPort, TaskRepositoryPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider, TaskStatus
from app.domain.models import KanbanCardDraft


def _draft_payload_json(draft: KanbanCardDraft) -> str:
    d = asdict(draft)
    d["priority"] = draft.priority.value
    d["card_status"] = draft.card_status.value
    if draft.placement_task_status is not None:
        d["placement_task_status"] = draft.placement_task_status.value
    return json.dumps(d, ensure_ascii=False)


def mapping_options_from_settings(settings: AppSettings) -> KanbanMappingOptions:
    return KanbanMappingOptions(
        max_title_chars=int(settings.kanban_max_title_chars),
        max_desc_chars=int(settings.kanban_max_desc_chars),
        include_review_metadata=bool(settings.kanban_include_review_metadata),
        include_message_metadata=bool(settings.kanban_include_message_metadata),
        default_card_status=settings.kanban_default_status,
    )


def _mapping_opts_for_provider(settings: AppSettings, provider: KanbanProvider) -> KanbanMappingOptions:
    base = mapping_options_from_settings(settings)
    if provider != KanbanProvider.YOUGILE:
        return base
    assignee = resolve_yougile_assignee(settings)
    return replace(base, assignee_external_id=assignee.assignee_external_id)


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


def _kanban_status_next_step_hint(settings: AppSettings, provider: KanbanProvider, summary: KanbanStatusSummaryDTO) -> str:
    if provider == KanbanProvider.YOUGILE:
        if not yougile_kanban_config_ready(settings):
            return "Fix YOUGILE_* env (see doctor), then run kanban-sync --dry-run."
        if summary.failed > 0:
            return "Run kanban-retry-failed --limit 20 to retry safe failures."
        if summary.manual_resync_pending > 0 and not settings.yougile_enable_update_existing:
            return "Manual resync backlog: enable YOUGILE_ENABLE_UPDATE_EXISTING or adjust tasks, then kanban-resync-changed."
        if summary.pending > 0:
            return "Run kanban-preview then kanban-sync (or kanban-sync --changed-only)."
    if provider == KanbanProvider.LOCAL_FILE:
        return "Run kanban-preview; use kanban-sync to export approved tasks to JSON cards."
    return "Run kanban-preview for a dry plan."


def _fingerprint_changed(*, sync: KanbanSyncRepositoryPort, task_id: int, provider: KanbanProvider, draft: KanbanCardDraft) -> bool:
    rec = sync.get_sync_record_for_task(task_id, provider)
    if rec is None:
        return True
    return rec.card_fingerprint != draft.fingerprint


@dataclass(frozen=True, slots=True)
class PreviewKanbanSyncCandidatesUseCase:
    tasks: TaskRepositoryPort
    sync: KanbanSyncRepositoryPort
    logger: LoggerPort
    settings: AppSettings

    def execute(
        self,
        *,
        provider: KanbanProvider,
        limit: int,
        draft_hook: Callable[[KanbanCardDraft], KanbanCardDraft] | None = None,
    ) -> KanbanPreviewSummaryDTO:
        opts = _mapping_opts_for_provider(self.settings, provider)
        contexts = list(self.tasks.list_approved_tasks_for_kanban(limit=limit))
        ready = 0
        skip_same = 0
        skip_manual = 0
        skip_cfg = 0
        fail_pre = 0
        creates = 0
        updates = 0
        sample: list[int] = []
        for ctx in contexts:
            if ctx.task.status != TaskStatus.APPROVED:
                continue
            ready += 1
            draft = build_kanban_card_draft(ctx, opts)
            if draft_hook is not None:
                draft = draft_hook(draft)
            plan = plan_outbound_kanban_action(
                task_status=ctx.task.status,
                provider=provider,
                settings=self.settings,
                sync=self.sync,
                task_id=ctx.task.id,
                draft=draft,
            )
            if plan.action == OutboundKanbanAction.SKIP_ALREADY_SYNCED:
                skip_same += 1
            elif plan.action == OutboundKanbanAction.SKIP_MANUAL_RESYNC:
                skip_manual += 1
            elif plan.action == OutboundKanbanAction.SKIP_PROVIDER_CONFIG:
                skip_cfg += 1
            elif plan.action == OutboundKanbanAction.FAIL_PRECONDITION:
                fail_pre += 1
            elif plan.action == OutboundKanbanAction.UPDATE_EXISTING:
                updates += 1
                if len(sample) < 10:
                    sample.append(ctx.task.id)
            elif plan.action == OutboundKanbanAction.CREATE:
                creates += 1
                if len(sample) < 10:
                    sample.append(ctx.task.id)
        self.logger.info(
            "kanban.preview",
            provider=provider.value,
            approved_ready=ready,
            skip_same_fp=skip_same,
            skip_manual=skip_manual,
            skip_provider_cfg=skip_cfg,
            fail_precondition=fail_pre,
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
            planned_skip_provider_config=skip_cfg,
            planned_fail_precondition=fail_pre,
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
        draft_hook: Callable[[KanbanCardDraft], KanbanCardDraft] | None = None,
        include_resync: bool = True,
        changed_only: bool = False,
    ) -> KanbanSyncBatchResultDTO:
        started = time.perf_counter()
        lim = int(limit) if limit is not None else int(self.settings.kanban_sync_batch_size)
        opts = _mapping_opts_for_provider(self.settings, provider)
        contexts = list(self.tasks.list_approved_tasks_for_kanban(limit=lim))

        found = 0
        synced = 0
        updated = 0
        skipped = 0
        failed = 0
        dry_planned = 0
        skip_provider_config = 0
        fail_precondition = 0
        skip_manual_resync = 0

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
            if draft_hook is not None:
                draft = draft_hook(draft)
            if changed_only and not _fingerprint_changed(sync=self.sync, task_id=ctx.task.id, provider=provider, draft=draft):
                skipped += 1
                continue

            plan = plan_outbound_kanban_action(
                task_status=ctx.task.status,
                provider=provider,
                settings=self.settings,
                sync=self.sync,
                task_id=ctx.task.id,
                draft=draft,
            )

            if plan.action == OutboundKanbanAction.SKIP_ALREADY_SYNCED:
                skipped += 1
                continue
            if plan.action == OutboundKanbanAction.SKIP_PROVIDER_CONFIG:
                skip_provider_config += 1
                self.logger.warning("kanban.sync.skip_provider_config", run_id=run_id, task_id=ctx.task.id, reason=plan.reason_code)
                continue
            if plan.action == OutboundKanbanAction.FAIL_PRECONDITION:
                fail_precondition += 1
                self.logger.warning("kanban.sync.fail_precondition", run_id=run_id, task_id=ctx.task.id, reason=plan.reason_code)
                continue
            if plan.action == OutboundKanbanAction.SKIP_MANUAL_RESYNC:
                existing = self.sync.get_sync_record_for_task(ctx.task.id, provider)
                if existing is not None:
                    self.sync.record_outbound_audit_preserve_synced(
                        record_id=existing.id,
                        outbound_action="skip_manual_resync",
                        operation_note=plan.reason_code,
                    )
                skip_manual_resync += 1
                self.logger.info("kanban.sync.skip_manual_resync", run_id=run_id, task_id=ctx.task.id, reason=plan.reason_code)
                continue
            if plan.action == OutboundKanbanAction.UPDATE_EXISTING and not include_resync:
                skipped += 1
                self.logger.info("kanban.sync.skip_resync_disabled", run_id=run_id, task_id=ctx.task.id)
                continue
            if dry_run:
                dry_planned += 1
                continue

            if plan.action == OutboundKanbanAction.UPDATE_EXISTING:
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
                    outbound = "create"
                else:
                    rid = existing.id
                    result = self.kanban.update_card(draft, external_card_id=ext_before)
                    outbound = "update_existing"
                if result.success:
                    ext_final = (result.external_card_id or "").strip() or ext_before or None
                    self.sync.mark_sync_success(
                        record_id=rid,
                        fingerprint=draft.fingerprint,
                        external_card_id=ext_final,
                        external_card_url=result.external_card_url,
                        outbound_action=outbound,
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
                    outbound_action="create",
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
            skip_provider_config=skip_provider_config,
            fail_precondition=fail_precondition,
            skip_manual_resync=skip_manual_resync,
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
        opts = _mapping_opts_for_provider(self.settings, provider)
        records = list(self.sync.list_failed_sync_records(provider, limit=lim, max_retry=max_retry))
        attempted = 0
        synced = 0
        failed = 0
        updated = 0
        skipped = 0

        if provider == KanbanProvider.STUB:
            return KanbanRetryBatchResultDTO(run_id=run_id, attempted=0, synced=0, failed=0)

        for rec in records:
            ctx = self.tasks.get_task_kanban_context(rec.task_id)
            if ctx is None or ctx.task.status != TaskStatus.APPROVED:
                self.logger.warning("kanban.retry.skip_task", task_id=rec.task_id, reason="not_approved_or_missing")
                skipped += 1
                continue
            draft = build_kanban_card_draft(ctx, opts)
            plan = plan_outbound_kanban_action(
                task_status=ctx.task.status,
                provider=provider,
                settings=self.settings,
                sync=self.sync,
                task_id=rec.task_id,
                draft=draft,
            )
            if plan.action in (OutboundKanbanAction.SKIP_PROVIDER_CONFIG, OutboundKanbanAction.FAIL_PRECONDITION):
                skipped += 1
                continue
            if plan.action == OutboundKanbanAction.SKIP_MANUAL_RESYNC:
                self.sync.record_outbound_audit_preserve_synced(
                    record_id=rec.id,
                    outbound_action="skip_manual_resync",
                    operation_note=plan.reason_code,
                )
                skipped += 1
                continue
            if plan.action == OutboundKanbanAction.SKIP_ALREADY_SYNCED:
                skipped += 1
                continue

            attempted += 1
            ext = (rec.external_card_id or "").strip()
            if plan.action == OutboundKanbanAction.UPDATE_EXISTING and ext:
                result = self.kanban.update_card(draft, external_card_id=ext)
                rid = rec.id
                outbound = "update_existing"
            else:
                rid = self.sync.upsert_pending_sync_record(
                    task_id=rec.task_id,
                    provider=provider,
                    fingerprint=draft.fingerprint,
                    payload_json=_draft_payload_json(draft),
                )
                result = self.kanban.create_card(draft)
                outbound = "create"
            if result.success:
                self.sync.mark_sync_success(
                    record_id=rid,
                    fingerprint=draft.fingerprint,
                    external_card_id=result.external_card_id,
                    external_card_url=result.external_card_url,
                    outbound_action=outbound,
                )
                self.tasks.update_task_status(rec.task_id, TaskStatus.SYNCED)
                if outbound == "update_existing":
                    updated += 1
                else:
                    synced += 1
            else:
                self.sync.mark_sync_failed(record_id=rid, error=result.error_message or "retry_failed")
                failed += 1

        self.logger.info(
            "kanban.retry.end", run_id=run_id, attempted=attempted, synced=synced, updated=updated, failed=failed, skipped=skipped
        )
        return KanbanRetryBatchResultDTO(
            run_id=run_id, attempted=attempted, synced=synced, failed=failed, updated=updated, skipped=skipped
        )


@dataclass(frozen=True, slots=True)
class ResyncChangedFingerprintsKanbanUseCase:
    """Fingerprint drift on already-synced rows; never performs silent mass creates."""

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
        lim = int(limit) if limit is not None else int(self.settings.kanban_sync_batch_size)
        opts = _mapping_opts_for_provider(self.settings, provider)
        candidates = list(self.sync.list_task_ids_for_resync_changed(provider, limit=lim))

        found = 0
        synced = 0
        updated = 0
        skipped = 0
        failed = 0
        dry_planned = 0
        skip_manual_resync = 0
        skip_provider_config = 0
        fail_precondition = 0

        if provider == KanbanProvider.STUB:
            return KanbanSyncBatchResultDTO(
                run_id=run_id,
                found=0,
                synced=0,
                updated=0,
                skipped=0,
                failed=0,
                dry_run=dry_run,
            )

        for tid in candidates:
            if only_task_id is not None and tid != only_task_id:
                continue
            ctx = self.tasks.get_task_kanban_context(tid)
            if ctx is None:
                continue
            draft = build_kanban_card_draft(ctx, opts)
            plan = plan_resync_changed_action(
                task_status=ctx.task.status,
                provider=provider,
                settings=self.settings,
                sync=self.sync,
                task_id=tid,
                draft=draft,
            )
            if plan.action == OutboundKanbanAction.SKIP_ALREADY_SYNCED:
                skipped += 1
                continue
            if plan.action == OutboundKanbanAction.SKIP_PROVIDER_CONFIG:
                skip_provider_config += 1
                continue
            if plan.action == OutboundKanbanAction.FAIL_PRECONDITION:
                fail_precondition += 1
                continue
            if plan.action == OutboundKanbanAction.SKIP_MANUAL_RESYNC:
                rec = self.sync.get_sync_record_for_task(tid, provider)
                if rec is not None:
                    self.sync.record_outbound_audit_preserve_synced(
                        record_id=rec.id,
                        outbound_action="skip_manual_resync",
                        operation_note=plan.reason_code,
                    )
                skip_manual_resync += 1
                continue
            found += 1
            if dry_run:
                dry_planned += 1
                continue

            rec = self.sync.get_sync_record_for_task(tid, provider)
            if rec is None:
                continue
            ext = (rec.external_card_id or "").strip()
            if plan.action != OutboundKanbanAction.UPDATE_EXISTING or not ext:
                fail_precondition += 1
                continue
            result = self.kanban.update_card(draft, external_card_id=ext)
            rid = rec.id
            if result.success:
                self.sync.mark_sync_success(
                    record_id=rid,
                    fingerprint=draft.fingerprint,
                    external_card_id=result.external_card_id or ext,
                    external_card_url=result.external_card_url,
                    outbound_action="update_existing",
                )
                updated += 1
                self.logger.info("kanban.resync_changed.updated", run_id=run_id, task_id=tid, record_id=rid)
            else:
                self.sync.mark_sync_failed(record_id=rid, error=result.error_message or "resync_changed_failed")
                failed += 1

        self.logger.info(
            "kanban.resync_changed.end",
            run_id=run_id,
            found=found,
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
            skip_provider_config=skip_provider_config,
            fail_precondition=fail_precondition,
            skip_manual_resync=skip_manual_resync,
        )


@dataclass(frozen=True, slots=True)
class ShowKanbanTaskSyncUseCase:
    tasks: TaskRepositoryPort
    sync: KanbanSyncRepositoryPort
    settings: AppSettings

    def execute(self, *, task_id: int, provider: KanbanProvider | None = None) -> KanbanTaskSyncInspectionDTO:
        p = provider or self.settings.kanban_provider
        ctx = self.tasks.get_task_kanban_context(task_id)
        rec = self.sync.get_sync_record_for_task(task_id, p)
        opts = _mapping_opts_for_provider(self.settings, p)
        draft_fp: str | None = None
        planned_action: str | None = None
        planned_reason: str | None = None
        update_possible = bool(p == KanbanProvider.YOUGILE and self.settings.yougile_enable_update_existing)
        manual_required = False

        if ctx is not None:
            draft = build_kanban_card_draft(ctx, opts)
            draft_fp = draft.fingerprint
            pl = plan_outbound_kanban_action(
                task_status=ctx.task.status,
                provider=p,
                settings=self.settings,
                sync=self.sync,
                task_id=task_id,
                draft=draft,
            )
            planned_action = pl.action.value
            planned_reason = pl.reason_code
            manual_required = pl.action == OutboundKanbanAction.SKIP_MANUAL_RESYNC

        return KanbanTaskSyncInspectionDTO(
            task_id=task_id,
            provider=p,
            local_task_status=ctx.task.status if ctx is not None else None,
            sync_status=rec.sync_status if rec is not None else None,
            card_fingerprint=rec.card_fingerprint if rec is not None else None,
            external_card_id=rec.external_card_id if rec is not None else None,
            external_card_url=rec.external_card_url if rec is not None else None,
            last_outbound_action=rec.last_outbound_action if rec is not None else None,
            last_operation_note=rec.last_operation_note if rec is not None else None,
            previous_fingerprint=rec.previous_fingerprint if rec is not None else None,
            previous_external_card_url=rec.previous_external_card_url if rec is not None else None,
            retry_count=rec.retry_count if rec is not None else None,
            last_error=rec.last_error if rec is not None else None,
            last_attempt_at=rec.last_attempt_at.isoformat() if rec is not None and rec.last_attempt_at else None,
            synced_at=rec.synced_at.isoformat() if rec is not None and rec.synced_at else None,
            record_updated_at=rec.record_updated_at.isoformat() if rec is not None and rec.record_updated_at else None,
            planned_outbound_action=planned_action,
            planned_reason_code=planned_reason,
            current_draft_fingerprint=draft_fp,
            update_existing_possible=update_possible,
            manual_resync_required=manual_required,
        )


@dataclass(frozen=True, slots=True)
class ListKanbanSyncStatusUseCase:
    sync: KanbanSyncRepositoryPort
    settings: AppSettings

    def execute(self, *, provider: KanbanProvider | None = None) -> KanbanStatusSummaryDTO:
        p = provider or self.settings.kanban_provider
        base = self.sync.load_status_summary(p)
        hint = kanban_status_readiness_hint(self.settings, p)
        next_hint = _kanban_status_next_step_hint(self.settings, p, base)
        if p == KanbanProvider.YOUGILE:
            return replace(
                base,
                provider_readiness=hint,
                yougile_update_existing_enabled=bool(self.settings.yougile_enable_update_existing),
                yougile_done_column_configured=bool(self.settings.yougile_column_id_done.strip()),
                yougile_blocked_column_configured=bool(self.settings.yougile_column_id_blocked.strip()),
                next_step_hint=next_hint,
            )
        return replace(base, provider_readiness=hint, next_step_hint=next_hint)


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

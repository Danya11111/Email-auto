from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.application.ports import KanbanSyncRepositoryPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider, KanbanSyncStatus, TaskStatus
from app.domain.models import KanbanCardDraft


class OutboundKanbanAction(StrEnum):
    """Single decision point for outbound Kanban writes (per task, per provider)."""

    CREATE = "create"
    UPDATE_EXISTING = "update_existing"
    SKIP_ALREADY_SYNCED = "skip_already_synced"
    SKIP_MANUAL_RESYNC = "skip_manual_resync"
    SKIP_PROVIDER_CONFIG = "skip_provider_config"
    FAIL_PRECONDITION = "fail_precondition"


@dataclass(frozen=True, slots=True)
class OutboundKanbanPlan:
    action: OutboundKanbanAction
    """Machine-readable reason for logging / audit (not end-user prose)."""
    reason_code: str


def yougile_kanban_config_ready(settings: AppSettings) -> bool:
    return bool(settings.yougile_api_key.strip() and settings.yougile_column_id_todo.strip())


def provider_kanban_config_ready(*, provider: KanbanProvider, settings: AppSettings) -> bool:
    if provider == KanbanProvider.YOUGILE:
        return yougile_kanban_config_ready(settings)
    if provider == KanbanProvider.TRELLO:
        return bool(
            settings.trello_api_key.strip()
            and settings.trello_token.strip()
            and settings.trello_list_id_todo.strip()
        )
    if provider == KanbanProvider.LOCAL_FILE:
        return True
    if provider == KanbanProvider.STUB:
        return True
    return False


def plan_outbound_kanban_action(
    *,
    task_status: TaskStatus,
    provider: KanbanProvider,
    settings: AppSettings,
    sync: KanbanSyncRepositoryPort,
    task_id: int,
    draft: KanbanCardDraft,
) -> OutboundKanbanPlan:
    if task_status != TaskStatus.APPROVED:
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.FAIL_PRECONDITION,
            reason_code="task_not_approved_for_outbound_sync",
        )

    if not provider_kanban_config_ready(provider=provider, settings=settings):
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.SKIP_PROVIDER_CONFIG,
            reason_code="provider_config_incomplete",
        )

    if sync.maybe_skip_if_already_synced_same_fingerprint(task_id=task_id, provider=provider, fingerprint=draft.fingerprint):
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.SKIP_ALREADY_SYNCED,
            reason_code="same_fingerprint_as_last_successful_sync",
        )

    existing = sync.get_sync_record_for_task(task_id, provider)
    if existing is None:
        return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="no_sync_record")

    if existing.sync_status == KanbanSyncStatus.FAILED:
        if int(existing.retry_count) >= int(settings.kanban_retry_limit):
            return OutboundKanbanPlan(
                action=OutboundKanbanAction.FAIL_PRECONDITION,
                reason_code="retry_limit_exhausted",
            )
        ext = (existing.external_card_id or "").strip()
        if not ext:
            return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="failed_without_external_id")
        if draft.fingerprint == existing.card_fingerprint:
            return OutboundKanbanPlan(action=OutboundKanbanAction.UPDATE_EXISTING, reason_code="failed_retry_same_fingerprint")
        if provider == KanbanProvider.YOUGILE and not settings.yougile_enable_update_existing:
            return OutboundKanbanPlan(
                action=OutboundKanbanAction.SKIP_MANUAL_RESYNC,
                reason_code="fingerprint_changed_yougile_updates_disabled",
            )
        if provider == KanbanProvider.YOUGILE and settings.yougile_enable_update_existing:
            return OutboundKanbanPlan(action=OutboundKanbanAction.UPDATE_EXISTING, reason_code="failed_retry_update_allowed")
        return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="failed_retry_non_yougile_create")

    if existing.sync_status != KanbanSyncStatus.SYNCED:
        return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="resync_pending_or_non_synced_record")

    if existing.card_fingerprint == draft.fingerprint:
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.SKIP_ALREADY_SYNCED,
            reason_code="synced_row_matches_fingerprint",
        )
    if not (existing.external_card_id or "").strip():
        return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="synced_missing_external_id")

    if provider == KanbanProvider.YOUGILE:
        if settings.yougile_enable_update_existing:
            return OutboundKanbanPlan(action=OutboundKanbanAction.UPDATE_EXISTING, reason_code="fingerprint_changed_update_enabled")
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.SKIP_MANUAL_RESYNC,
            reason_code="fingerprint_changed_yougile_updates_disabled",
        )

    if provider == KanbanProvider.LOCAL_FILE:
        return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="local_file_recreate_on_drift")

    if provider == KanbanProvider.TRELLO:
        return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="trello_recreate_on_drift")

    return OutboundKanbanPlan(action=OutboundKanbanAction.CREATE, reason_code="default_create")


def plan_resync_changed_action(
    *,
    task_status: TaskStatus,
    provider: KanbanProvider,
    settings: AppSettings,
    sync: KanbanSyncRepositoryPort,
    task_id: int,
    draft: KanbanCardDraft,
) -> OutboundKanbanPlan:
    """Operational resync: only fingerprint drift on previously successful rows (no silent mass create)."""
    if task_status not in (TaskStatus.APPROVED, TaskStatus.SYNCED):
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.FAIL_PRECONDITION,
            reason_code="task_status_not_eligible_for_resync_changed",
        )

    if not provider_kanban_config_ready(provider=provider, settings=settings):
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.SKIP_PROVIDER_CONFIG,
            reason_code="provider_config_incomplete",
        )

    existing = sync.get_sync_record_for_task(task_id, provider)
    if existing is None or existing.sync_status != KanbanSyncStatus.SYNCED:
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.FAIL_PRECONDITION,
            reason_code="no_synced_record_for_resync_changed",
        )
    if not (existing.external_card_id or "").strip():
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.FAIL_PRECONDITION,
            reason_code="synced_record_missing_external_id",
        )
    if existing.card_fingerprint == draft.fingerprint:
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.SKIP_ALREADY_SYNCED,
            reason_code="fingerprint_unchanged",
        )

    if provider == KanbanProvider.YOUGILE and not settings.yougile_enable_update_existing:
        return OutboundKanbanPlan(
            action=OutboundKanbanAction.SKIP_MANUAL_RESYNC,
            reason_code="fingerprint_changed_yougile_updates_disabled",
        )

    if provider == KanbanProvider.YOUGILE and settings.yougile_enable_update_existing:
        return OutboundKanbanPlan(action=OutboundKanbanAction.UPDATE_EXISTING, reason_code="resync_changed_update")

    if provider == KanbanProvider.LOCAL_FILE:
        return OutboundKanbanPlan(action=OutboundKanbanAction.UPDATE_EXISTING, reason_code="local_file_resync_overwrite")

    return OutboundKanbanPlan(
        action=OutboundKanbanAction.FAIL_PRECONDITION,
        reason_code="resync_changed_not_supported_for_provider",
    )

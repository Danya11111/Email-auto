from __future__ import annotations

from enum import StrEnum

from app.application.ports import KanbanSyncRepositoryPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider, KanbanSyncStatus
from app.domain.models import KanbanCardDraft


class KanbanOutboundPlan(StrEnum):
    """Planned external action for an approved task at the active provider."""

    SKIP_SAME_FINGERPRINT = "skip_same_fingerprint"
    """Fingerprint matches last successful sync — no write."""
    CREATE = "create"
    """Create a new external card/task (or overwrite local_file JSON in-place)."""
    UPDATE_EXISTING = "update_existing"
    """Safe in-place update of an existing external task (provider + config must allow)."""
    SKIP_MANUAL_RESYNC = "skip_manual_resync"
    """Fingerprint changed after sync but policy forbids silent create/update (YouGile default)."""


def plan_kanban_outbound(
    *,
    provider: KanbanProvider,
    settings: AppSettings,
    sync: KanbanSyncRepositoryPort,
    task_id: int,
    draft: KanbanCardDraft,
) -> KanbanOutboundPlan:
    if sync.maybe_skip_if_already_synced_same_fingerprint(task_id=task_id, provider=provider, fingerprint=draft.fingerprint):
        return KanbanOutboundPlan.SKIP_SAME_FINGERPRINT

    existing = sync.get_sync_record_for_task(task_id, provider)
    if existing is None:
        return KanbanOutboundPlan.CREATE

    if existing.sync_status == KanbanSyncStatus.FAILED:
        ext = (existing.external_card_id or "").strip()
        if not ext:
            return KanbanOutboundPlan.CREATE
        if draft.fingerprint == existing.card_fingerprint:
            return KanbanOutboundPlan.UPDATE_EXISTING
        if provider == KanbanProvider.YOUGILE and not settings.yougile_enable_update_existing:
            return KanbanOutboundPlan.SKIP_MANUAL_RESYNC
        if provider == KanbanProvider.YOUGILE and settings.yougile_enable_update_existing:
            return KanbanOutboundPlan.UPDATE_EXISTING
        return KanbanOutboundPlan.CREATE

    if existing.sync_status != KanbanSyncStatus.SYNCED:
        return KanbanOutboundPlan.CREATE

    if existing.card_fingerprint == draft.fingerprint:
        return KanbanOutboundPlan.SKIP_SAME_FINGERPRINT
    if not (existing.external_card_id or "").strip():
        return KanbanOutboundPlan.CREATE

    if provider == KanbanProvider.YOUGILE:
        if settings.yougile_enable_update_existing:
            return KanbanOutboundPlan.UPDATE_EXISTING
        return KanbanOutboundPlan.SKIP_MANUAL_RESYNC

    if provider == KanbanProvider.LOCAL_FILE:
        return KanbanOutboundPlan.CREATE

    if provider == KanbanProvider.TRELLO:
        return KanbanOutboundPlan.CREATE

    return KanbanOutboundPlan.CREATE

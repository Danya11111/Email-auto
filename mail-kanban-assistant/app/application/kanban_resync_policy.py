from __future__ import annotations

"""Backward-compatible re-exports; outbound decisions live in `outbound_kanban_planner`."""

from app.application.outbound_kanban_planner import (
    OutboundKanbanAction as KanbanOutboundPlan,
    OutboundKanbanPlan,
    plan_outbound_kanban_action,
)
from app.application.ports import KanbanSyncRepositoryPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider, TaskStatus
from app.domain.models import KanbanCardDraft

# Legacy alias values (StrEnum) — use OutboundKanbanAction in new code.
SKIP_SAME_FINGERPRINT = KanbanOutboundPlan.SKIP_ALREADY_SYNCED
CREATE = KanbanOutboundPlan.CREATE
UPDATE_EXISTING = KanbanOutboundPlan.UPDATE_EXISTING
SKIP_MANUAL_RESYNC = KanbanOutboundPlan.SKIP_MANUAL_RESYNC


def plan_kanban_outbound(
    *,
    provider: KanbanProvider,
    settings: AppSettings,
    sync: KanbanSyncRepositoryPort,
    task_id: int,
    draft: KanbanCardDraft,
    task_status: TaskStatus = TaskStatus.APPROVED,
) -> KanbanOutboundPlan:
    """Delegate to centralized planner (returns action enum only for compatibility)."""
    return plan_outbound_kanban_action(
        task_status=task_status,
        provider=provider,
        settings=settings,
        sync=sync,
        task_id=task_id,
        draft=draft,
    ).action


__all__ = [
    "KanbanOutboundPlan",
    "OutboundKanbanPlan",
    "plan_kanban_outbound",
    "plan_outbound_kanban_action",
    "SKIP_SAME_FINGERPRINT",
    "CREATE",
    "UPDATE_EXISTING",
    "SKIP_MANUAL_RESYNC",
]

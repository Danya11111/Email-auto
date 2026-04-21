from __future__ import annotations

from dataclasses import dataclass

from app.application.kanban_mapping import resolve_card_status_for_kanban_task
from app.config import AppSettings
from app.domain.enums import KanbanCardStatus, TaskStatus
from app.domain.models import KanbanCardDraft


@dataclass(frozen=True, slots=True)
class YougileColumnPick:
    """Resolved YouGile column id for a card draft (application-layer policy; not HTTP)."""

    column_id: str
    warnings: tuple[str, ...]


def pick_yougile_column_for_draft(settings: AppSettings, draft: KanbanCardDraft) -> YougileColumnPick:
    """
    Map local task lifecycle + logical card status to YouGile column ids from env.
    Fingerprint stays stable (card_status on draft stays default); column follows task_status mapping.
    Missing optional columns fall back to TODO with explicit warnings (safe default).
    """
    warnings: list[str] = []
    todo = settings.yougile_column_id_todo.strip()
    done = settings.yougile_column_id_done.strip()
    blocked = settings.yougile_column_id_blocked.strip()

    task_status = draft.placement_task_status or TaskStatus.APPROVED
    logical = resolve_card_status_for_kanban_task(task_status, draft.card_status)

    if logical == KanbanCardStatus.DONE:
        if done:
            return YougileColumnPick(column_id=done, warnings=tuple(warnings))
        warnings.append("YOUGILE_COLUMN_ID_DONE unset — using TODO column for synced/done-like tasks")
        return YougileColumnPick(column_id=todo, warnings=tuple(warnings))

    if logical == KanbanCardStatus.BLOCKED:
        if blocked:
            return YougileColumnPick(column_id=blocked, warnings=tuple(warnings))
        warnings.append("YOUGILE_COLUMN_ID_BLOCKED unset — using TODO column for blocked/rejected tasks")
        return YougileColumnPick(column_id=todo, warnings=tuple(warnings))

    return YougileColumnPick(column_id=todo, warnings=tuple(warnings))


@dataclass(frozen=True, slots=True)
class YougilePriorityStickerPlan:
    """
    Future-facing contract for mapping KanbanPriority to YouGile stickers/states.
    MVP keeps priority in description (adapter baseline); sticker mapping stays inactive until explicitly completed.
    """

    sticker_name: str
    state_low: str
    state_medium: str
    state_high: str
    state_critical: str

    @classmethod
    def from_settings(cls, settings: AppSettings) -> YougilePriorityStickerPlan:
        return cls(
            sticker_name=settings.yougile_priority_sticker_name.strip(),
            state_low=settings.yougile_priority_state_low.strip(),
            state_medium=settings.yougile_priority_state_medium.strip(),
            state_high=settings.yougile_priority_state_high.strip(),
            state_critical=settings.yougile_priority_state_critical.strip(),
        )

    def is_active(self) -> bool:
        return bool(self.sticker_name and self.state_low and self.state_medium and self.state_high and self.state_critical)


def describe_yougile_priority_baseline_note() -> str:
    return "Priority is embedded in description until sticker mapping is fully validated and enabled."


@dataclass(frozen=True, slots=True)
class YougileAssigneeResolution:
    """Scaffold for future assignee mapping; MVP returns None only."""

    assignee_external_id: str | None
    note: str | None


def resolve_yougile_assignee(settings: AppSettings) -> YougileAssigneeResolution:
    v = settings.yougile_default_assignee_external_id.strip()
    if v:
        return YougileAssigneeResolution(
            assignee_external_id=v,
            note="assignee_external_id set via YOUGILE_DEFAULT_ASSIGNEE_EXTERNAL_ID (not applied to API in MVP)",
        )
    return YougileAssigneeResolution(
        assignee_external_id=None,
        note="Assignee automation is planned; configure YOUGILE_DEFAULT_ASSIGNEE_EXTERNAL_ID for future wiring.",
    )

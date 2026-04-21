from __future__ import annotations

import pytest

from app.application.yougile_kanban_policies import (
    YougilePriorityStickerPlan,
    pick_yougile_column_for_draft,
    resolve_yougile_assignee,
)
from app.config import AppSettings
from app.domain.enums import KanbanCardStatus, KanbanPriority, TaskStatus
from app.domain.models import KanbanCardDraft


def _draft(*, placement: TaskStatus | None = None) -> KanbanCardDraft:
    return KanbanCardDraft(
        internal_task_id=1,
        source_message_id=1,
        title="t",
        description="d",
        due_at=None,
        priority=KanbanPriority.MEDIUM,
        card_status=KanbanCardStatus.TODO,
        labels=(),
        dedupe_marker="x",
        fingerprint="fp",
        placement_task_status=placement,
    )


def test_pick_column_done_when_synced_and_done_column_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_COLUMN_ID_TODO", "todo-1")
    monkeypatch.setenv("YOUGILE_COLUMN_ID_DONE", "done-1")
    s = AppSettings()
    pick = pick_yougile_column_for_draft(s, _draft(placement=TaskStatus.SYNCED))
    assert pick.column_id == "done-1"
    assert pick.warnings == ()


def test_pick_column_fallback_when_done_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_COLUMN_ID_TODO", "todo-1")
    monkeypatch.delenv("YOUGILE_COLUMN_ID_DONE", raising=False)
    s = AppSettings()
    pick = pick_yougile_column_for_draft(s, _draft(placement=TaskStatus.SYNCED))
    assert pick.column_id == "todo-1"
    assert pick.warnings


def test_priority_sticker_plan_inactive_by_default() -> None:
    s = AppSettings()
    plan = YougilePriorityStickerPlan.from_settings(s)
    assert plan.is_active() is False


def test_assignee_scaffold_none_by_default() -> None:
    s = AppSettings()
    r = resolve_yougile_assignee(s)
    assert r.assignee_external_id is None

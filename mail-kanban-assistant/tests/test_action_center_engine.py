from __future__ import annotations

from datetime import UTC, datetime

from app.application.action_center_engine import build_action_center_snapshot
from app.application.dtos import (
    ActionCenterMessageRowDTO,
    ActionCenterRawBundleDTO,
    ActionCenterTaskPinDTO,
    DigestReviewSnapshotDTO,
    KanbanSyncFailurePinDTO,
)
from app.config import AppSettings
from app.domain.enums import (
    ActionCenterCategory,
    MessageImportance,
    ReplyRequirement,
    ReplyState,
    ReviewKind,
    TaskStatus,
)


def _msg(
    mid: int,
    *,
    subj: str,
    imp: MessageImportance,
    rep: ReplyRequirement,
    actionable: bool,
    at: datetime,
) -> ActionCenterMessageRowDTO:
    return ActionCenterMessageRowDTO(
        message_id=mid,
        received_at=at,
        subject=subj,
        sender="client@ext.com",
        recipients=("me@local",),
        thread_hint=None,
        importance=imp,
        reply_requirement=rep,
        actionable=actionable,
        triage_summary="s",
        triage_confidence=0.9,
    )


def test_high_priority_thread_single_action_item_not_per_message() -> None:
    t0 = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    m1 = _msg(1, subj="Re: Contract", imp=MessageImportance.HIGH, rep=ReplyRequirement.REQUIRED, actionable=True, at=t0)
    m2 = _msg(2, subj="Re: Contract", imp=MessageImportance.MEDIUM, rep=ReplyRequirement.OPTIONAL, actionable=False, at=t0)
    bundle = ActionCenterRawBundleDTO(
        window_start=t0,
        window_end=t0,
        messages=(m1, m2),
        task_pins=(),
        pending_reviews=(),
        kanban_failures=(),
        approved_ready_to_sync=0,
        manual_resync_backlog=0,
    )
    settings = AppSettings(
        thread_grouping_time_window_hours=96,
        reply_overdue_hours=200,
        reply_recommended_hours=24,
        action_center_max_items=50,
        action_center_include_informational=False,
        action_center_require_review_for_ambiguous_reply=False,
    )
    snap = build_action_center_snapshot(bundle, settings=settings, now=t0)
    thread_items = [i for i in snap.items if i.source_type == "thread"]
    assert len(thread_items) == 1
    assert len(thread_items[0].message_ids) == 2


def test_kanban_failure_boosts_category() -> None:
    t0 = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    m1 = _msg(1, subj="FYI", imp=MessageImportance.LOW, rep=ReplyRequirement.NO, actionable=False, at=t0)
    fail = KanbanSyncFailurePinDTO(sync_record_id=9, task_id=42, provider="local_file", last_error="boom")
    bundle = ActionCenterRawBundleDTO(
        window_start=t0,
        window_end=t0,
        messages=(m1,),
        task_pins=(),
        pending_reviews=(),
        kanban_failures=(fail,),
        approved_ready_to_sync=0,
        manual_resync_backlog=0,
    )
    settings = AppSettings(
        thread_grouping_time_window_hours=96,
        action_center_max_items=50,
        action_center_include_informational=False,
        action_center_require_review_for_ambiguous_reply=False,
    )
    snap = build_action_center_snapshot(bundle, settings=settings, now=t0)
    assert any(i.item_id == "ac:kanban:9" for i in snap.items)
    item = next(i for i in snap.items if i.item_id == "ac:kanban:9")
    assert item.category == ActionCenterCategory.TASKS_APPROVE_OR_SYNC


def test_top_items_sorted_and_explain_fields() -> None:
    t0 = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    m1 = _msg(1, subj="A", imp=MessageImportance.CRITICAL, rep=ReplyRequirement.URGENT, actionable=True, at=t0)
    m2 = _msg(2, subj="B", imp=MessageImportance.LOW, rep=ReplyRequirement.NO, actionable=False, at=t0)
    task = ActionCenterTaskPinDTO(
        task_id=7, message_id=2, title="t", status=TaskStatus.CANDIDATE, confidence=0.9, due_at=None
    )
    rev = DigestReviewSnapshotDTO(
        review_id=3,
        review_kind=ReviewKind.TRIAGE,
        message_id=2,
        task_id=None,
        reason_code="low_conf",
        reason_text="check",
        confidence=0.5,
    )
    bundle = ActionCenterRawBundleDTO(
        window_start=t0,
        window_end=t0,
        messages=(m1, m2),
        task_pins=(task,),
        pending_reviews=(rev,),
        kanban_failures=(),
        approved_ready_to_sync=0,
        manual_resync_backlog=0,
    )
    settings = AppSettings(
        thread_grouping_time_window_hours=96,
        reply_overdue_hours=500,
        reply_recommended_hours=24,
        action_center_max_items=10,
        action_center_include_informational=False,
        action_center_require_review_for_ambiguous_reply=True,
    )
    snap = build_action_center_snapshot(bundle, settings=settings, now=t0)
    assert len(snap.items) >= 2
    scores = [i.priority_score for i in snap.items]
    assert scores == sorted(scores, reverse=True)
    assert all(i.recommended_next_step for i in snap.items)
    assert any(i.reply_state == ReplyState.AMBIGUOUS for i in snap.items if i.source_type == "thread")

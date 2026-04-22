from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.reply_state_rules import infer_reply_state
from app.domain.enums import ReplyRequirement, ReplyState


def test_no_reply_needed() -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    rs = infer_reply_state(
        max_reply_requirement=ReplyRequirement.NO,
        any_actionable=False,
        latest_message_at=now - timedelta(hours=1),
        now=now,
        overdue_after=timedelta(hours=48),
        recommended_within=timedelta(hours=24),
        has_pending_review=False,
    )
    assert rs == ReplyState.NO_REPLY_NEEDED


def test_waiting_for_them_optional_not_actionable() -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    rs = infer_reply_state(
        max_reply_requirement=ReplyRequirement.OPTIONAL,
        any_actionable=False,
        latest_message_at=now - timedelta(hours=1),
        now=now,
        overdue_after=timedelta(hours=48),
        recommended_within=timedelta(hours=24),
        has_pending_review=False,
    )
    assert rs == ReplyState.WAITING_FOR_THEM


def test_waiting_for_us_required_actionable() -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    latest = now - timedelta(hours=30)
    rs = infer_reply_state(
        max_reply_requirement=ReplyRequirement.REQUIRED,
        any_actionable=True,
        latest_message_at=latest,
        now=now,
        overdue_after=timedelta(hours=48),
        recommended_within=timedelta(hours=24),
        has_pending_review=False,
    )
    assert rs == ReplyState.WAITING_FOR_US


def test_overdue_for_us() -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    latest = now - timedelta(hours=100)
    rs = infer_reply_state(
        max_reply_requirement=ReplyRequirement.REQUIRED,
        any_actionable=True,
        latest_message_at=latest,
        now=now,
        overdue_after=timedelta(hours=48),
        recommended_within=timedelta(hours=24),
        has_pending_review=False,
    )
    assert rs == ReplyState.OVERDUE_FOR_US


def test_ambiguous_pending_review() -> None:
    now = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    rs = infer_reply_state(
        max_reply_requirement=ReplyRequirement.REQUIRED,
        any_actionable=True,
        latest_message_at=now - timedelta(hours=1),
        now=now,
        overdue_after=timedelta(hours=48),
        recommended_within=timedelta(hours=24),
        has_pending_review=True,
    )
    assert rs == ReplyState.AMBIGUOUS

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.dtos import ActionCenterMessageRowDTO
from app.application.thread_grouping import cluster_messages_into_threads, grouping_key
from app.domain.enums import MessageImportance, ReplyRequirement


def _row(
    mid: int,
    *,
    received: datetime,
    subject: str,
    sender: str,
    thread_hint: str | None = None,
) -> ActionCenterMessageRowDTO:
    return ActionCenterMessageRowDTO(
        message_id=mid,
        received_at=received,
        subject=subject,
        sender=sender,
        recipients=(),
        thread_hint=thread_hint,
        importance=MessageImportance.MEDIUM,
        reply_requirement=ReplyRequirement.NO,
        actionable=False,
        triage_summary="",
        triage_confidence=0.8,
    )


def test_explicit_thread_hint_groups_together() -> None:
    t0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    a = _row(1, received=t0, subject="Re: Plan", sender="a@x.com", thread_hint="tid-1")
    b = _row(2, received=t0 + timedelta(hours=1), subject="Fwd: Plan", sender="b@x.com", thread_hint="tid-1")
    assert grouping_key(a).startswith("hint:")
    out = cluster_messages_into_threads((a, b), time_window=timedelta(hours=24))
    assert len(out) == 1
    tid = next(iter(out))
    assert tid.startswith("t-hint-")
    assert set(out[tid]) == {1, 2}


def test_normalized_subject_fallback_same_party_merges_within_window() -> None:
    t0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    a = _row(1, received=t0, subject="Re: Budget Q2", sender="Alice <a@corp.com>")
    b = _row(2, received=t0 + timedelta(hours=2), subject="Fwd: Budget Q2", sender="Alice <a@corp.com>")
    out = cluster_messages_into_threads((a, b), time_window=timedelta(hours=96))
    assert len(out) == 1
    assert set(next(iter(out.values()))) == {1, 2}


def test_different_participants_do_not_share_heuristic_key() -> None:
    t0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    a = _row(1, received=t0, subject="Re: Budget Q2", sender="alice@corp.com")
    b = _row(2, received=t0 + timedelta(hours=1), subject="Fwd: Budget Q2", sender="bob@corp.com")
    assert grouping_key(a) != grouping_key(b)


def test_time_window_prevents_merge() -> None:
    t0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    a = _row(1, received=t0, subject="Project X", sender="a@x.com")
    b = _row(2, received=t0 + timedelta(hours=50), subject="Re: Project X", sender="a@x.com")
    out = cluster_messages_into_threads((a, b), time_window=timedelta(hours=24))
    assert len(out) == 2

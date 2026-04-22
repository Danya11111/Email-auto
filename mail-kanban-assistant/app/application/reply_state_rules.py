from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.enums import MessageImportance, ReplyRequirement, ReplyState


def infer_reply_state(
    *,
    max_reply_requirement: ReplyRequirement,
    any_actionable: bool,
    latest_message_at: datetime | None,
    now: datetime,
    overdue_after: timedelta,
    recommended_within: timedelta,
    has_pending_review: bool,
) -> ReplyState:
    """Rule-based reply posture for an aggregated thread (no network, no LLM)."""
    if has_pending_review:
        return ReplyState.AMBIGUOUS

    if not any_actionable and max_reply_requirement == ReplyRequirement.NO:
        return ReplyState.NO_REPLY_NEEDED

    if not any_actionable and max_reply_requirement == ReplyRequirement.OPTIONAL:
        return ReplyState.WAITING_FOR_THEM

    if max_reply_requirement in (ReplyRequirement.REQUIRED, ReplyRequirement.URGENT) and any_actionable:
        if latest_message_at is None:
            return ReplyState.WAITING_FOR_US
        age = now - latest_message_at.astimezone(UTC)
        if age > overdue_after:
            return ReplyState.OVERDUE_FOR_US
        if age <= recommended_within:
            return ReplyState.REPLY_RECOMMENDED_TODAY
        return ReplyState.WAITING_FOR_US

    if max_reply_requirement == ReplyRequirement.OPTIONAL and any_actionable:
        return ReplyState.REPLY_RECOMMENDED_TODAY

    return ReplyState.AMBIGUOUS


def max_importance(a: MessageImportance, b: MessageImportance) -> MessageImportance:
    order = (
        MessageImportance.LOW,
        MessageImportance.MEDIUM,
        MessageImportance.HIGH,
        MessageImportance.CRITICAL,
    )
    return a if order.index(a) >= order.index(b) else b


def max_reply_requirement(a: ReplyRequirement, b: ReplyRequirement) -> ReplyRequirement:
    order = (ReplyRequirement.NO, ReplyRequirement.OPTIONAL, ReplyRequirement.REQUIRED, ReplyRequirement.URGENT)
    return a if order.index(a) >= order.index(b) else b

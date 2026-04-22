from __future__ import annotations

from app.domain.enums import MessageImportance, ReplyRequirement, ReplyState, ThreadActionState


def infer_thread_action_state(
    *,
    aggregated_importance: MessageImportance,
    max_reply_requirement: ReplyRequirement,
    any_actionable: bool,
    has_pending_review: bool,
    reply_state: ReplyState,
) -> ThreadActionState:
    if has_pending_review:
        return ThreadActionState.REVIEW_NEEDED
    if reply_state in (ReplyState.WAITING_FOR_THEM, ReplyState.NO_REPLY_NEEDED):
        if aggregated_importance in (MessageImportance.HIGH, MessageImportance.CRITICAL):
            return ThreadActionState.INFORMATIONAL
        return ThreadActionState.INFORMATIONAL
    if reply_state in (ReplyState.WAITING_FOR_US, ReplyState.OVERDUE_FOR_US, ReplyState.REPLY_RECOMMENDED_TODAY):
        return ThreadActionState.ACTIONABLE
    if reply_state == ReplyState.AMBIGUOUS:
        return ThreadActionState.BLOCKED
    if max_reply_requirement == ReplyRequirement.NO and not any_actionable:
        return ThreadActionState.INFORMATIONAL
    if not any_actionable:
        return ThreadActionState.WAITING
    return ThreadActionState.ACTIONABLE

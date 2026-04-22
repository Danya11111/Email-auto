from __future__ import annotations

from app.application.dtos import ActionCenterSnapshotDTO, DailyActionItemDTO, MessageThreadSummaryDTO
from app.domain.enums import ActionCenterCategory, ReplyState, ThreadActionState
from app.domain.models import TriageResult


def explain_action_item_lines(*, item: DailyActionItemDTO) -> list[str]:
    lines = [
        f"Action item: {item.item_id}",
        f"Source: {item.source_type}  Category: {item.category.value}  Priority score: {item.priority_score}",
        f"Title: {item.title}",
        f"Why (signals): {item.reason}",
    ]
    if item.signals:
        lines.append("Signals: " + "; ".join(item.signals))
    if item.reply_state is not None:
        lines.append(f"Reply state: {item.reply_state.value}")
    if item.thread_id:
        lines.append(f"Thread id: {item.thread_id}")
    if item.message_ids:
        lines.append("Message ids: " + ", ".join(f"m{mid}" for mid in item.message_ids))
    if item.task_id is not None:
        lines.append(f"Task id: t{item.task_id}")
    if item.review_id is not None:
        lines.append(f"Review id: r{item.review_id}")
    lines.append(f"Recommended next step: {item.recommended_next_step}")
    return lines


def explain_thread_lines(*, summary: MessageThreadSummaryDTO) -> list[str]:
    lines = [
        f"Thread id: {summary.thread_id}",
        f"Subject: {summary.subject_line}",
        f"Messages: {', '.join(f'm{mid}' for mid in summary.related_message_ids)}",
        f"Latest message at: {summary.latest_message_at.isoformat() if summary.latest_message_at else '(unknown)'}",
        f"Participants (sample): {', '.join(summary.participants) if summary.participants else '(none)'}",
        f"Aggregated importance: {summary.aggregated_importance.value}",
        f"Max reply requirement: {summary.max_reply_requirement.value}",
        f"Any actionable (triage): {summary.any_actionable}",
        f"Reply state (rules): {summary.reply_state.value}",
        f"Thread action state: {summary.thread_action_state.value}",
        f"Candidate tasks: {', '.join(f't{t}' for t in summary.candidate_task_ids) or 'none'}",
        f"Pending reviews: {', '.join(f'r{r}' for r in summary.pending_review_ids) or 'none'}",
        f"Include in action center: {summary.include_in_action_center}",
        f"Likely active: {summary.likely_active}",
    ]
    if summary.signals:
        lines.append("Signals: " + "; ".join(summary.signals))
    lines.append(
        "Notes: reply_state is heuristic from triage reply_requirement + recency + optional review ambiguity; "
        "not a guarantee that the other party already replied."
    )
    return lines


class ActionCenterSummaryLite:
    """Narrow projection for explain-message."""

    __slots__ = (
        "thread_id",
        "reply_state",
        "thread_action_state",
        "subject_line",
        "pending_review_ids",
        "candidate_task_ids",
    )

    def __init__(
        self,
        *,
        thread_id: str,
        reply_state: ReplyState,
        thread_action_state: ThreadActionState,
        subject_line: str,
        pending_review_ids: tuple[int, ...],
        candidate_task_ids: tuple[int, ...],
    ) -> None:
        self.thread_id = thread_id
        self.reply_state = reply_state
        self.thread_action_state = thread_action_state
        self.subject_line = subject_line
        self.pending_review_ids = pending_review_ids
        self.candidate_task_ids = candidate_task_ids


def explain_message_lines(
    *,
    message_id: int,
    triage: TriageResult | None,
    snapshot: ActionCenterSummaryLite | None,
) -> list[str]:
    lines = [f"Message id: m{message_id}"]
    if triage is None:
        lines.append("Triage: not found for this message (ingested but not triaged yet?).")
    else:
        lines.extend(
            [
                f"Triage importance: {triage.importance.value}",
                f"Triage reply requirement: {triage.reply_requirement.value}",
                f"Triage actionable: {triage.actionable}",
                f"Triage confidence: {triage.confidence:.2f}",
                f"Triage summary: {triage.summary}",
                f"Reason codes: {', '.join(triage.reason_codes) or '(none)'}",
            ]
        )
    if snapshot is None:
        lines.append(
            "Thread context: message not found in current action-center window (try ACTION_CENTER_LOOKBACK_HOURS)."
        )
    else:
        lines.extend(
            [
                f"Thread id: {snapshot.thread_id}",
                f"Thread reply state: {snapshot.reply_state.value}",
                f"Thread action state: {snapshot.thread_action_state.value}",
                f"Thread subject (normalized): {snapshot.subject_line}",
            ]
        )
        if snapshot.pending_review_ids:
            lines.append("Pending reviews on thread: " + ", ".join(f"r{r}" for r in snapshot.pending_review_ids))
        if snapshot.candidate_task_ids:
            lines.append("Candidate tasks on thread: " + ", ".join(f"t{t}" for t in snapshot.candidate_task_ids))
        lines.append(
            "Why it matters: derived from max importance/reply_requirement across messages in the thread cluster "
            "plus review/task pins (see ADR: thread-aware action center)."
        )
    return lines


def find_thread_summary_for_message(
    snapshot: ActionCenterSnapshotDTO, message_id: int
) -> MessageThreadSummaryDTO | None:
    for t in snapshot.threads:
        if message_id in t.related_message_ids:
            return t
    return None


def snapshot_lite_from_summary(summary: MessageThreadSummaryDTO) -> ActionCenterSummaryLite:
    return ActionCenterSummaryLite(
        thread_id=summary.thread_id,
        reply_state=summary.reply_state,
        thread_action_state=summary.thread_action_state,
        subject_line=summary.subject_line,
        pending_review_ids=summary.pending_review_ids,
        candidate_task_ids=summary.candidate_task_ids,
    )


def find_action_item(snapshot: ActionCenterSnapshotDTO, item_id: str) -> DailyActionItemDTO | None:
    for it in snapshot.items:
        if it.item_id == item_id:
            return it
    return None


def count_reply_critical_items(snapshot: ActionCenterSnapshotDTO) -> int:
    n = 0
    for it in snapshot.items:
        if it.category == ActionCenterCategory.REPLIES_NEEDED and it.reply_state in (
            ReplyState.OVERDUE_FOR_US,
            ReplyState.WAITING_FOR_US,
        ):
            n += 1
        if it.category == ActionCenterCategory.CRITICAL_TODAY:
            n += 1
    return n

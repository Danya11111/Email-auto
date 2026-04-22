from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.dtos import (
    ActionCenterCategorySectionDTO,
    ActionCenterRawBundleDTO,
    ActionCenterSnapshotDTO,
    ActionCenterTaskPinDTO,
    DailyActionItemDTO,
    MessageThreadSummaryDTO,
)
from app.application.reply_state_rules import infer_reply_state, max_importance, max_reply_requirement
from app.application.thread_action_state_rules import infer_thread_action_state
from app.application.thread_grouping import cluster_messages_into_threads
from app.application.thread_subject import normalize_subject
from app.config import AppSettings
from app.domain.enums import (
    ActionCenterCategory,
    MessageImportance,
    ReplyRequirement,
    ReplyState,
    TaskStatus,
    ThreadActionState,
)


def _importance_score(imp: MessageImportance) -> int:
    return {
        MessageImportance.LOW: 10,
        MessageImportance.MEDIUM: 40,
        MessageImportance.HIGH: 70,
        MessageImportance.CRITICAL: 100,
    }[imp]


def _reply_boost(req: ReplyRequirement) -> int:
    if req in (ReplyRequirement.REQUIRED, ReplyRequirement.URGENT):
        return 55
    if req == ReplyRequirement.OPTIONAL:
        return 20
    return 0


def score_thread_item(
    *,
    summary: MessageThreadSummaryDTO,
    reply_state: ReplyState,
) -> int:
    s = _importance_score(summary.aggregated_importance)
    s += _reply_boost(summary.max_reply_requirement)
    if summary.pending_review_ids:
        s += 35
    if summary.candidate_task_ids:
        s += 18
    if summary.max_candidate_task_confidence >= 0.85:
        s += 12
    if reply_state == ReplyState.OVERDUE_FOR_US:
        s += 40
    elif reply_state in (ReplyState.WAITING_FOR_US, ReplyState.REPLY_RECOMMENDED_TODAY):
        s += 15
    return s


def _pick_category(
    *,
    summary: MessageThreadSummaryDTO,
    reply_state: ReplyState,
    include_informational: bool,
) -> ActionCenterCategory | None:
    if summary.pending_review_ids:
        return ActionCenterCategory.REVIEW_REQUIRED
    if reply_state in (ReplyState.OVERDUE_FOR_US, ReplyState.WAITING_FOR_US, ReplyState.REPLY_RECOMMENDED_TODAY):
        return ActionCenterCategory.REPLIES_NEEDED
    if reply_state == ReplyState.WAITING_FOR_THEM:
        return ActionCenterCategory.WAITING_OR_BLOCKED
    if summary.aggregated_importance == MessageImportance.CRITICAL and summary.include_in_action_center:
        return ActionCenterCategory.CRITICAL_TODAY
    if summary.aggregated_importance == MessageImportance.HIGH and summary.include_in_action_center:
        return ActionCenterCategory.CRITICAL_TODAY
    if summary.candidate_task_ids and summary.include_in_action_center:
        return ActionCenterCategory.TASKS_APPROVE_OR_SYNC
    if include_informational and summary.thread_action_state == ThreadActionState.INFORMATIONAL:
        return ActionCenterCategory.INFORMATIONAL
    return None


def build_action_center_snapshot(
    bundle: ActionCenterRawBundleDTO,
    *,
    settings: AppSettings,
    now: datetime,
) -> ActionCenterSnapshotDTO:
    window_td = timedelta(hours=float(settings.thread_grouping_time_window_hours))
    clusters = cluster_messages_into_threads(bundle.messages, time_window=window_td)
    msg_by_id = {m.message_id: m for m in bundle.messages}
    tasks_by_msg: dict[int, list[ActionCenterTaskPinDTO]] = {}
    for tp in bundle.task_pins:
        tasks_by_msg.setdefault(tp.message_id, []).append(tp)

    thread_reviews: dict[str, list[int]] = {tid: [] for tid in clusters}
    for tid, mids in clusters.items():
        midset = set(mids)
        task_ids_for_thread = {tp.task_id for mid in mids for tp in tasks_by_msg.get(mid, [])}
        for rev in bundle.pending_reviews:
            if rev.message_id in midset or (rev.task_id is not None and rev.task_id in task_ids_for_thread):
                thread_reviews[tid].append(rev.review_id)

    overdue_td = timedelta(hours=float(settings.reply_overdue_hours))
    recommended_td = timedelta(hours=float(settings.reply_recommended_hours))

    threads: list[MessageThreadSummaryDTO] = []
    items: list[DailyActionItemDTO] = []

    for tid, mids in clusters.items():
        rows = [msg_by_id[i] for i in mids if i in msg_by_id]
        if not rows:
            continue
        latest = max((r.received_at for r in rows if r.received_at is not None), default=None)
        participants: set[str] = set()
        for r in rows:
            if r.sender:
                participants.add(r.sender.strip())
            participants.update(x.strip() for x in r.recipients if x.strip())
        imp = MessageImportance.LOW
        rep = ReplyRequirement.NO
        actionable_any = False
        for r in rows:
            imp = max_importance(imp, r.importance)
            rep = max_reply_requirement(rep, r.reply_requirement)
            actionable_any = actionable_any or r.actionable
        subj = next((normalize_subject(r.subject) for r in sorted(rows, key=lambda x: x.message_id, reverse=True)), "")
        display_subj = subj or "(no subject)"
        cand_tasks = tuple(sorted({t.task_id for mid in mids for t in tasks_by_msg.get(mid, []) if t.status == TaskStatus.CANDIDATE}))
        max_cand_conf = max(
            (t.confidence for mid in mids for t in tasks_by_msg.get(mid, []) if t.status == TaskStatus.CANDIDATE),
            default=0.0,
        )
        rev_ids = tuple(sorted(set(thread_reviews.get(tid, []))))
        has_rev = bool(rev_ids)
        rs = infer_reply_state(
            max_reply_requirement=rep,
            any_actionable=actionable_any,
            latest_message_at=latest,
            now=now.astimezone(UTC),
            overdue_after=overdue_td,
            recommended_within=recommended_td,
            has_pending_review=has_rev and settings.action_center_require_review_for_ambiguous_reply,
        )
        if has_rev and not settings.action_center_require_review_for_ambiguous_reply:
            rs = infer_reply_state(
                max_reply_requirement=rep,
                any_actionable=actionable_any,
                latest_message_at=latest,
                now=now.astimezone(UTC),
                overdue_after=overdue_td,
                recommended_within=recommended_td,
                has_pending_review=False,
            )
        tas = infer_thread_action_state(
            aggregated_importance=imp,
            max_reply_requirement=rep,
            any_actionable=actionable_any,
            has_pending_review=has_rev,
            reply_state=rs,
        )
        signals: list[str] = [
            f"importance_max={imp.value}",
            f"reply_max={rep.value}",
            f"actionable_any={actionable_any}",
        ]
        if latest:
            signals.append(f"latest_message_at={latest.isoformat()}")
        include_ac = tas in (
            ThreadActionState.ACTIONABLE,
            ThreadActionState.REVIEW_NEEDED,
            ThreadActionState.BLOCKED,
            ThreadActionState.WAITING,
        ) or imp in (MessageImportance.HIGH, MessageImportance.CRITICAL)
        if settings.action_center_include_informational and tas == ThreadActionState.INFORMATIONAL and imp == MessageImportance.LOW:
            include_ac = True
        summary = MessageThreadSummaryDTO(
            thread_id=tid,
            related_message_ids=tuple(sorted(mids)),
            latest_message_at=latest,
            participants=tuple(sorted(participants))[:12],
            subject_line=display_subj[:200],
            aggregated_importance=imp,
            max_reply_requirement=rep,
            any_actionable=actionable_any,
            reply_state=rs,
            thread_action_state=tas,
            candidate_task_ids=cand_tasks,
            pending_review_ids=tuple(rev_ids),
            likely_active=latest is not None and (now.astimezone(UTC) - (latest.astimezone(UTC))).days <= 14,
            include_in_action_center=include_ac,
            max_candidate_task_confidence=float(max_cand_conf),
            signals=tuple(signals),
        )
        threads.append(summary)

        if not include_ac:
            continue
        cat = _pick_category(
            summary=summary,
            reply_state=rs,
            include_informational=settings.action_center_include_informational,
        )
        if cat is None:
            continue
        score = score_thread_item(summary=summary, reply_state=rs)
        reason = f"{rs.value}; triage reply≤{rep.value}; tasks={len(cand_tasks)}; reviews={len(rev_ids)}"
        step = "Run review-list / triage inbox or reply in mail client."
        if rev_ids:
            step = f"Resolve reviews: {', '.join(f'r{r}' for r in rev_ids[:5])}"
        elif cand_tasks:
            step = f"Approve candidate tasks: {', '.join(f't{t}' for t in cand_tasks[:5])}"
        elif rs in (ReplyState.WAITING_FOR_US, ReplyState.OVERDUE_FOR_US):
            step = "Draft a reply (reply requirement indicates you owe a response)."
        items.append(
            DailyActionItemDTO(
                item_id=f"ac:thread:{tid}",
                source_type="thread",
                category=cat,
                priority_score=score,
                title=f"Thread: {display_subj[:120]}",
                reason=reason,
                recommended_next_step=step,
                thread_id=tid,
                message_ids=summary.related_message_ids,
                reply_state=rs,
                signals=tuple(signals),
            )
        )

    for rev in bundle.pending_reviews:
        if any(rev.review_id in t.pending_review_ids for t in threads):
            continue
        items.append(
            DailyActionItemDTO(
                item_id=f"ac:review:{rev.review_id}",
                source_type="review",
                category=ActionCenterCategory.REVIEW_REQUIRED,
                priority_score=60 + int(rev.confidence * 10),
                title=f"Review {rev.review_kind.value} m{rev.message_id}",
                reason=rev.reason_text[:200],
                recommended_next_step=f"Run review-approve/reject for r{rev.review_id}",
                review_id=rev.review_id,
                message_ids=(rev.message_id,),
                signals=(f"reason_code={rev.reason_code}",),
            )
        )

    for f in bundle.kanban_failures:
        items.append(
            DailyActionItemDTO(
                item_id=f"ac:kanban:{f.sync_record_id}",
                source_type="kanban_sync",
                category=ActionCenterCategory.TASKS_APPROVE_OR_SYNC,
                priority_score=45,
                title=f"Kanban sync failed t{f.task_id} ({f.provider})",
                reason=(f.last_error or "")[:240],
                recommended_next_step="Run kanban-show-task-sync --task-id {tid} then kanban-retry-failed.".format(tid=f.task_id),
                task_id=f.task_id,
                signals=("kanban_sync_failed",),
            )
        )

    if bundle.approved_ready_to_sync > 0:
        items.append(
            DailyActionItemDTO(
                item_id="ac:kanban:approved_ready",
                source_type="kanban_queue",
                category=ActionCenterCategory.TASKS_APPROVE_OR_SYNC,
                priority_score=30 + min(40, bundle.approved_ready_to_sync * 2),
                title=f"{bundle.approved_ready_to_sync} approved task(s) ready for kanban sync",
                reason="Approved tasks exist without synced kanban record for active provider.",
                recommended_next_step="Run kanban-preview then kanban-sync (or enable KANBAN_AUTO_SYNC).",
                signals=("approved_ready_to_sync",),
            )
        )

    if bundle.manual_resync_backlog > 0:
        items.append(
            DailyActionItemDTO(
                item_id="ac:kanban:manual_resync",
                source_type="kanban_queue",
                category=ActionCenterCategory.TASKS_APPROVE_OR_SYNC,
                priority_score=38 + min(30, bundle.manual_resync_backlog * 3),
                title=f"{bundle.manual_resync_backlog} manual YouGile resync item(s)",
                reason="Fingerprint drift with YOUGILE_ENABLE_UPDATE_EXISTING=false (audit trail).",
                recommended_next_step="Enable updates or run kanban-resync-changed after policy change.",
                signals=("manual_resync_backlog",),
            )
        )

    items.sort(key=lambda i: (-i.priority_score, i.item_id))
    max_items = int(settings.action_center_max_items)
    items = items[:max_items]

    buckets: dict[ActionCenterCategory, list[DailyActionItemDTO]] = {c: [] for c in ActionCenterCategory}
    for it in items:
        buckets.setdefault(it.category, []).append(it)

    sections: list[ActionCenterCategorySectionDTO] = []
    for cat in (
        ActionCenterCategory.CRITICAL_TODAY,
        ActionCenterCategory.REPLIES_NEEDED,
        ActionCenterCategory.TASKS_APPROVE_OR_SYNC,
        ActionCenterCategory.WAITING_OR_BLOCKED,
        ActionCenterCategory.REVIEW_REQUIRED,
        ActionCenterCategory.INFORMATIONAL,
    ):
        sec_items = tuple(buckets.get(cat, ()))
        if sec_items:
            sections.append(ActionCenterCategorySectionDTO(category=cat, items=sec_items))

    return ActionCenterSnapshotDTO(
        window_start=bundle.window_start,
        window_end=bundle.window_end,
        threads=tuple(sorted(threads, key=lambda t: t.thread_id)),
        items=tuple(items),
        category_sections=tuple(sections),
    )


def build_executive_summary_lines(
    snapshot: ActionCenterSnapshotDTO, *, stats_line: str, max_items: int = 4
) -> tuple[str, ...]:
    """Deterministic executive bullets (no LLM)."""
    lines: list[str] = [stats_line]
    top = snapshot.items[: max(1, int(max_items))]
    if not top:
        lines.append("No high-priority action center items in this window.")
        return tuple(lines)
    for it in top:
        lines.append(f"- ({it.category.value}) {it.title} — {it.recommended_next_step}")
    return tuple(lines)

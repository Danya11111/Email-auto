from __future__ import annotations

from app.application.digest_compose_options import DigestComposeOptions
from app.application.dtos import DailyDigestContextDTO
from app.domain.enums import ActionCenterCategory, MessageImportance, ReplyRequirement, ReplyState


def compose_daily_digest_markdown(
    *,
    ctx: DailyDigestContextDTO,
    pipeline_notes: dict[str, object],
    options: DigestComposeOptions | None = None,
) -> str:
    """Deterministic digest layout (no LLM), optimized for quick human scanning."""
    opt = options or DigestComposeOptions()
    cap_msgs = 10 if opt.compact else 25
    cap_tasks = 15 if opt.compact else 50
    cap_ac = 6 if opt.compact else 12

    ws = ctx.window_start.isoformat()
    we = ctx.window_end.isoformat()

    critical = [m for m in ctx.messages if m.importance in {MessageImportance.HIGH, MessageImportance.CRITICAL}]
    reply_needed = [m for m in ctx.messages if m.reply_requirement in {ReplyRequirement.REQUIRED, ReplyRequirement.URGENT}]

    lines: list[str] = []
    lines.append(f"# Morning digest ({ws} → {we})")
    lines.append("")

    lines.append("## Executive summary")
    if ctx.executive_summary_lines:
        for ln in ctx.executive_summary_lines:
            lines.append(ln)
    else:
        lines.append(
            f"- Messages in window: **{ctx.stats.messages_in_window}** (capped for display: **{ctx.stats.messages_capped}**)"
        )
        lines.append(f"- Pending manual reviews: **{ctx.stats.pending_reviews}**")
        lines.append(f"- Candidate tasks (candidate status): **{ctx.stats.candidate_tasks}**")
        if critical:
            lines.append(f"- High/critical items: **{len(critical)}**")
        if reply_needed:
            lines.append(f"- Reply-sensitive items: **{len(reply_needed)}**")
    lines.append("")

    rd = ctx.reply_draft_digest
    if rd is not None and (rd.needing_draft or rd.ready_for_review or rd.stale or rd.approved_not_exported):
        lines.append("## Reply draft workload")
        if rd.needing_draft:
            lines.append("### Replies needing draft")
            for ln in rd.needing_draft[:8]:
                lines.append(f"- {ln}")
            lines.append("")
        if rd.ready_for_review:
            lines.append("### Reply drafts ready for review")
            for ln in rd.ready_for_review[:8]:
                lines.append(f"- {ln}")
            lines.append("")
        if rd.stale:
            lines.append("### Stale reply drafts")
            for ln in rd.stale[:8]:
                lines.append(f"- {ln}")
            lines.append("")
        if rd.approved_not_exported:
            lines.append("### Approved replies not yet exported")
            for ln in rd.approved_not_exported[:8]:
                lines.append(f"- {ln}")
            lines.append("")

    ac = ctx.action_center
    if ac is not None:
        lines.append("## Today's action center")
        if not ac.category_sections:
            lines.append("- No grouped action items in this window (see `action-center` CLI for live build).")
        else:
            for sec in ac.category_sections:
                if sec.category == ActionCenterCategory.INFORMATIONAL and not opt.include_informational:
                    continue
                lines.append(f"### {sec.category.value.replace('_', ' ').title()}")
                for it in sec.items[:cap_ac]:
                    rs = f" reply={it.reply_state.value}" if it.reply_state else ""
                    lines.append(
                        f"- **{it.item_id}** ({it.source_type}) score={it.priority_score}{rs} — {it.title}"
                    )
                    lines.append(f"  - Next: {it.recommended_next_step}")
                if len(sec.items) > cap_ac:
                    lines.append(f"  - _…and {len(sec.items) - cap_ac} more in this bucket_")
                lines.append("")
        lines.append("")

        lines.append("## Replies needing attention")
        reply_items = [
            it
            for sec in ac.category_sections
            if sec.category == ActionCenterCategory.REPLIES_NEEDED
            for it in sec.items
        ]
        if not reply_items:
            lines.append("- None in action-center ranking (still check per-message reply list below).")
        else:
            for it in reply_items[:cap_msgs]:
                lines.append(f"- **{it.item_id}** — {it.title} — {it.reason}")
        lines.append("")

        lines.append("## High-priority active threads")
        hot_threads = [t for t in ac.threads if t.aggregated_importance in (MessageImportance.HIGH, MessageImportance.CRITICAL)]
        if not hot_threads:
            lines.append("- None in thread window.")
        else:
            for t in sorted(hot_threads, key=lambda x: x.thread_id)[:cap_msgs]:
                lines.append(
                    f"- **{t.thread_id}** — {t.subject_line[:120]} — importance={t.aggregated_importance.value} "
                    f"reply_state={t.reply_state.value} messages={len(t.related_message_ids)}"
                )
        lines.append("")

        lines.append("## Waiting on others")
        wait_threads = [t for t in ac.threads if t.reply_state == ReplyState.WAITING_FOR_THEM]
        if not wait_threads:
            lines.append("- None classified as waiting_for_them in this window.")
        else:
            for t in wait_threads[:cap_msgs]:
                lines.append(f"- **{t.thread_id}** — {t.subject_line[:120]} — last={t.latest_message_at}")
        lines.append("")

    lines.append("## Tasks ready for approval / sync")
    if not ctx.candidate_tasks:
        lines.append("- None")
    else:
        for t in ctx.candidate_tasks[:cap_tasks]:
            lines.append(
                f"- **t{t.task_id}** (m{t.message_id}) conf={t.confidence:.2f} — {t.title}"
                + (f" — due `{t.due_at}`" if t.due_at else "")
            )
    lines.append("")

    lines.append("## Items requiring manual review")
    if not ctx.pending_reviews:
        lines.append("- None")
    else:
        for r in ctx.pending_reviews[:cap_tasks]:
            lines.append(
                f"- **r{r.review_id}** [{r.review_kind}] m{r.message_id}"
                + (f" t{r.task_id}" if r.task_id is not None else "")
                + f" — `{r.reason_code}` — conf={r.confidence:.2f} — {r.reason_text}"
            )
    lines.append("")

    lines.append("## Kanban sync")
    kb = ctx.kanban
    if kb is None:
        lines.append("- Kanban digest stats unavailable (no sync repository wired for this run).")
    else:
        lines.append(
            f"- Provider: **{kb.provider}**; auto-sync: **{'on' if kb.auto_sync_enabled else 'off'}**"
        )
        lines.append(
            f"- Approved ready to sync: **{kb.approved_ready_to_sync}**; "
            f"outbox pending: **{kb.pending_outbox}**; synced records: **{kb.synced}**; failed: **{kb.failed}**"
        )
        if kb.outbound_updates_last_24h or kb.manual_resync_pending:
            lines.append(
                f"- Recent outbound writes (24h): **{kb.outbound_updates_last_24h}**; "
                f"manual resync backlog: **{kb.manual_resync_pending}**"
            )
        if kb.failed:
            lines.append(f"- **Failed sync count: {kb.failed}** — inspect `kanban-retry-failed` / recent errors below.")
        if kb.manual_resync_pending:
            lines.append(
                "- **Manual resync backlog** — fingerprint drift with updates disabled; see `kanban-resync-changed` / README."
            )
        if "yougile" in str(kb.provider).lower():
            lines.append(
                "- YouGile: use `kanban-status` for column/update policy; `kanban-resync-changed` for drift-only updates."
            )
        if kb.recent_errors:
            lines.append("- Recent sync errors:")
            for err in kb.recent_errors[:5]:
                lines.append(f"  - `{err[:200]}`")
        else:
            lines.append("- Recent sync errors: none")
    lines.append("")

    lines.append("## Critical / High priority messages (per-message)")
    if not critical:
        lines.append("- None")
    else:
        for m in critical[:cap_msgs]:
            lines.append(f"- **m{m.message_id}** — {m.subject or '(no subject)'} — from `{m.sender or ''}` — {m.importance}")
    lines.append("")

    lines.append("## Messages requiring reply (triage flags)")
    if not reply_needed:
        lines.append("- None")
    else:
        for m in reply_needed[:cap_msgs]:
            lines.append(
                f"- **m{m.message_id}** — {m.subject or '(no subject)'} — reply={m.reply_requirement} — {m.triage_summary}"
            )
    lines.append("")

    lines.append("## Pipeline stats / system notes")
    if not pipeline_notes:
        lines.append("- None")
    else:
        for k in sorted(pipeline_notes.keys(), key=str):
            lines.append(f"- **{k}**: {pipeline_notes[k]}")
    lines.append("")
    lines.append("_Generated locally. Thread grouping is best-effort; verify critical actions before sending._")
    lines.append("")
    return "\n".join(lines)


def compose_action_center_markdown_export(*, ctx: DailyDigestContextDTO) -> str:
    """Markdown snapshot suitable for daily archive / notes (uses embedded action center if present)."""
    ac = ctx.action_center
    lines = [
        f"# Action center export ({ctx.window_start.isoformat()} → {ctx.window_end.isoformat()})",
        "",
    ]
    if ac is None:
        lines.append("_No action center snapshot attached._")
        return "\n".join(lines) + "\n"
    for sec in ac.category_sections:
        lines.append(f"## {sec.category.value}")
        for it in sec.items:
            lines.append(f"- **{it.item_id}** (score {it.priority_score}) — {it.title}")
            lines.append(f"  - Why: {it.reason}")
            lines.append(f"  - Next: {it.recommended_next_step}")
        lines.append("")
    return "\n".join(lines)

from __future__ import annotations

from app.application.dtos import DailyDigestContextDTO
from app.domain.enums import MessageImportance, ReplyRequirement


def compose_daily_digest_markdown(*, ctx: DailyDigestContextDTO, pipeline_notes: dict[str, object]) -> str:
    """Deterministic digest layout (no LLM), optimized for quick human scanning."""

    ws = ctx.window_start.isoformat()
    we = ctx.window_end.isoformat()

    critical = [m for m in ctx.messages if m.importance in {MessageImportance.HIGH, MessageImportance.CRITICAL}]
    reply_needed = [m for m in ctx.messages if m.reply_requirement in {ReplyRequirement.REQUIRED, ReplyRequirement.URGENT}]

    lines: list[str] = []
    lines.append(f"# Morning digest ({ws} → {we})")
    lines.append("")
    lines.append("## Executive summary")
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

    lines.append("## Critical / High priority messages")
    if not critical:
        lines.append("- None")
    else:
        for m in critical[:25]:
            lines.append(f"- **m{m.message_id}** — {m.subject or '(no subject)'} — from `{m.sender or ''}` — {m.importance}")
    lines.append("")

    lines.append("## Messages requiring reply")
    if not reply_needed:
        lines.append("- None")
    else:
        for m in reply_needed[:25]:
            lines.append(
                f"- **m{m.message_id}** — {m.subject or '(no subject)'} — reply={m.reply_requirement} — {m.triage_summary}"
            )
    lines.append("")

    lines.append("## Candidate tasks")
    if not ctx.candidate_tasks:
        lines.append("- None")
    else:
        for t in ctx.candidate_tasks[:50]:
            lines.append(
                f"- **t{t.task_id}** (m{t.message_id}) conf={t.confidence:.2f} — {t.title}"
                + (f" — due `{t.due_at}`" if t.due_at else "")
            )
    lines.append("")

    lines.append("## Items requiring manual review")
    if not ctx.pending_reviews:
        lines.append("- None")
    else:
        for r in ctx.pending_reviews[:50]:
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
        if kb.provider == "yougile":
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

    lines.append("## Pipeline stats / system notes")
    if not pipeline_notes:
        lines.append("- None")
    else:
        for k in sorted(pipeline_notes.keys(), key=str):
            lines.append(f"- **{k}**: {pipeline_notes[k]}")
    lines.append("")
    lines.append("_Generated locally. Triage/task extraction may still be model-dependent; verify critical actions._")
    lines.append("")
    return "\n".join(lines)

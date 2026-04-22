from __future__ import annotations

from app.application.dtos import ReplyDraftContextDTO
from app.domain.reply_draft import ReplyDraft


def explain_reply_draft_lines(
    *,
    draft: ReplyDraft,
    context: ReplyDraftContextDTO | None = None,
) -> tuple[str, ...]:
    lines: list[str] = [
        f"Reply draft d{draft.id}",
        f"- thread_id: {draft.thread_id}",
        f"- status: {draft.status.value}",
        f"- tone: {draft.tone.value}",
        f"- generation_mode: {draft.generation_mode.value}",
        f"- fingerprint: {draft.generation_fingerprint}",
        f"- model: {draft.model_name or '(unknown)'}",
        f"- generated_at: {draft.generated_at.isoformat()}",
    ]
    if draft.approved_at:
        lines.append(f"- approved_at: {draft.approved_at.isoformat()}")
    if draft.exported_at:
        lines.append(f"- exported_at: {draft.exported_at.isoformat()}")
    lines.append("")
    lines.append("Why this draft exists:")
    lines.append(f"- short_rationale: {draft.short_rationale}")
    lines.append(f"- fact_boundary_note: {draft.fact_boundary_note}")
    lines.append("")
    lines.append("Sources embedded in the draft record:")
    lines.append(f"- message ids: {', '.join(f'm{x}' for x in draft.source_message_ids)}")
    lines.append(f"- task ids: {', '.join(f't{x}' for x in draft.source_task_ids) or '(none)'}")
    lines.append(f"- review ids: {', '.join(f'r{x}' for x in draft.source_review_ids) or '(none)'}")
    if context is not None:
        lines.append("")
        lines.append("Context pack used for generation (bounded excerpts):")
        lines.append(f"- normalized_subject: {context.normalized_subject}")
        lines.append(f"- reply_state: {context.reply_state.value}")
        lines.append(f"- primary_message_id: m{context.primary_message_id}")
        lines.append(f"- messages_included: {', '.join(f'm{m.message_id}' for m in context.messages_included)}")
        if context.action_center_next_step:
            lines.append(f"- action_center_next_step: {context.action_center_next_step}")
        lines.append(f"- context_char_estimate: {context.context_char_estimate}")
    lines.append("")
    lines.append("If status is stale: thread fingerprint changed vs generation_fingerprint (new messages/reviews/tasks).")
    return tuple(lines)

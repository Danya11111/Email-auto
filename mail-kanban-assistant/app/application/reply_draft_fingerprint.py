from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.dtos import ReplyDraftContextDTO


def fingerprint_for_reply_context(ctx: ReplyDraftContextDTO) -> str:
    """Deterministic fingerprint: thread shape + triage pins + included ids (no raw bodies)."""
    payload: dict[str, Any] = {
        "thread_id": ctx.thread_id,
        "primary_message_id": ctx.primary_message_id,
        "reply_state": ctx.reply_state.value,
        "subject": ctx.normalized_subject,
        "msg_ids": [m.message_id for m in ctx.messages_included],
        "msg_times": [(m.message_id, m.received_at.isoformat() if m.received_at else None) for m in ctx.messages_included],
        "triage_reply": ctx.triage_reply_requirement.value if ctx.triage_reply_requirement else None,
        "triage_imp": ctx.triage_importance.value if ctx.triage_importance else None,
        "triage_summary_head": (ctx.triage_summary_primary or "")[:240],
        "tasks": list(ctx.extracted_task_points),
        "task_ids": list(ctx.source_task_ids),
        "review_ids": list(ctx.source_review_ids),
        "reviews": list(ctx.pending_review_notes),
        "deadlines": list(ctx.deadlines),
        "ac_step": (ctx.action_center_next_step or "")[:200],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

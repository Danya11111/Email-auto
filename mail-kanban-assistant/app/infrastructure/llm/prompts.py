from __future__ import annotations

TRIAGE_SYSTEM = (
    "You triage ONE email. Output a single JSON object matching the schema. "
    "Be conservative: actionable=true only for concrete requests/deadlines/follow-ups."
)

TASK_EXTRACTION_SYSTEM = (
    "You extract actionable tasks from ONE email. Output JSON with a tasks array (possibly empty). "
    "Tasks must be specific and checkable."
)

DIGEST_SYSTEM = (
    "You write a compact JSON object with a markdown field. Do not invent emails not present in the payload."
)

REPLY_DRAFT_SYSTEM = (
    "You draft ONE business email reply from structured context JSON. Output a single JSON object matching the schema. "
    "Be conservative: do not invent facts, dates, amounts, or commitments absent from context. "
    "If information is missing, list it in missing_information and keep body_text safe and short. "
    "Honor requested tone without adding boilerplate disclaimers."
)


def triage_user_prompt(subject: str | None, sender: str | None, *, body_excerpt: str) -> str:
    return (
        "Email:\n"
        f"Subject: {subject or '(none)'}\n"
        f"From: {sender or '(unknown)'}\n\n"
        f"Body:\n{body_excerpt}\n"
    )


def task_extraction_user_prompt(subject: str | None, sender: str | None, *, triage_summary: str, body_excerpt: str) -> str:
    return (
        "Context:\n"
        f"Subject: {subject or '(none)'}\n"
        f"From: {sender or '(unknown)'}\n"
        f"Triage summary: {triage_summary}\n\n"
        f"Body:\n{body_excerpt}\n"
    )


def digest_user_prompt(payload_json: str) -> str:
    return "Digest payload (JSON):\n" + payload_json


def reply_draft_user_prompt(*, context_json: str, tone: str, reply_state: str) -> str:
    return f"Tone: {tone}\nReplyState: {reply_state}\nContext JSON:\n{context_json}"

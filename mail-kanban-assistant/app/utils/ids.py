from __future__ import annotations

import uuid


def new_run_id() -> str:
    return str(uuid.uuid4())


def stable_dedupe_key_for_incoming(
    *,
    source: str,
    rfc_message_id: str | None,
    subject: str | None,
    sender: str | None,
    received_at_iso: str | None,
    body_plain: str,
) -> str:
    """Deterministic dedupe when Message-Id is missing."""

    if rfc_message_id:
        return f"{source}:{rfc_message_id.strip()}"

    import hashlib

    payload = "|".join(
        [
            source,
            subject or "",
            sender or "",
            received_at_iso or "",
            body_plain[:32_000],
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"{source}:sha256:{digest}"

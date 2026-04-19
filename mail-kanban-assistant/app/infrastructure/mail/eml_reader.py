from __future__ import annotations

import email.policy
from email import message_from_bytes
from email.message import Message as EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Sequence

from app.application.dtos import IncomingMessageDTO
from app.application.ports import MessageReaderPort
from app.domain.enums import MessageSource
from app.utils.ids import stable_dedupe_key_for_incoming
from app.utils.text import normalize_mail_body


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _decode_payload(msg: EmailMessage) -> str:
    if msg.is_multipart():
        parts: list[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")).lower():
                try:
                    parts.append(_as_text(part.get_content()))
                except Exception:  # noqa: BLE001
                    continue
        return normalize_mail_body("\n".join(parts))

    if msg.get_content_type() == "text/plain":
        try:
            return normalize_mail_body(_as_text(msg.get_content()))
        except Exception:  # noqa: BLE001
            return ""

    try:
        return normalize_mail_body(_as_text(msg.get_content()))
    except Exception:  # noqa: BLE001
        return ""


def _first_addr(msg: EmailMessage, header: str) -> str | None:
    raw = msg.get(header)
    if not raw:
        return None
    pairs = getaddresses([raw])
    if not pairs:
        return None
    name, addr = pairs[0]
    if addr:
        return addr
    return name or None


def _all_addrs(msg: EmailMessage, header: str) -> tuple[str, ...]:
    raw = msg.get(header)
    if not raw:
        return ()
    pairs = getaddresses([raw])
    out: list[str] = []
    for name, addr in pairs:
        if addr:
            out.append(addr)
        elif name:
            out.append(name)
    return tuple(out)


def _parse_date(msg: EmailMessage):
    date_hdr = msg.get("Date")
    if not date_hdr:
        return None
    try:
        return parsedate_to_datetime(date_hdr)
    except (TypeError, ValueError, OverflowError):
        return None


def _thread_hint(msg: EmailMessage) -> str | None:
    in_reply_to = msg.get("In-Reply-To")
    references = msg.get("References")
    if in_reply_to:
        return str(in_reply_to).strip().strip("<>")
    if references:
        parts = str(references).split()
        if parts:
            return parts[0].strip().strip("<>")
    return None


def _rfc_message_id(msg: EmailMessage) -> str | None:
    mid = msg.get("Message-ID")
    if not mid:
        return None
    return str(mid).strip().strip("<>")


class EmlDirectoryReader(MessageReaderPort):
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def read_messages(self) -> Sequence[IncomingMessageDTO]:
        if not self._directory.exists():
            return ()

        out: list[IncomingMessageDTO] = []
        for path in sorted(self._directory.rglob("*.eml")):
            raw = path.read_bytes()
            parsed: EmailMessage = message_from_bytes(raw, policy=email.policy.default)
            body = _decode_payload(parsed)
            rfc_id = _rfc_message_id(parsed)
            received = _parse_date(parsed)
            dedupe = stable_dedupe_key_for_incoming(
                source=MessageSource.EML.value,
                rfc_message_id=rfc_id,
                subject=parsed.get("Subject"),
                sender=_first_addr(parsed, "From"),
                received_at_iso=received.isoformat() if received else None,
                body_plain=body,
            )
            out.append(
                IncomingMessageDTO(
                    dedupe_key=dedupe,
                    source=MessageSource.EML,
                    rfc_message_id=rfc_id,
                    subject=parsed.get("Subject"),
                    sender=_first_addr(parsed, "From"),
                    recipients=_all_addrs(parsed, "To") + _all_addrs(parsed, "Cc"),
                    received_at=received,
                    body_plain=body,
                    thread_hint=_thread_hint(parsed),
                    source_path=str(path),
                )
            )
        return tuple(out)

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.application.dtos import IncomingMessageDTO
from app.domain.enums import MessageSource


class AppleMailDropSnapshotFile(BaseModel):
    """Validated on-disk JSON format produced by macOS Mail automation (drop folder).

    This is intentionally not RFC822: it is a stable, local-first interchange format.
    """

    model_config = {"extra": "ignore"}

    snapshot_id: str = Field(min_length=1, max_length=256)
    source: Literal["apple_mail_drop"]
    message_id: str = Field(min_length=1, max_length=512)
    thread_id: str | None = Field(default=None, max_length=512)
    mailbox_name: str | None = Field(default=None, max_length=512)
    account_name: str | None = Field(default=None, max_length=512)
    subject: str | None = Field(default=None, max_length=4096)
    sender_name: str | None = Field(default=None, max_length=1024)
    sender_email: str | None = Field(default=None, max_length=512)
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    date: datetime | None = None
    body_text: str = Field(min_length=1)
    body_preview: str | None = Field(default=None, max_length=4096)
    unread: bool | None = None
    flagged: bool | None = None
    received_at: datetime | None = None
    collected_at: datetime
    attachments_summary: list[dict[str, Any]] | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("to", "cc", "bcc", mode="before")
    @classmethod
    def _coerce_recipient_lists(cls, value: object) -> object:
        if value is None:
            return []
        return value

    @field_validator("source")
    @classmethod
    def _source_must_be_drop(cls, value: object) -> object:
        if str(value) != "apple_mail_drop":
            raise ValueError("source must be 'apple_mail_drop'")
        return value


def snapshot_to_incoming_message_dto(*, snapshot: AppleMailDropSnapshotFile, source_path: str) -> IncomingMessageDTO:
    """Map a validated snapshot into the existing ingestion DTO contract."""

    sender = snapshot.sender_email or snapshot.sender_name
    recipients = tuple(snapshot.to + snapshot.cc + snapshot.bcc)
    received = snapshot.received_at or snapshot.date or snapshot.collected_at
    dedupe_key = f"{MessageSource.APPLE_MAIL_DROP.value}:{snapshot.message_id.strip()}"

    return IncomingMessageDTO(
        dedupe_key=dedupe_key,
        source=MessageSource.APPLE_MAIL_DROP,
        rfc_message_id=snapshot.message_id.strip(),
        subject=snapshot.subject,
        sender=sender,
        recipients=recipients,
        received_at=received,
        body_plain=snapshot.body_text,
        thread_hint=snapshot.thread_id,
        source_path=source_path,
    )

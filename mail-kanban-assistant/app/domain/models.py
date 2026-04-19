from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from app.domain.enums import (
    MessageImportance,
    MessageProcessingStatus,
    MessageSource,
    ReplyRequirement,
    ReviewKind,
    ReviewStatus,
    TaskStatus,
)

MessageId = NewType("MessageId", str)
ThreadId = NewType("ThreadId", str)
ReviewItemId = NewType("ReviewItemId", int)


@dataclass(frozen=True, slots=True)
class Message:
    """Normalized mail message as a domain object (no persistence identifiers)."""

    dedupe_key: str
    source: MessageSource
    rfc_message_id: MessageId | None
    subject: str | None
    sender: str | None
    recipients: tuple[str, ...]
    received_at: datetime | None
    body_plain: str
    thread_hint: ThreadId | None
    processing_status: MessageProcessingStatus


@dataclass(frozen=True, slots=True)
class TriageResult:
    importance: MessageImportance
    reply_requirement: ReplyRequirement
    summary: str
    actionable: bool
    confidence: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractedTask:
    title: str
    description: str | None
    due_at: datetime | None
    confidence: float
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class MorningDigest:
    """Markdown-ready digest content for a fixed time window."""

    window_start: datetime
    window_end: datetime
    markdown: str


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """Human review queue item (domain view; persistence may use additional columns)."""

    id: ReviewItemId
    review_kind: ReviewKind
    related_message_id: int
    related_task_id: int | None
    reason_code: str
    reason_text: str
    confidence: float
    payload_json: str
    status: ReviewStatus
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None

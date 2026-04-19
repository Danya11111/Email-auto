from __future__ import annotations

from enum import StrEnum


class MessageImportance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MessageSource(StrEnum):
    EML = "eml"
    MBOX = "mbox"
    APPLE_MAIL = "apple_mail"


class TaskStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    SYNCED = "synced"
    REJECTED = "rejected"


class ReplyRequirement(StrEnum):
    NO = "no"
    OPTIONAL = "optional"
    REQUIRED = "required"
    URGENT = "urgent"


class MessageProcessingStatus(StrEnum):
    """Persistence-oriented lifecycle; kept in domain as pure enum (no IO)."""

    INGESTED = "ingested"
    TRIAGED = "triaged"
    AWAITING_REVIEW = "awaiting_review"
    TASKS_EXTRACTED = "tasks_extracted"
    FAILED = "failed"


class MessageBodyTruncateStrategy(StrEnum):
    """How to shrink message bodies before sending them to an LLM."""

    HEAD = "head"
    HEAD_TAIL = "head_tail"
    MIDDLE_SNIP = "middle_snip"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewKind(StrEnum):
    TRIAGE = "triage"
    TASK = "task"

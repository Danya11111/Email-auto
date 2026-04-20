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
    # Legacy placeholder for export-based readers (stub adapter).
    APPLE_MAIL = "apple_mail"
    # JSON snapshots from Apple Mail automation (local-first drop folder).
    APPLE_MAIL_DROP = "apple_mail_drop"


class IngestedArtifactStatus(StrEnum):
    """Lifecycle for maildrop files / snapshot artifacts (restart-safe bookkeeping)."""

    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


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


class KanbanProvider(StrEnum):
    STUB = "stub"
    LOCAL_FILE = "local_file"
    TRELLO = "trello"


class KanbanSyncStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
    SKIPPED = "skipped"


class KanbanCardStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class KanbanPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

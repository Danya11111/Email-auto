from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import (
    IngestedArtifactStatus,
    MessageImportance,
    MessageProcessingStatus,
    MessageSource,
    ReplyRequirement,
    ReviewKind,
    ReviewStatus,
    TaskStatus,
)


class IncomingMessageDTO(BaseModel):
    """Normalized message as produced by mail readers (application boundary)."""

    model_config = {"frozen": True}

    dedupe_key: str
    source: MessageSource
    rfc_message_id: str | None
    subject: str | None
    sender: str | None
    recipients: tuple[str, ...] = Field(default_factory=tuple)
    received_at: datetime | None
    body_plain: str
    thread_hint: str | None
    source_path: str | None = None


class PersistedMessageDTO(BaseModel):
    model_config = {"frozen": True}

    id: int
    dedupe_key: str
    source: MessageSource
    rfc_message_id: str | None
    subject: str | None
    sender: str | None
    recipients: tuple[str, ...]
    received_at: datetime | None
    body_plain: str
    body_normalized: str
    thread_hint: str | None
    processing_status: MessageProcessingStatus


class TriageLLMResponseDTO(BaseModel):
    model_config = {"frozen": True}

    importance: MessageImportance
    reply_requirement: ReplyRequirement
    summary: str
    actionable: bool
    confidence: float
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)


class TaskExtractionItemDTO(BaseModel):
    model_config = {"frozen": True}

    title: str
    description: str | None = None
    due_at: datetime | None = None
    confidence: float


class DigestLLMResponseDTO(BaseModel):
    model_config = {"frozen": True}

    markdown: str


class DigestMessageSnapshotDTO(BaseModel):
    model_config = {"frozen": True}

    message_id: int
    subject: str | None
    sender: str | None
    importance: MessageImportance
    reply_requirement: ReplyRequirement
    triage_summary: str
    actionable: bool


class DigestTaskSnapshotDTO(BaseModel):
    model_config = {"frozen": True}

    task_id: int
    message_id: int
    title: str
    confidence: float
    due_at: str | None


class DigestReviewSnapshotDTO(BaseModel):
    model_config = {"frozen": True}

    review_id: int
    review_kind: ReviewKind
    message_id: int
    task_id: int | None
    reason_code: str
    reason_text: str
    confidence: float


class DailyDigestStatsDTO(BaseModel):
    model_config = {"frozen": True}

    messages_in_window: int
    messages_capped: int
    pending_reviews: int
    candidate_tasks: int


class DailyDigestContextDTO(BaseModel):
    model_config = {"frozen": True}

    window_start: datetime
    window_end: datetime
    stats: DailyDigestStatsDTO
    messages: tuple[DigestMessageSnapshotDTO, ...]
    candidate_tasks: tuple[DigestTaskSnapshotDTO, ...]
    pending_reviews: tuple[DigestReviewSnapshotDTO, ...]


class ReviewEnqueueCommandDTO(BaseModel):
    model_config = {"frozen": True}

    review_kind: ReviewKind
    message_id: int
    related_task_id: int | None = None
    reason_code: str
    reason_text: str
    confidence: float
    payload_json: str


class ReviewListItemDTO(BaseModel):
    model_config = {"frozen": True}

    id: int
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


class SavedCandidateTaskDTO(BaseModel):
    model_config = {"frozen": True}

    task_id: int
    dedupe_key: str
    created: bool


@dataclass(frozen=True, slots=True)
class IngestResultDTO:
    run_id: str
    inserted: int
    duplicates: int
    failures: int


@dataclass(frozen=True, slots=True)
class TriageBatchResultDTO:
    run_id: str
    processed: int
    failures: int
    reviews_enqueued: int = 0


@dataclass(frozen=True, slots=True)
class ExtractTasksBatchResultDTO:
    run_id: str
    messages_processed: int
    tasks_created: int
    failures: int
    reviews_enqueued: int = 0


@dataclass(frozen=True, slots=True)
class DigestBuildResultDTO:
    run_id: str
    digest_id: int
    markdown: str


@dataclass(frozen=True, slots=True)
class ExtractedTaskRecordDTO:
    id: int
    message_id: int
    title: str
    description: str | None
    due_at: datetime | None
    confidence: float
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class EnqueueReviewItemsResultDTO:
    inserted: int
    skipped_duplicates: int


@dataclass(frozen=True, slots=True)
class RunDailyResultDTO:
    run_id: str
    digest_markdown: str
    stdout_summary: str
    digest_id: int


class IngestedArtifactRecordDTO(BaseModel):
    """SQLite row view for maildrop / snapshot artifact bookkeeping."""

    model_config = {"frozen": True}

    id: int
    content_hash: str
    snapshot_id: str | None
    source_type: str
    original_filename: str
    related_message_id: int | None
    status: IngestedArtifactStatus
    first_seen_at: datetime
    processed_at: datetime | None
    error_text: str | None


@dataclass(frozen=True, slots=True)
class AppleMailDropIngestSummaryDTO:
    run_id: str
    found: int
    ingested: int
    duplicate: int
    failed: int
    moved_processed: int
    moved_failed: int

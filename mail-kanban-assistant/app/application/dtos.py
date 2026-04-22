from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import (
    ActionCenterCategory,
    IngestedArtifactStatus,
    KanbanProvider,
    KanbanSyncStatus,
    MessageImportance,
    MessageProcessingStatus,
    MessageSource,
    ReplyRequirement,
    ReplyState,
    ReviewKind,
    ReviewStatus,
    TaskStatus,
    ThreadActionState,
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


class KanbanDigestSectionDTO(BaseModel):
    """Compact Kanban sync snapshot for deterministic digest rendering."""

    model_config = {"frozen": True}

    provider: str
    auto_sync_enabled: bool
    approved_ready_to_sync: int
    pending_outbox: int
    synced: int
    failed: int
    recent_errors: tuple[str, ...] = Field(default_factory=tuple)
    outbound_updates_last_24h: int = 0
    manual_resync_pending: int = 0


class ActionCenterTaskPinDTO(BaseModel):
    model_config = {"frozen": True}

    task_id: int
    message_id: int
    title: str
    status: TaskStatus
    confidence: float
    due_at: str | None = None


class ActionCenterMessageRowDTO(BaseModel):
    """Single message row joined with triage for thread-aware processing."""

    model_config = {"frozen": True}

    message_id: int
    received_at: datetime | None
    subject: str | None
    sender: str | None
    recipients: tuple[str, ...] = Field(default_factory=tuple)
    thread_hint: str | None
    importance: MessageImportance
    reply_requirement: ReplyRequirement
    actionable: bool
    triage_summary: str
    triage_confidence: float = 0.0


class KanbanSyncFailurePinDTO(BaseModel):
    model_config = {"frozen": True}

    sync_record_id: int
    task_id: int
    provider: str
    last_error: str


class ActionCenterRawBundleDTO(BaseModel):
    """Inputs loaded from SQLite for deterministic action center construction."""

    model_config = {"frozen": True}

    window_start: datetime
    window_end: datetime
    messages: tuple[ActionCenterMessageRowDTO, ...]
    task_pins: tuple[ActionCenterTaskPinDTO, ...]
    pending_reviews: tuple[DigestReviewSnapshotDTO, ...]
    kanban_failures: tuple[KanbanSyncFailurePinDTO, ...]
    approved_ready_to_sync: int
    manual_resync_backlog: int


class MessageThreadSummaryDTO(BaseModel):
    model_config = {"frozen": True}

    thread_id: str
    related_message_ids: tuple[int, ...]
    latest_message_at: datetime | None
    participants: tuple[str, ...]
    subject_line: str
    aggregated_importance: MessageImportance
    max_reply_requirement: ReplyRequirement
    any_actionable: bool
    reply_state: ReplyState
    thread_action_state: ThreadActionState
    candidate_task_ids: tuple[int, ...] = Field(default_factory=tuple)
    pending_review_ids: tuple[int, ...] = Field(default_factory=tuple)
    likely_active: bool
    include_in_action_center: bool
    max_candidate_task_confidence: float = 0.0
    signals: tuple[str, ...] = Field(default_factory=tuple)


class DailyActionItemDTO(BaseModel):
    model_config = {"frozen": True}

    item_id: str
    source_type: str
    category: ActionCenterCategory
    priority_score: int
    title: str
    reason: str
    recommended_next_step: str
    thread_id: str | None = None
    message_ids: tuple[int, ...] = Field(default_factory=tuple)
    task_id: int | None = None
    review_id: int | None = None
    due_at: str | None = None
    reply_state: ReplyState | None = None
    signals: tuple[str, ...] = Field(default_factory=tuple)


class ActionCenterCategorySectionDTO(BaseModel):
    model_config = {"frozen": True}

    category: ActionCenterCategory
    items: tuple[DailyActionItemDTO, ...]


class ActionCenterSnapshotDTO(BaseModel):
    model_config = {"frozen": True}

    window_start: datetime
    window_end: datetime
    threads: tuple[MessageThreadSummaryDTO, ...]
    items: tuple[DailyActionItemDTO, ...]
    category_sections: tuple[ActionCenterCategorySectionDTO, ...]


class DailyDigestContextDTO(BaseModel):
    model_config = {"frozen": True}

    window_start: datetime
    window_end: datetime
    stats: DailyDigestStatsDTO
    messages: tuple[DigestMessageSnapshotDTO, ...]
    candidate_tasks: tuple[DigestTaskSnapshotDTO, ...]
    pending_reviews: tuple[DigestReviewSnapshotDTO, ...]
    kanban: KanbanDigestSectionDTO | None = None
    action_center: ActionCenterSnapshotDTO | None = None
    executive_summary_lines: tuple[str, ...] = Field(default_factory=tuple)


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


class PersistedExtractedTaskDTO(BaseModel):
    model_config = {"frozen": True}

    id: int
    message_id: int
    title: str
    description: str | None
    due_at: datetime | None
    confidence: float
    status: TaskStatus
    dedupe_key: str


class TaskKanbanSourceContextDTO(BaseModel):
    """Inputs required to build a deterministic Kanban card draft."""

    model_config = {"frozen": True}

    task: PersistedExtractedTaskDTO
    message_subject: str | None
    message_sender: str | None
    triage_summary: str | None
    triage_reply_requirement: ReplyRequirement | None
    triage_confidence: float | None
    triage_importance: MessageImportance | None = None


class KanbanSyncRecordRowDTO(BaseModel):
    model_config = {"frozen": True}

    id: int
    task_id: int
    provider: KanbanProvider
    sync_status: KanbanSyncStatus
    external_card_id: str | None
    external_card_url: str | None
    card_fingerprint: str
    payload_json: str
    created_at: datetime
    synced_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    retry_count: int
    last_outbound_action: str | None = None
    last_operation_note: str | None = None
    previous_fingerprint: str | None = None
    previous_external_card_url: str | None = None
    record_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class KanbanPreviewSummaryDTO:
    provider: KanbanProvider
    approved_ready: int
    would_skip_already_synced: int
    """Same fingerprint as last successful sync — no external write."""
    would_sync_or_retry: int
    """Backward compatible: planned creates + planned updates (excludes manual-skip)."""
    sample_task_ids: tuple[int, ...]
    planned_creates: int = 0
    planned_updates: int = 0
    planned_skip_manual_resync: int = 0
    planned_skip_provider_config: int = 0
    planned_fail_precondition: int = 0


@dataclass(frozen=True, slots=True)
class KanbanSyncBatchResultDTO:
    run_id: str
    found: int
    synced: int
    updated: int
    skipped: int
    failed: int
    dry_run: bool
    dry_run_planned: int = 0
    skip_provider_config: int = 0
    fail_precondition: int = 0
    skip_manual_resync: int = 0


@dataclass(frozen=True, slots=True)
class KanbanRetryBatchResultDTO:
    run_id: str
    attempted: int
    synced: int
    failed: int
    updated: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class KanbanStatusSummaryDTO:
    provider: KanbanProvider
    pending: int
    synced: int
    failed: int
    skipped: int
    last_errors: tuple[str, ...]
    provider_readiness: str | None = None
    last_outbound_actions: tuple[str, ...] = ()
    manual_resync_pending: int = 0
    outbound_updates_last_24h: int = 0
    yougile_update_existing_enabled: bool = False
    yougile_done_column_configured: bool = False
    yougile_blocked_column_configured: bool = False
    next_step_hint: str | None = None


class KanbanTaskSyncInspectionDTO(BaseModel):
    """Operational snapshot for a single task + provider sync row."""

    model_config = {"frozen": True}

    task_id: int
    provider: KanbanProvider
    local_task_status: TaskStatus | None
    sync_status: KanbanSyncStatus | None
    card_fingerprint: str | None
    external_card_id: str | None
    external_card_url: str | None
    last_outbound_action: str | None
    last_operation_note: str | None
    previous_fingerprint: str | None
    previous_external_card_url: str | None
    retry_count: int | None
    last_error: str | None
    last_attempt_at: str | None
    synced_at: str | None
    record_updated_at: str | None
    planned_outbound_action: str | None
    planned_reason_code: str | None
    current_draft_fingerprint: str | None
    update_existing_possible: bool
    manual_resync_required: bool


class YougileDiscoveryBoardDTO(BaseModel):
    model_config = {"frozen": True}

    id: str
    title: str
    deleted: bool = False
    project_id: str | None = None


class YougileDiscoveryColumnDTO(BaseModel):
    model_config = {"frozen": True}

    id: str
    title: str
    board_id: str
    deleted: bool = False


class YougileWorkspaceDiscoveryDTO(BaseModel):
    model_config = {"frozen": True}

    ok: bool
    error: str | None = None
    boards: tuple[YougileDiscoveryBoardDTO, ...] = Field(default_factory=tuple)
    columns: tuple[YougileDiscoveryColumnDTO, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class YougileSmokeSyncResultDTO:
    task_id: int
    dry_run: bool
    task_approved: bool
    plan: str | None
    run_id: str
    message: str
    external_task_id: str | None = None
    external_url: str | None = None
    synced: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

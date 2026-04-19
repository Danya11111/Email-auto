from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

from app.application.dtos import (
    DailyDigestContextDTO,
    DigestLLMResponseDTO,
    IngestedArtifactRecordDTO,
    IncomingMessageDTO,
    PersistedMessageDTO,
    ReviewEnqueueCommandDTO,
    ReviewListItemDTO,
    SavedCandidateTaskDTO,
    TaskExtractionItemDTO,
    TriageLLMResponseDTO,
)
from app.domain.enums import MessageProcessingStatus, ReviewKind, TaskStatus
from app.domain.models import ExtractedTask, MorningDigest, TriageResult


class MessageReaderPort(Protocol):
    """Produces normalized messages from a concrete mail source."""

    def read_messages(self) -> Sequence[IncomingMessageDTO]:
        ...


class MessageRepositoryPort(Protocol):
    def insert_message(
        self,
        message: IncomingMessageDTO,
        body_normalized: str,
        processing_status: MessageProcessingStatus,
    ) -> int:
        ...

    def find_message_id_by_dedupe_key(self, dedupe_key: str) -> int | None:
        ...

    def list_messages_pending_triage(self, limit: int) -> Sequence[PersistedMessageDTO]:
        ...

    def list_messages_for_task_extraction(self, limit: int) -> Sequence[PersistedMessageDTO]:
        ...

    def list_messages_for_digest(self, window_start: datetime, window_end: datetime) -> Sequence[PersistedMessageDTO]:
        ...

    def update_processing_status(self, message_id: int, status: MessageProcessingStatus) -> None:
        ...


class TriageResultRepositoryPort(Protocol):
    def save_triage(self, message_id: int, triage: TriageResult, raw_json: str) -> None:
        ...

    def has_triage(self, message_id: int) -> bool:
        ...

    def get_triage(self, message_id: int) -> TriageResult | None:
        ...

    def delete_for_message(self, message_id: int) -> None:
        ...

    def set_human_confirmed(self, message_id: int, *, confirmed: bool) -> None:
        ...


class TriageLLMPort(Protocol):
    def triage_message(self, message: PersistedMessageDTO) -> TriageLLMResponseDTO:
        ...


class TaskExtractionLLMPort(Protocol):
    def extract_tasks(self, message: PersistedMessageDTO, triage_summary: str) -> Sequence[TaskExtractionItemDTO]:
        ...


class DigestLLMPort(Protocol):
    def build_digest_markdown(self, window_start: datetime, window_end: datetime, payload_json: str) -> DigestLLMResponseDTO:
        ...


class TaskRepositoryPort(Protocol):
    def save_candidate_tasks(
        self, message_id: int, tasks: Sequence[ExtractedTask], dedupe_keys: Sequence[str]
    ) -> Sequence[SavedCandidateTaskDTO]:
        ...

    def update_task_status(self, task_id: int, status: TaskStatus) -> None:
        ...

    def message_has_candidate_tasks(self, message_id: int) -> bool:
        ...


class KanbanPort(Protocol):
    def create_task_card(self, task: ExtractedTask, message: PersistedMessageDTO) -> str | None:
        ...


class ClockPort(Protocol):
    def now(self) -> datetime:
        ...


class LoggerPort(Protocol):
    def info(self, event: str, **fields: object) -> None:
        ...

    def warning(self, event: str, **fields: object) -> None:
        ...

    def error(self, event: str, **fields: object) -> None:
        ...


class MorningDigestRepositoryPort(Protocol):
    def save_digest(self, pipeline_run_id: int | None, digest: MorningDigest) -> int:
        ...


class PipelineRunRepositoryPort(Protocol):
    def start_run(self, run_id: str, command: str) -> int:
        ...

    def finish_run(self, db_id: int, status: str, metadata: str | None) -> None:
        ...


class ReviewRepositoryPort(Protocol):
    def find_pending_duplicate(self, *, kind: ReviewKind, message_id: int, task_id: int | None) -> int | None:
        ...

    def enqueue(self, cmd: ReviewEnqueueCommandDTO) -> tuple[int, bool]:
        ...

    def list_pending(self, limit: int) -> Sequence[ReviewListItemDTO]:
        ...

    def get(self, review_id: int) -> ReviewListItemDTO:
        ...

    def approve(self, review_id: int, *, decided_by: str, note: str | None) -> None:
        ...

    def reject(self, review_id: int, *, decided_by: str, note: str | None) -> None:
        ...


class DigestContextPort(Protocol):
    def load_daily_digest_context(
        self, *, window_start: datetime, window_end: datetime, max_messages: int
    ) -> DailyDigestContextDTO:
        ...


class IngestedArtifactRepositoryPort(Protocol):
    def maybe_find_artifact_by_hash_or_snapshot_id(
        self, *, content_hash: str, snapshot_id: str | None
    ) -> IngestedArtifactRecordDTO | None:
        ...

    def check_artifact_already_processed(self, *, content_hash: str) -> bool:
        ...

    def register_incoming_artifact(
        self, *, content_hash: str, source_type: str, original_filename: str
    ) -> int:
        ...

    def set_snapshot_id(self, artifact_id: int, snapshot_id: str) -> None:
        ...

    def mark_artifact_processed(self, *, artifact_id: int, related_message_id: int) -> None:
        ...

    def mark_artifact_failed(self, *, artifact_id: int, error_text: str) -> None:
        ...

    def reset_failed_artifact_to_pending(self, artifact_id: int) -> None:
        ...

    def find_artifact_with_snapshot_id(self, *, snapshot_id: str, exclude_artifact_id: int) -> IngestedArtifactRecordDTO | None:
        ...


class AppleMailDropScannerPort(Protocol):
    def list_incoming_json_paths(self, maildrop_root: Path) -> Sequence[Path]:
        ...


class MaildropFilesystemPort(Protocol):
    def ensure_maildrop_layout(self, maildrop_root: Path) -> None:
        ...

    def move_to_processed(self, src: Path, maildrop_root: Path) -> Path:
        ...

    def move_to_failed(self, src: Path, maildrop_root: Path) -> Path:
        ...


class HttpProbePort(Protocol):
    def get_status(self, url: str, *, timeout_seconds: float) -> int | None:
        """Return HTTP status code, or None if the request did not complete."""
        ...

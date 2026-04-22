from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import ReplyDraftGenerationMode, ReplyDraftStatus, ReplyTone


@dataclass(frozen=True, slots=True)
class ReplyDraft:
    """Human-in-the-loop reply draft artifact (local-only; never sent automatically)."""

    id: int
    thread_id: str
    primary_message_id: int
    related_action_item_id: str | None
    status: ReplyDraftStatus
    tone: ReplyTone
    subject_suggestion: str
    body_text: str
    opening_line: str
    closing_line: str
    short_rationale: str
    key_points: tuple[str, ...]
    missing_information: tuple[str, ...]
    confidence: float
    source_message_ids: tuple[int, ...]
    source_task_ids: tuple[int, ...]
    source_review_ids: tuple[int, ...]
    generated_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    rejected_at: datetime | None
    exported_at: datetime | None
    generation_fingerprint: str
    model_name: str | None
    generation_mode: ReplyDraftGenerationMode
    fact_boundary_note: str
    user_note: str | None

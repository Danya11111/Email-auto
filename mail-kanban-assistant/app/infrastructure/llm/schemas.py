from __future__ import annotations

from pydantic import BaseModel, Field

from app.application.dtos import TaskExtractionItemDTO


class DigestStructuredResponse(BaseModel):
    model_config = {"frozen": True}

    markdown: str


class TaskExtractionStructuredResponse(BaseModel):
    model_config = {"frozen": True}

    tasks: list[TaskExtractionItemDTO] = Field(default_factory=list)


class ReplyDraftStructuredResponse(BaseModel):
    model_config = {"frozen": True}

    subject_suggestion: str
    opening_line: str = ""
    core_points: list[str] = Field(default_factory=list)
    closing_line: str = ""
    body_text: str
    short_rationale: str = ""
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    fact_boundary_note: str = ""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.application.dtos import TaskExtractionItemDTO


class DigestStructuredResponse(BaseModel):
    model_config = {"frozen": True}

    markdown: str


class TaskExtractionStructuredResponse(BaseModel):
    model_config = {"frozen": True}

    tasks: list[TaskExtractionItemDTO] = Field(default_factory=list)

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

from app.application.dtos import (
    DigestLLMResponseDTO,
    IncomingMessageDTO,
    PersistedMessageDTO,
    TaskExtractionItemDTO,
    TriageLLMResponseDTO,
)
from app.application.ports import ClockPort, DigestLLMPort, LoggerPort, MessageReaderPort, TaskExtractionLLMPort, TriageLLMPort
from app.domain.enums import MessageImportance, ReplyRequirement


class FixedClock(ClockPort):
    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        self._now = now

    def now(self) -> datetime:
        return self._now


class NullLogger(LoggerPort):
    def info(self, event: str, **fields: object) -> None:
        return

    def warning(self, event: str, **fields: object) -> None:
        return

    def error(self, event: str, **fields: object) -> None:
        return


class ListIncomingReader(MessageReaderPort):
    def __init__(self, items: Sequence[IncomingMessageDTO]) -> None:
        self._items = tuple(items)

    def read_messages(self) -> Sequence[IncomingMessageDTO]:
        return self._items


class FakeTriageLLM(TriageLLMPort):
    def __init__(self, response: TriageLLMResponseDTO | None = None) -> None:
        self.calls: list[PersistedMessageDTO] = []
        self.response = response or TriageLLMResponseDTO(
            importance=MessageImportance.HIGH,
            reply_requirement=ReplyRequirement.REQUIRED,
            summary="Needs follow-up",
            actionable=True,
            confidence=0.9,
            reason_codes=("test",),
        )

    def triage_message(self, message: PersistedMessageDTO) -> TriageLLMResponseDTO:
        self.calls.append(message)
        return self.response


class FakeTaskLLM(TaskExtractionLLMPort):
    def __init__(self, tasks: Sequence[TaskExtractionItemDTO] | None = None) -> None:
        self.calls: list[tuple[PersistedMessageDTO, str]] = []
        self.tasks = list(tasks or [TaskExtractionItemDTO(title="Reply to Alice", confidence=0.9)])

    def extract_tasks(self, message: PersistedMessageDTO, triage_summary: str) -> Sequence[TaskExtractionItemDTO]:
        self.calls.append((message, triage_summary))
        return tuple(self.tasks)


class FakeDigestLLM(DigestLLMPort):
    def __init__(self, markdown: str = "# Digest\n\n- Item") -> None:
        self.calls: list[str] = []
        self.markdown = markdown

    def build_digest_markdown(self, window_start: datetime, window_end: datetime, payload_json: str) -> DigestLLMResponseDTO:
        _ = (window_start, window_end)
        self.calls.append(payload_json)
        return DigestLLMResponseDTO(markdown=self.markdown)

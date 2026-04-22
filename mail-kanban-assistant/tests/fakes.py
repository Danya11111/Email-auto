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
from app.application.dtos import ReplyDraftStructuredLLMItemDTO
from app.application.ports import ClockPort, DigestLLMPort, LoggerPort, MessageReaderPort, ReplyDraftLLMPort, TaskExtractionLLMPort, TriageLLMPort
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


class FakeReplyDraftLLM(ReplyDraftLLMPort):
    def __init__(self, item: ReplyDraftStructuredLLMItemDTO | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.item = item or ReplyDraftStructuredLLMItemDTO(
            subject_suggestion="Re: follow-up",
            opening_line="Hi,",
            core_points=("Ack request",),
            closing_line="Thanks,",
            body_text="Hi,\n\nAck request.\n\nThanks,",
            short_rationale="Based on triage summary only.",
            missing_information=("Exact deadline if any",),
            confidence=0.55,
            fact_boundary_note="Do not confirm dates not in context.",
        )

    def generate_reply_draft_structured(
        self,
        *,
        context_json: str,
        tone: str,
        reply_state: str,
    ) -> ReplyDraftStructuredLLMItemDTO:
        self.calls.append((context_json[:80], tone, reply_state))
        return self.item


class FakeDigestLLM(DigestLLMPort):
    def __init__(self, markdown: str = "# Digest\n\n- Item") -> None:
        self.calls: list[str] = []
        self.markdown = markdown

    def build_digest_markdown(self, window_start: datetime, window_end: datetime, payload_json: str) -> DigestLLMResponseDTO:
        _ = (window_start, window_end)
        self.calls.append(payload_json)
        return DigestLLMResponseDTO(markdown=self.markdown)

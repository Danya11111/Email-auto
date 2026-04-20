from __future__ import annotations

from app.application.dtos import PersistedMessageDTO
from app.application.ports import KanbanPort, LoggerPort
from app.domain.models import ExtractedTask, KanbanCardDraft, KanbanProviderCreateResult


class StubKanbanAdapter(KanbanPort):
    def __init__(self, logger: LoggerPort) -> None:
        self._logger = logger

    def create_task_card(self, task: ExtractedTask, message: PersistedMessageDTO) -> str | None:
        self._logger.info(
            "kanban.stub.create_skipped",
            message_id=message.id,
            task_title=task.title,
            confidence=task.confidence,
        )
        return None

    def create_card(self, draft: KanbanCardDraft) -> KanbanProviderCreateResult:
        self._logger.info("kanban.stub.create_card_noop", task_id=draft.internal_task_id)
        return KanbanProviderCreateResult(
            success=False,
            external_card_id=None,
            external_card_url=None,
            error_message="kanban_provider_stub_does_not_create_cards",
        )

    def update_card(self, draft: KanbanCardDraft, *, external_card_id: str) -> KanbanProviderCreateResult:
        _ = (draft, external_card_id)
        self._logger.info("kanban.stub.update_card_noop")
        return KanbanProviderCreateResult(
            success=False,
            external_card_id=None,
            external_card_url=None,
            error_message="kanban_provider_stub_does_not_update_cards",
        )

    def healthcheck(self) -> bool:
        return True

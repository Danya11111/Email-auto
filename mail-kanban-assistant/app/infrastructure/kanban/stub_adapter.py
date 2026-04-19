from __future__ import annotations

from app.application.dtos import PersistedMessageDTO
from app.application.ports import KanbanPort, LoggerPort
from app.domain.models import ExtractedTask


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

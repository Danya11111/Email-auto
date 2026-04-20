from __future__ import annotations

from app.application.ports import KanbanPort, LoggerPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider
from app.infrastructure.kanban.local_file_adapter import LocalFileKanbanAdapter
from app.infrastructure.kanban.stub_adapter import StubKanbanAdapter
from app.infrastructure.kanban.trello_adapter import TrelloKanbanAdapter


def make_kanban_port(settings: AppSettings, logger: LoggerPort) -> KanbanPort:
    provider = settings.kanban_provider
    if provider == KanbanProvider.LOCAL_FILE:
        return LocalFileKanbanAdapter(root_dir=settings.kanban_root_dir.resolve(), logger=logger)
    if provider == KanbanProvider.TRELLO:
        return TrelloKanbanAdapter(
            api_key=settings.trello_api_key,
            token=settings.trello_token,
            list_id_todo=settings.trello_list_id_todo,
            logger=logger,
            timeout_seconds=float(settings.kanban_http_timeout_seconds),
        )
    return StubKanbanAdapter(logger)

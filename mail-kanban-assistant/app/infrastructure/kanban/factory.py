from __future__ import annotations

from app.application.ports import KanbanPort, LoggerPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider
from app.infrastructure.kanban.local_file_adapter import LocalFileKanbanAdapter
from app.infrastructure.kanban.stub_adapter import StubKanbanAdapter
from app.infrastructure.kanban.trello_adapter import TrelloKanbanAdapter
from app.infrastructure.kanban.yougile_adapter import YougileKanbanAdapter, yougile_api_v2_root


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
    if provider == KanbanProvider.YOUGILE:
        return YougileKanbanAdapter(
            api_v2_root=yougile_api_v2_root(settings.yougile_base_url),
            api_key=settings.yougile_api_key,
            board_id=settings.yougile_board_id,
            column_id_todo=settings.yougile_column_id_todo,
            column_id_done=settings.yougile_column_id_done,
            column_id_blocked=settings.yougile_column_id_blocked,
            timeout_seconds=float(settings.yougile_request_timeout_seconds),
            requests_per_minute=int(settings.yougile_requests_per_minute),
            max_description_chars=int(settings.yougile_max_description_chars),
            include_internal_ids=bool(settings.yougile_include_internal_ids),
            attach_source_metadata=bool(settings.yougile_attach_source_metadata),
            logger=logger,
        )
    return StubKanbanAdapter(logger)

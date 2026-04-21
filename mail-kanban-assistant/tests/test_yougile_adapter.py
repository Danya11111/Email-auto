from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.domain.enums import KanbanCardStatus, KanbanPriority
from app.domain.models import KanbanCardDraft
from app.infrastructure.kanban.yougile_adapter import YougileKanbanAdapter, yougile_api_v2_root
from tests.fakes import NullLogger


def _draft() -> KanbanCardDraft:
    return KanbanCardDraft(
        internal_task_id=9,
        source_message_id=3,
        title="Hello",
        description="Body",
        due_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        priority=KanbanPriority.MEDIUM,
        card_status=KanbanCardStatus.TODO,
        labels=(),
        dedupe_marker="m:1",
        fingerprint="fp1",
    )


def test_yougile_api_v2_root_appends_suffix() -> None:
    assert yougile_api_v2_root("https://ru.yougile.com") == "https://ru.yougile.com/api-v2"
    assert yougile_api_v2_root("https://ru.yougile.com/api-v2") == "https://ru.yougile.com/api-v2"


def test_yougile_create_success_top_level_id() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "POST" and str(request.url).endswith("/tasks"):
            return httpx.Response(201, json={"id": "111-222"})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, timeout=5.0)
    adapter = YougileKanbanAdapter(
        api_v2_root="https://ru.yougile.com",
        api_key="k",
        board_id="b",
        column_id_todo="col-todo",
        column_id_done="",
        column_id_blocked="",
        column_id_for_draft=None,
        timeout_seconds=5.0,
        requests_per_minute=60,
        max_description_chars=5000,
        include_internal_ids=True,
        attach_source_metadata=False,
        logger=NullLogger(),
        http_client=client,
    )
    res = adapter.create_card(_draft())
    assert res.success is True
    assert res.external_card_id == "111-222"
    assert res.external_card_url is not None
    assert "111-222" in (res.external_card_url or "")
    assert calls and calls[0][0] == "POST"


def test_yougile_create_nested_content_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"content": {"id": "nested-id"}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    adapter = YougileKanbanAdapter(
        api_v2_root="https://ru.yougile.com",
        api_key="k",
        board_id="b",
        column_id_todo="c",
        column_id_done="",
        column_id_blocked="",
        column_id_for_draft=None,
        timeout_seconds=5.0,
        requests_per_minute=60,
        max_description_chars=5000,
        include_internal_ids=False,
        attach_source_metadata=False,
        logger=NullLogger(),
        http_client=client,
    )
    res = adapter.create_card(_draft())
    assert res.success and res.external_card_id == "nested-id"


def test_yougile_create_config_error_missing_key() -> None:
    adapter = YougileKanbanAdapter(
        api_v2_root="https://ru.yougile.com",
        api_key="",
        board_id="b",
        column_id_todo="c",
        column_id_done="",
        column_id_blocked="",
        column_id_for_draft=None,
        timeout_seconds=5.0,
        requests_per_minute=40,
        max_description_chars=5000,
        include_internal_ids=True,
        attach_source_metadata=True,
        logger=NullLogger(),
    )
    res = adapter.create_card(_draft())
    assert res.success is False
    assert "YOUGILE_API_KEY" in (res.error_message or "")


def test_yougile_create_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    adapter = YougileKanbanAdapter(
        api_v2_root="https://ru.yougile.com",
        api_key="secret",
        board_id="b",
        column_id_todo="c",
        column_id_done="",
        column_id_blocked="",
        column_id_for_draft=None,
        timeout_seconds=5.0,
        requests_per_minute=40,
        max_description_chars=5000,
        include_internal_ids=True,
        attach_source_metadata=False,
        logger=NullLogger(),
        http_client=client,
    )
    res = adapter.create_card(_draft())
    assert res.success is False
    assert "401" in (res.error_message or "")


def test_yougile_update_put() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.method == "PUT" and "/tasks/ext-99" in str(request.url):
            return httpx.Response(200, json={"id": "ext-99"})
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    adapter = YougileKanbanAdapter(
        api_v2_root="https://ru.yougile.com",
        api_key="k",
        board_id="b",
        column_id_todo="c",
        column_id_done="",
        column_id_blocked="",
        column_id_for_draft=None,
        timeout_seconds=5.0,
        requests_per_minute=40,
        max_description_chars=5000,
        include_internal_ids=True,
        attach_source_metadata=False,
        logger=NullLogger(),
        http_client=client,
    )
    res = adapter.update_card(_draft(), external_card_id="ext-99")
    assert res.success is True
    assert any("tasks/ext-99" in p for p in paths)


def test_yougile_healthcheck_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/boards/b1" in str(request.url):
            return httpx.Response(200, json={"id": "b1"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    adapter = YougileKanbanAdapter(
        api_v2_root="https://ru.yougile.com",
        api_key="k",
        board_id="b1",
        column_id_todo="c",
        column_id_done="",
        column_id_blocked="",
        column_id_for_draft=None,
        timeout_seconds=5.0,
        requests_per_minute=40,
        max_description_chars=5000,
        include_internal_ids=True,
        attach_source_metadata=False,
        logger=NullLogger(),
        http_client=client,
    )
    assert adapter.healthcheck() is True

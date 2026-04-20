from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.application.doctor_report import DoctorLineDTO, DoctorReportDTO
from app.application.dtos import IncomingMessageDTO
from app.application.use_cases.ingest_messages import IngestMessagesUseCase
from app.application.use_cases.yougile_workspace import (
    YougileDiscoverWorkspaceUseCase,
    YougileSmokeSyncUseCase,
    build_yougile_env_fragment,
    render_yougile_discovery_text,
    run_yougile_deep_doctor,
    run_yougile_live_status_probe,
)
from app.application.use_cases.kanban_sync import SyncApprovedTasksToKanbanUseCase
from app.domain.enums import MessageImportance, MessageProcessingStatus, MessageSource, ReplyRequirement, TaskStatus
from app.domain.models import ExtractedTask, TriageResult
from app.infrastructure.storage.repositories import SqliteMessageRepository, SqlitePipelineRunRepository, SqliteTaskRepository, SqliteTriageRepository
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from app.infrastructure.storage.sqlite_kanban_sync_repository import SqliteKanbanSyncRepository
from app.infrastructure.kanban.factory import make_kanban_port
from tests.fakes import FixedClock, ListIncomingReader, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(encoding="utf-8")


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "yg_onb.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()


def _seed_approved(conn, clock: FixedClock) -> int:
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    incoming = IncomingMessageDTO(
        dedupe_key="eml:yg-smoke-1",
        source=MessageSource.EML,
        rfc_message_id="yg-smoke-1",
        subject="S",
        sender="a@b.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="body",
        thread_hint=None,
        source_path=None,
    )
    IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=NullLogger()).execute(
        ListIncomingReader([incoming]), run_id="r", command="t", record_pipeline=False
    )
    mid = messages.list_messages_pending_triage(limit=1)[0].id
    triage_repo.save_triage(
        mid,
        TriageResult(
            importance=MessageImportance.LOW,
            reply_requirement=ReplyRequirement.NO,
            summary="s",
            actionable=True,
            confidence=0.9,
            reason_codes=(),
        ),
        raw_json="{}",
    )
    messages.update_processing_status(mid, MessageProcessingStatus.TRIAGED)
    saved = tasks.save_candidate_tasks(
        mid,
        [ExtractedTask(title="T1", description=None, due_at=None, confidence=0.9, status=TaskStatus.CANDIDATE)],
        [f"{mid}:t1"],
    )
    tid = saved[0].task_id
    tasks.update_task_status(tid, TaskStatus.APPROVED)
    return tid


def test_discover_parses_boards_and_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_API_KEY", "secret")
    monkeypatch.setenv("YOUGILE_BASE_URL", "https://ru.yougile.com")
    from app.config import AppSettings

    settings = AppSettings()

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if request.method == "GET" and u.rstrip("/").endswith("/boards"):
            return httpx.Response(
                200,
                json={"content": [{"id": "b1", "title": "Board One", "deleted": False, "projectId": "p1"}]},
            )
        if request.method == "GET" and u.rstrip("/").endswith("/columns"):
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"id": "c1", "title": "Todo", "deleted": False, "boardId": "b1"},
                    ]
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    uc = YougileDiscoverWorkspaceUseCase(settings=settings, logger=NullLogger())
    dto = uc.execute(http_client=client)
    assert dto.ok is True
    assert len(dto.boards) == 1
    assert dto.boards[0].id == "b1"
    assert any(c.id == "c1" for c in dto.columns)


def test_discover_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_API_KEY", "bad")
    from app.config import AppSettings

    settings = AppSettings()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    uc = YougileDiscoverWorkspaceUseCase(settings=settings, logger=NullLogger())
    dto = uc.execute(http_client=client)
    assert dto.ok is False
    assert dto.error is not None
    assert "401" in dto.error or "authentication" in dto.error.lower()


def test_deep_doctor_auth_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_API_KEY", "x")
    monkeypatch.setenv("YOUGILE_BOARD_ID", "")
    monkeypatch.setenv("YOUGILE_COLUMN_ID_TODO", "")
    from app.config import AppSettings

    settings = AppSettings()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    lines = run_yougile_deep_doctor(settings, NullLogger(), http=client)
    assert any(l.level == "FAIL" for l in lines)


def test_live_probe_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_API_KEY", "k")
    monkeypatch.setenv("YOUGILE_BOARD_ID", "b1")
    monkeypatch.setenv("YOUGILE_COLUMN_ID_TODO", "c1")
    from app.config import AppSettings

    settings = AppSettings()

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        tail = u.split("/api-v2", 1)[-1].strip("/") if "/api-v2" in u else ""
        if tail == "boards":
            return httpx.Response(200, json={"content": []})
        if tail == "boards/b1":
            return httpx.Response(200, json={"id": "b1", "title": "B"})
        if tail == "columns/c1":
            return httpx.Response(200, json={"id": "c1", "title": "Todo", "boardId": "b1"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    lines = run_yougile_live_status_probe(settings, NullLogger(), http=client)
    assert any("GET /boards OK" in ln for ln in lines)


def test_smoke_dry_run_approved(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_PROVIDER", "yougile")
    monkeypatch.setenv("YOUGILE_API_KEY", "k")
    monkeypatch.setenv("YOUGILE_COLUMN_ID_TODO", "c")
    monkeypatch.setenv("YOUGILE_BOARD_ID", "b")
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
    logger = NullLogger()
    tid = _seed_approved(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    sync_uc = SyncApprovedTasksToKanbanUseCase(
        tasks=tasks,
        sync=sync_repo,
        kanban=make_kanban_port(settings, logger),
        logger=logger,
        settings=settings,
    )
    uc = YougileSmokeSyncUseCase(tasks=tasks, sync=sync_repo, sync_uc=sync_uc, settings=settings)
    res = uc.execute(task_id=tid, dry_run=True, run_id="smoke1")
    assert res.task_approved is True
    assert res.dry_run is True
    assert "Dry-run" in res.message


def test_smoke_rejects_non_approved(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_PROVIDER", "yougile")
    monkeypatch.setenv("YOUGILE_API_KEY", "k")
    monkeypatch.setenv("YOUGILE_COLUMN_ID_TODO", "c")
    monkeypatch.setenv("YOUGILE_BOARD_ID", "b")
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
    logger = NullLogger()
    tid = _seed_approved(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    tasks.update_task_status(tid, TaskStatus.CANDIDATE)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    sync_uc = SyncApprovedTasksToKanbanUseCase(
        tasks=tasks,
        sync=sync_repo,
        kanban=make_kanban_port(settings, logger),
        logger=logger,
        settings=settings,
    )
    uc = YougileSmokeSyncUseCase(tasks=tasks, sync=sync_repo, sync_uc=sync_uc, settings=settings)
    res = uc.execute(task_id=tid, dry_run=True, run_id="x")
    assert res.task_approved is False


def test_doctor_report_json_roundtrip() -> None:
    r = DoctorReportDTO(lines=(DoctorLineDTO("OK", "fine"),))
    d = json.loads(r.render_json())
    assert d["lines"][0]["level"] == "OK"


def test_build_env_fragment_contains_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_API_KEY", "")
    from app.config import AppSettings

    text = build_yougile_env_fragment(AppSettings(), board_id=None, column_todo=None)
    assert "KANBAN_PROVIDER=yougile" in text
    assert "YOUGILE_BOARD_ID=" in text


def test_render_discovery_unknown_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_API_KEY", "k")
    from app.config import AppSettings
    from app.application.dtos import YougileWorkspaceDiscoveryDTO

    dto = YougileWorkspaceDiscoveryDTO(ok=True, boards=(), columns=(), warnings=("w1",))
    txt = render_yougile_discovery_text(dto, compact=False, base_url_for_env="https://ru.yougile.com")
    assert "w1" in txt

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.dtos import IncomingMessageDTO
from app.application.kanban_mapping import KanbanMappingOptions, build_kanban_card_draft
from app.application.use_cases.build_morning_digest import BuildMorningDigestUseCase
from app.application.use_cases.ingest_messages import IngestMessagesUseCase
from app.application.use_cases.kanban_sync import (
    ExportLocalKanbanBoardUseCase,
    ListKanbanSyncStatusUseCase,
    PreviewKanbanSyncCandidatesUseCase,
    SyncApprovedTasksToKanbanUseCase,
)
from app.domain.enums import KanbanProvider, MessageImportance, MessageProcessingStatus, MessageSource, ReplyRequirement, TaskStatus
from app.domain.models import ExtractedTask, TriageResult
from app.infrastructure.kanban.factory import make_kanban_port
from app.infrastructure.storage.repositories import (
    SqliteDigestContextRepository,
    SqliteMessageRepository,
    SqliteMorningDigestRepository,
    SqlitePipelineRunRepository,
    SqliteTaskRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from app.infrastructure.storage.sqlite_kanban_sync_repository import SqliteKanbanSyncRepository
from tests.fakes import FixedClock, ListIncomingReader, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "kb.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()


def _seed_approved_task(conn, clock: FixedClock) -> int:
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    incoming = IncomingMessageDTO(
        dedupe_key="eml:kb-1",
        source=MessageSource.EML,
        rfc_message_id="kb-1",
        subject="Client asks for estimate",
        sender="c@client.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="Please send estimate",
        thread_hint=None,
        source_path=None,
    )
    IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=NullLogger()).execute(
        ListIncomingReader([incoming]),
        run_id="r",
        command="test",
        record_pipeline=False,
    )
    mid = messages.list_messages_pending_triage(limit=1)[0].id
    triage_repo.save_triage(
        mid,
        TriageResult(
            importance=MessageImportance.MEDIUM,
            reply_requirement=ReplyRequirement.REQUIRED,
            summary="Client needs pricing",
            actionable=True,
            confidence=0.88,
            reason_codes=(),
        ),
        raw_json="{}",
    )
    messages.update_processing_status(mid, MessageProcessingStatus.TRIAGED)
    saved = tasks.save_candidate_tasks(
        mid,
        [ExtractedTask(title="Send estimate", description=None, due_at=None, confidence=0.9, status=TaskStatus.CANDIDATE)],
        [f"{mid}:send estimate"],
    )
    tid = saved[0].task_id
    tasks.update_task_status(tid, TaskStatus.APPROVED)
    return tid


def test_resync_skips_when_task_still_approved_and_fingerprint_unchanged(
    conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a task is re-opened to APPROVED but the card fingerprint matches the last successful sync, do not write again."""
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "board_reopen"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")
    os.environ.pop("KANBAN_AUTO_SYNC", None)

    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    logger = NullLogger()
    tid = _seed_approved_task(conn, clock)

    tasks = SqliteTaskRepository(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    kanban = make_kanban_port(settings, logger)
    uc = SyncApprovedTasksToKanbanUseCase(tasks=tasks, sync=sync_repo, kanban=kanban, logger=logger, settings=settings)

    assert uc.execute(run_id="a1", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=10, only_task_id=None).synced == 1
    tasks.update_task_status(tid, TaskStatus.APPROVED)

    r2 = uc.execute(run_id="a2", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=10, only_task_id=None)
    assert r2.synced == 0
    assert r2.skipped >= 1


def test_kanban_sync_record_mark_success_and_idempotent_skip(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "board"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")
    os.environ.pop("KANBAN_AUTO_SYNC", None)

    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    logger = NullLogger()
    _seed_approved_task(conn, clock)

    tasks = SqliteTaskRepository(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    kanban = make_kanban_port(settings, logger)
    uc = SyncApprovedTasksToKanbanUseCase(tasks=tasks, sync=sync_repo, kanban=kanban, logger=logger, settings=settings)

    r1 = uc.execute(run_id="s1", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=10, only_task_id=None)
    assert r1.synced == 1
    assert r1.skipped == 0

    r2 = uc.execute(run_id="s2", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=10, only_task_id=None)
    assert r2.synced == 0
    # Task is no longer APPROVED after first successful sync, so nothing is "found" for a second pass.
    assert r2.found == 0
    assert r2.skipped == 0

    row = conn.execute("SELECT status FROM extracted_tasks LIMIT 1").fetchone()
    assert str(row["status"]) == TaskStatus.SYNCED.value


def test_local_file_adapter_no_duplicate_file(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "b2"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    logger = NullLogger()
    _seed_approved_task(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    kanban = make_kanban_port(settings, logger)
    uc = SyncApprovedTasksToKanbanUseCase(tasks=tasks, sync=sync_repo, kanban=kanban, logger=logger, settings=settings)
    uc.execute(run_id="a", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=5, only_task_id=None)
    uc.execute(run_id="b", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=5, only_task_id=None)
    cards = list((tmp_path / "b2" / "cards").glob("task_*.json"))
    assert len(cards) == 1


def test_preview_and_dry_run(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "b3"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    logger = NullLogger()
    _seed_approved_task(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    preview = PreviewKanbanSyncCandidatesUseCase(tasks=tasks, sync=sync_repo, logger=logger, settings=settings)
    pv = preview.execute(provider=KanbanProvider.LOCAL_FILE, limit=10)
    assert pv.would_sync_or_retry == 1

    kanban = make_kanban_port(settings, logger)
    sync_uc = SyncApprovedTasksToKanbanUseCase(tasks=tasks, sync=sync_repo, kanban=kanban, logger=logger, settings=settings)
    dr = sync_uc.execute(run_id="d", provider=KanbanProvider.LOCAL_FILE, dry_run=True, limit=10, only_task_id=None)
    assert dr.dry_run_planned == 1
    assert dr.synced == 0
    assert not (tmp_path / "b3" / "cards").exists() or not list((tmp_path / "b3" / "cards").glob("*.json"))


def test_export_local_board(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "b4"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    logger = NullLogger()
    _seed_approved_task(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    kanban = make_kanban_port(settings, logger)
    SyncApprovedTasksToKanbanUseCase(tasks=tasks, sync=sync_repo, kanban=kanban, logger=logger, settings=settings).execute(
        run_id="e", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=5, only_task_id=None
    )
    out = ExportLocalKanbanBoardUseCase(settings=settings, logger=logger).execute()
    assert out.exists()
    assert "Local Kanban" in out.read_text(encoding="utf-8")


def test_digest_includes_kanban_section(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "b5"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    digests = SqliteMorningDigestRepository(conn, clock)
    digest_ctx = SqliteDigestContextRepository(conn)
    kb_sync = SqliteKanbanSyncRepository(conn, clock)
    uc = BuildMorningDigestUseCase(
        digest_context=digest_ctx,
        digests=digests,
        clock=clock,
        logger=logger,
        settings=settings,
        kanban_sync=kb_sync,
    )
    res = uc.execute(run_id="dg", pipeline_run_db_id=None, pipeline_stats={})
    assert "## Kanban sync" in res.markdown
    assert settings.kanban_provider.value in res.markdown


def test_fingerprint_changes_when_title_changes(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "b6"))
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    tid = _seed_approved_task(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    ctx1 = tasks.get_task_kanban_context(tid)
    assert ctx1 is not None
    d1 = build_kanban_card_draft(ctx1, KanbanMappingOptions())
    conn.execute("UPDATE extracted_tasks SET title = ? WHERE id = ?", ("Send estimate v2", tid))
    conn.commit()
    ctx2 = tasks.get_task_kanban_context(tid)
    assert ctx2 is not None
    d2 = build_kanban_card_draft(ctx2, KanbanMappingOptions())
    assert d1.fingerprint != d2.fingerprint


def test_list_status_use_case(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "b7"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")
    from app.config import AppSettings

    settings = AppSettings()
    clock = FixedClock(datetime(2026, 5, 1, 8, 0, tzinfo=UTC))
    logger = NullLogger()
    _seed_approved_task(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    sync_repo = SqliteKanbanSyncRepository(conn, clock)
    kanban = make_kanban_port(settings, logger)
    SyncApprovedTasksToKanbanUseCase(tasks=tasks, sync=sync_repo, kanban=kanban, logger=logger, settings=settings).execute(
        run_id="st", provider=KanbanProvider.LOCAL_FILE, dry_run=False, limit=5, only_task_id=None
    )
    st = ListKanbanSyncStatusUseCase(sync=sync_repo, settings=settings).execute(provider=KanbanProvider.LOCAL_FILE)
    assert st.synced >= 1

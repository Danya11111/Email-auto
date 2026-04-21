from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.dtos import IncomingMessageDTO
from app.application.use_cases.build_morning_digest import BuildMorningDigestUseCase
from app.application.use_cases.ingest_messages import IngestMessagesUseCase
from app.domain.enums import KanbanProvider, MessageImportance, MessageProcessingStatus, MessageSource, ReplyRequirement
from app.infrastructure.storage.repositories import (
    SqliteDigestContextRepository,
    SqliteMessageRepository,
    SqliteMorningDigestRepository,
    SqlitePipelineRunRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_kanban_sync_repository import SqliteKanbanSyncRepository
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from app.domain.models import TriageResult
from tests.fakes import FixedClock, ListIncomingReader, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "digest.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()


def test_build_digest_persists_and_contains_sections(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    digests = SqliteMorningDigestRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    digest_ctx = SqliteDigestContextRepository(conn)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:digest-1",
        source=MessageSource.EML,
        rfc_message_id="digest-1",
        subject="Newsletter",
        sender="news@example.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="Some updates...",
        thread_hint=None,
        source_path=None,
    )

    IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=logger).execute(
        ListIncomingReader([incoming]),
        run_id="run",
        command="test",
        record_pipeline=False,
    )

    mid = messages.list_messages_pending_triage(limit=1)[0].id
    triage_repo.save_triage(
        mid,
        TriageResult(
            importance=MessageImportance.HIGH,
            reply_requirement=ReplyRequirement.REQUIRED,
            summary="Important newsletter",
            actionable=True,
            confidence=0.9,
            reason_codes=("news",),
        ),
        raw_json="{}",
    )
    messages.update_processing_status(mid, MessageProcessingStatus.TRIAGED)

    kb_sync = SqliteKanbanSyncRepository(conn, clock)
    uc = BuildMorningDigestUseCase(
        digest_context=digest_ctx,
        digests=digests,
        clock=clock,
        logger=logger,
        lookback_hours=24,
        digest_max_messages=30,
        kanban_sync=kb_sync,
        kanban_provider=KanbanProvider.LOCAL_FILE,
        kanban_auto_sync=False,
    )

    res = uc.execute(run_id="digest-run", pipeline_run_db_id=None, pipeline_stats={"note": "test"})
    assert "Executive summary" in res.markdown
    assert "Critical / High priority messages" in res.markdown
    assert "## Kanban sync" in res.markdown
    assert "Pipeline stats / system notes" in res.markdown


def test_digest_yougile_kanban_ops_line(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    digests = SqliteMorningDigestRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    digest_ctx = SqliteDigestContextRepository(conn)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:digest-yg",
        source=MessageSource.EML,
        rfc_message_id="digest-yg",
        subject="Ping",
        sender="a@b.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="hello",
        thread_hint=None,
        source_path=None,
    )
    IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=logger).execute(
        ListIncomingReader([incoming]),
        run_id="run",
        command="test",
        record_pipeline=False,
    )
    mid = messages.list_messages_pending_triage(limit=1)[0].id
    triage_repo.save_triage(
        mid,
        TriageResult(
            importance=MessageImportance.MEDIUM,
            reply_requirement=ReplyRequirement.NO,
            summary="ok",
            actionable=False,
            confidence=0.8,
            reason_codes=(),
        ),
        raw_json="{}",
    )
    messages.update_processing_status(mid, MessageProcessingStatus.TRIAGED)

    kb_sync = SqliteKanbanSyncRepository(conn, clock)
    uc = BuildMorningDigestUseCase(
        digest_context=digest_ctx,
        digests=digests,
        clock=clock,
        logger=logger,
        lookback_hours=24,
        digest_max_messages=30,
        kanban_sync=kb_sync,
        kanban_provider=KanbanProvider.YOUGILE,
        kanban_auto_sync=False,
    )
    res = uc.execute(run_id="d-yg", pipeline_run_db_id=None, pipeline_stats={})
    assert "yougile" in res.markdown.lower()
    assert "kanban-status" in res.markdown.lower()

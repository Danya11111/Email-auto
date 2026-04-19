from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.dtos import IncomingMessageDTO, TaskExtractionItemDTO, TriageLLMResponseDTO
from app.application.policies import TaskAutomationPolicy
from app.application.use_cases.enqueue_review_items import EnqueueReviewItemsUseCase
from app.application.use_cases.extract_tasks import ExtractTasksUseCase
from app.application.use_cases.ingest_messages import IngestMessagesUseCase
from app.application.use_cases.triage_messages import TriageMessagesUseCase
from app.domain.enums import MessageImportance, MessageProcessingStatus, MessageSource, ReplyRequirement
from app.infrastructure.kanban.stub_adapter import StubKanbanAdapter
from app.infrastructure.storage.repositories import (
    SqliteMessageRepository,
    SqlitePipelineRunRepository,
    SqliteReviewRepository,
    SqliteTaskRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from tests.fakes import FakeTaskLLM, FakeTriageLLM, FixedClock, ListIncomingReader, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "tasks.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()


def test_extract_tasks_creates_candidates(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    tasks_repo = SqliteTaskRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    kanban = StubKanbanAdapter(logger)
    reviews = SqliteReviewRepository(conn, clock)
    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:task-1",
        source=MessageSource.EML,
        rfc_message_id="id-task-1",
        subject="Action items",
        sender="pm@example.com",
        recipients=("you@example.com",),
        received_at=clock.now(),
        body_plain="Please send the invoice today.",
        thread_hint=None,
        source_path=None,
    )

    IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=logger).execute(
        ListIncomingReader([incoming]),
        run_id="run",
        command="test",
        record_pipeline=False,
    )

    triage_llm = FakeTriageLLM(
        TriageLLMResponseDTO(
            importance=MessageImportance.MEDIUM,
            reply_requirement=ReplyRequirement.REQUIRED,
            summary="Invoice requested",
            actionable=True,
            confidence=0.9,
            reason_codes=("request",),
        )
    )
    triage_uc = TriageMessagesUseCase(
        messages=messages,
        triage=triage_repo,
        llm=triage_llm,
        logger=logger,
        enqueue_reviews=enqueue,
        review_threshold=0.70,
    )
    triage_uc.execute(run_id="triage")

    extract_llm = FakeTaskLLM(
        [
            TaskExtractionItemDTO(title="Send invoice", description=None, due_at=None, confidence=0.91),
        ]
    )

    extract_uc = ExtractTasksUseCase(
        messages=messages,
        triage_repo=triage_repo,
        tasks_llm=extract_llm,
        tasks=tasks_repo,
        kanban=kanban,
        logger=logger,
        enqueue_reviews=enqueue,
        review_threshold=0.70,
    )
    policy = TaskAutomationPolicy(confidence_threshold=0.99, auto_create_kanban=False)

    message_id = messages.list_messages_for_task_extraction(limit=10)[0].id
    res = extract_uc.execute(run_id="extract", policy=policy)

    assert res.messages_processed == 1
    assert res.tasks_created == 1
    assert tasks_repo.message_has_candidate_tasks(message_id) is True

    triaged = messages.list_messages_for_task_extraction(limit=10)
    assert triaged == []

    pending = messages.list_messages_pending_triage(limit=10)
    assert pending == []

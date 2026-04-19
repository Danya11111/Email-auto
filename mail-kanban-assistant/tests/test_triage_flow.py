from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.dtos import IncomingMessageDTO, TriageLLMResponseDTO
from app.application.use_cases.enqueue_review_items import EnqueueReviewItemsUseCase
from app.application.use_cases.triage_messages import TriageMessagesUseCase
from app.domain.enums import MessageImportance, MessageProcessingStatus, MessageSource, ReplyRequirement
from app.domain.errors import DuplicateMessageError
from app.infrastructure.storage.repositories import (
    SqliteMessageRepository,
    SqlitePipelineRunRepository,
    SqliteReviewRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from tests.fakes import FakeTriageLLM, FixedClock, ListIncomingReader, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "t.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()


def test_triage_flow_marks_triaged(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:test-1",
        source=MessageSource.EML,
        rfc_message_id="id-1",
        subject="Please review",
        sender="boss@example.com",
        recipients=("you@example.com",),
        received_at=clock.now(),
        body_plain="We need the deck by Friday.",
        thread_hint=None,
        source_path="/tmp/x.eml",
    )

    from app.application.use_cases.ingest_messages import IngestMessagesUseCase

    ingest = IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=logger)
    ingest.execute(ListIncomingReader([incoming]), run_id="run-ingest", command="test", record_pipeline=False)

    llm = FakeTriageLLM(
        TriageLLMResponseDTO(
            importance=MessageImportance.HIGH,
            reply_requirement=ReplyRequirement.REQUIRED,
            summary="Deck requested",
            actionable=True,
            confidence=0.88,
            reason_codes=("deadline",),
        )
    )
    triage_uc = TriageMessagesUseCase(
        messages=messages,
        triage=triage_repo,
        llm=llm,
        logger=logger,
        enqueue_reviews=enqueue,
        review_threshold=0.72,
    )
    triage_uc.execute(run_id="run-triage")

    updated = messages.list_messages_pending_triage(limit=10)
    assert updated == []

    stored = messages.list_messages_for_task_extraction(limit=10)
    assert len(stored) == 1
    assert stored[0].processing_status == MessageProcessingStatus.TRIAGED


def test_triage_low_confidence_enqueues_review_and_blocks_extraction(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:review-1",
        source=MessageSource.EML,
        rfc_message_id="id-review-1",
        subject="Uncertain",
        sender="x@example.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="Maybe important?",
        thread_hint=None,
        source_path=None,
    )

    from app.application.use_cases.ingest_messages import IngestMessagesUseCase

    IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=logger).execute(
        ListIncomingReader([incoming]),
        run_id="run",
        command="test",
        record_pipeline=False,
    )

    llm = FakeTriageLLM(
        TriageLLMResponseDTO(
            importance=MessageImportance.HIGH,
            reply_requirement=ReplyRequirement.OPTIONAL,
            summary="Unclear",
            actionable=True,
            confidence=0.40,
            reason_codes=("uncertain",),
        )
    )
    triage_uc = TriageMessagesUseCase(
        messages=messages,
        triage=triage_repo,
        llm=llm,
        logger=logger,
        enqueue_reviews=enqueue,
        review_threshold=0.72,
    )
    res = triage_uc.execute(run_id="run-triage")
    assert res.reviews_enqueued == 1

    msg = messages.list_messages_for_task_extraction(limit=10)
    assert msg == []

    pending = reviews.list_pending(limit=10)
    assert len(pending) == 1


def test_ingest_is_idempotent_on_duplicate(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    pipeline = SqlitePipelineRunRepository(conn, clock)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:dup",
        source=MessageSource.EML,
        rfc_message_id=None,
        subject="Dup",
        sender="a@b.com",
        recipients=(),
        received_at=None,
        body_plain="same",
        thread_hint=None,
        source_path=None,
    )

    from app.application.use_cases.ingest_messages import IngestMessagesUseCase

    ingest = IngestMessagesUseCase(messages=messages, pipeline_runs=pipeline, logger=logger)
    reader = ListIncomingReader([incoming, incoming])
    res = ingest.execute(reader, run_id="run", command="test", record_pipeline=False)
    assert res.inserted == 1
    assert res.duplicates == 1

    with pytest.raises(DuplicateMessageError):
        messages.insert_message(incoming, body_normalized="same", processing_status=MessageProcessingStatus.INGESTED)

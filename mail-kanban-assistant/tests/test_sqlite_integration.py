from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.dtos import IncomingMessageDTO, ReviewEnqueueCommandDTO
from app.application.use_cases.approve_review_item import ApproveReviewItemUseCase
from app.application.use_cases.enqueue_review_items import EnqueueReviewItemsUseCase
from app.application.use_cases.reject_review_item import RejectReviewItemUseCase
from app.domain.enums import (
    MessageImportance,
    MessageProcessingStatus,
    MessageSource,
    ReplyRequirement,
    ReviewKind,
    TaskStatus,
)
from app.domain.models import ExtractedTask, TriageResult
from app.infrastructure.storage.repositories import (
    SqliteMessageRepository,
    SqliteReviewRepository,
    SqliteTaskRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from tests.fakes import FixedClock, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )


def test_review_enqueue_is_idempotent(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:rev-dedupe",
        source=MessageSource.EML,
        rfc_message_id="rev-1",
        subject="S",
        sender="a@b.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="b",
        thread_hint=None,
        source_path=None,
    )
    mid = messages.insert_message(incoming, body_normalized="b", processing_status=MessageProcessingStatus.TRIAGED)

    cmd = ReviewEnqueueCommandDTO(
        review_kind=ReviewKind.TRIAGE,
        message_id=mid,
        related_task_id=None,
        reason_code="test",
        reason_text="because",
        confidence=0.5,
        payload_json=json.dumps({"message_id": mid}),
    )
    r1 = enqueue.execute(run_id="r1", commands=[cmd])
    r2 = enqueue.execute(run_id="r2", commands=[cmd])
    assert r1.inserted == 1
    assert r2.inserted == 0
    assert r2.skipped_duplicates == 1


def test_approve_triage_review_promotes_message_and_confirms(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    tasks_repo = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:approve-1",
        source=MessageSource.EML,
        rfc_message_id="a1",
        subject="S",
        sender="a@b.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="b",
        thread_hint=None,
        source_path=None,
    )
    mid = messages.insert_message(incoming, body_normalized="b", processing_status=MessageProcessingStatus.AWAITING_REVIEW)
    triage_repo.save_triage(
        mid,
        TriageResult(
            importance=MessageImportance.MEDIUM,
            reply_requirement=ReplyRequirement.OPTIONAL,
            summary="sum",
            actionable=True,
            confidence=0.5,
            reason_codes=("x",),
        ),
        raw_json="{}",
    )
    rid = enqueue.execute(
        run_id="run",
        commands=[
            ReviewEnqueueCommandDTO(
                review_kind=ReviewKind.TRIAGE,
                message_id=mid,
                related_task_id=None,
                reason_code="low_confidence",
                reason_text="test",
                confidence=0.5,
                payload_json="{}",
            )
        ],
    ).inserted
    assert rid == 1

    approve = ApproveReviewItemUseCase(
        reviews=reviews,
        messages=messages,
        triage=triage_repo,
        tasks=tasks_repo,
        logger=logger,
    )
    pending = reviews.list_pending(limit=10)
    approve.execute(review_id=pending[0].id, decided_by="tester", note=None)

    msg = messages.list_messages_for_task_extraction(limit=10)[0]
    assert msg.processing_status == MessageProcessingStatus.TRIAGED


def test_reject_triage_review_resets_to_ingested(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    tasks_repo = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:reject-1",
        source=MessageSource.EML,
        rfc_message_id="r1",
        subject="S",
        sender="a@b.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="b",
        thread_hint=None,
        source_path=None,
    )
    mid = messages.insert_message(incoming, body_normalized="b", processing_status=MessageProcessingStatus.AWAITING_REVIEW)
    triage_repo.save_triage(
        mid,
        TriageResult(
            importance=MessageImportance.MEDIUM,
            reply_requirement=ReplyRequirement.OPTIONAL,
            summary="sum",
            actionable=True,
            confidence=0.5,
            reason_codes=("x",),
        ),
        raw_json="{}",
    )
    enqueue.execute(
        run_id="run",
        commands=[
            ReviewEnqueueCommandDTO(
                review_kind=ReviewKind.TRIAGE,
                message_id=mid,
                related_task_id=None,
                reason_code="low_confidence",
                reason_text="test",
                confidence=0.5,
                payload_json="{}",
            )
        ],
    )

    reject = RejectReviewItemUseCase(
        reviews=reviews,
        messages=messages,
        triage=triage_repo,
        tasks=tasks_repo,
        logger=logger,
    )
    pending = reviews.list_pending(limit=10)
    reject.execute(review_id=pending[0].id, decided_by="tester", note="nope")

    row = conn.execute("SELECT processing_status FROM messages WHERE id = ?", (mid,)).fetchone()
    assert str(row["processing_status"]) == MessageProcessingStatus.INGESTED.value
    assert triage_repo.get_triage(mid) is None


def test_reject_task_review_marks_task_rejected(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    messages = SqliteMessageRepository(conn, clock)
    triage_repo = SqliteTriageRepository(conn, clock)
    tasks_repo = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    enqueue = EnqueueReviewItemsUseCase(reviews=reviews, logger=logger)

    incoming = IncomingMessageDTO(
        dedupe_key="eml:taskrej-1",
        source=MessageSource.EML,
        rfc_message_id="t1",
        subject="S",
        sender="a@b.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="b",
        thread_hint=None,
        source_path=None,
    )
    mid = messages.insert_message(incoming, body_normalized="b", processing_status=MessageProcessingStatus.TASKS_EXTRACTED)
    saved = tasks_repo.save_candidate_tasks(
        mid,
        [ExtractedTask(title="t", description=None, due_at=None, confidence=0.5, status=TaskStatus.CANDIDATE)],
        dedupe_keys=[f"{mid}:t"],
    )
    tid = saved[0].task_id

    enqueue.execute(
        run_id="run",
        commands=[
            ReviewEnqueueCommandDTO(
                review_kind=ReviewKind.TASK,
                message_id=mid,
                related_task_id=tid,
                reason_code="low_task_confidence",
                reason_text="test",
                confidence=0.5,
                payload_json="{}",
            )
        ],
    )

    reject = RejectReviewItemUseCase(
        reviews=reviews,
        messages=messages,
        triage=triage_repo,
        tasks=tasks_repo,
        logger=logger,
    )
    pending = reviews.list_pending(limit=10)
    reject.execute(review_id=pending[0].id, decided_by="tester", note=None)

    row = conn.execute("SELECT status FROM extracted_tasks WHERE id = ?", (tid,)).fetchone()
    assert str(row["status"]) == TaskStatus.REJECTED.value


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "int.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()

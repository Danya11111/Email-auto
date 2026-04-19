from __future__ import annotations

from datetime import UTC, datetime

from app.domain.enums import (
    MessageImportance,
    MessageProcessingStatus,
    MessageSource,
    ReplyRequirement,
    TaskStatus,
)
from app.domain.errors import ValidationError
from app.domain.models import ExtractedTask, Message, TriageResult


def test_message_is_frozen_and_typed() -> None:
    received = datetime(2026, 1, 2, tzinfo=UTC)
    msg = Message(
        dedupe_key="eml:<foo@bar>",
        source=MessageSource.EML,
        rfc_message_id=None,
        subject="Hello",
        sender="a@b.com",
        recipients=("c@d.com",),
        received_at=received,
        body_plain="Body",
        thread_hint=None,
        processing_status=MessageProcessingStatus.INGESTED,
    )
    assert msg.source == MessageSource.EML
    assert msg.recipients == ("c@d.com",)


def test_triage_result_reason_codes_immutable_view() -> None:
    triage = TriageResult(
        importance=MessageImportance.MEDIUM,
        reply_requirement=ReplyRequirement.OPTIONAL,
        summary="s",
        actionable=False,
        confidence=0.2,
        reason_codes=("a", "b"),
    )
    assert triage.reason_codes == ("a", "b")


def test_extracted_task_status_enum() -> None:
    task = ExtractedTask(
        title="t",
        description=None,
        due_at=None,
        confidence=0.8,
        status=TaskStatus.CANDIDATE,
    )
    assert task.status == TaskStatus.CANDIDATE


def test_validation_error_is_domain_error() -> None:
    err = ValidationError("bad")
    assert str(err) == "bad"

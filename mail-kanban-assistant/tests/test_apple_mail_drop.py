from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.application.use_cases.process_apple_mail_drop import ProcessAppleMailDropUseCase
from app.domain.enums import IngestedArtifactStatus, MessageSource
from app.infrastructure.fs.maildrop_filesystem import OsMaildropFilesystem
from app.infrastructure.mail.apple_mail_drop_reader import AppleMailDropIncomingScanner
from app.infrastructure.storage.repositories import SqliteMessageRepository
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from app.infrastructure.storage.sqlite_ingested_artifact_repository import SqliteIngestedArtifactRepository
from tests.fakes import FixedClock, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )


def _snapshot(**overrides: object) -> str:
    doc: dict[str, object] = {
        "snapshot_id": "snap-a",
        "source": "apple_mail_drop",
        "message_id": "msg-a",
        "thread_id": None,
        "mailbox_name": None,
        "account_name": None,
        "subject": "Hello",
        "sender_name": None,
        "sender_email": "a@b.com",
        "to": ["c@d.com"],
        "cc": [],
        "bcc": [],
        "date": "2026-04-19T10:00:00+00:00",
        "body_text": "Body here",
        "body_preview": None,
        "unread": None,
        "flagged": None,
        "received_at": "2026-04-19T10:00:00+00:00",
        "collected_at": "2026-04-19T11:00:00+00:00",
        "attachments_summary": None,
        "raw_metadata": {},
    }
    doc.update(overrides)
    return json.dumps(doc, ensure_ascii=False)


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "drop.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()


def test_apple_mail_drop_valid_snapshot_inserts_message(conn: object, tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    root = tmp_path / "maildrop"
    incoming = root / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "a.json").write_text(_snapshot(snapshot_id="snap-1", message_id="mid-1"), encoding="utf-8")

    uc = ProcessAppleMailDropUseCase(
        messages=SqliteMessageRepository(conn, clock),
        artifacts=SqliteIngestedArtifactRepository(conn, clock),
        fs=OsMaildropFilesystem(logger),
        scanner=AppleMailDropIncomingScanner(),
        logger=logger,
    )
    res = uc.execute(maildrop_root=root, run_id="r1")
    assert res.found == 1
    assert res.ingested == 1
    assert res.duplicate == 0
    assert res.failed == 0
    assert res.moved_processed == 1
    assert not (incoming / "a.json").exists()
    assert (root / "processed" / "a.json").exists()

    row = conn.execute("SELECT COUNT(1) AS c FROM messages WHERE source = ?", (MessageSource.APPLE_MAIL_DROP.value,)).fetchone()
    assert int(row["c"]) == 1


def test_apple_mail_drop_invalid_json_moves_failed_without_stopping_batch(conn: object, tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    root = tmp_path / "maildrop"
    incoming = root / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "bad.json").write_text("{", encoding="utf-8")
    (incoming / "good.json").write_text(_snapshot(snapshot_id="snap-ok", message_id="mid-ok"), encoding="utf-8")

    uc = ProcessAppleMailDropUseCase(
        messages=SqliteMessageRepository(conn, clock),
        artifacts=SqliteIngestedArtifactRepository(conn, clock),
        fs=OsMaildropFilesystem(logger),
        scanner=AppleMailDropIncomingScanner(),
        logger=logger,
    )
    res = uc.execute(maildrop_root=root, run_id="r2")
    assert res.found == 2
    assert res.ingested == 1
    assert res.failed == 1
    assert res.moved_failed == 1
    assert res.moved_processed == 1
    assert (root / "failed" / "bad.json").exists()
    assert (root / "processed" / "good.json").exists()


def test_apple_mail_drop_repeat_run_is_safe(conn: object, tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    root = tmp_path / "maildrop"
    incoming = root / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "one.json").write_text(_snapshot(snapshot_id="snap-r", message_id="mid-r"), encoding="utf-8")

    uc = ProcessAppleMailDropUseCase(
        messages=SqliteMessageRepository(conn, clock),
        artifacts=SqliteIngestedArtifactRepository(conn, clock),
        fs=OsMaildropFilesystem(logger),
        scanner=AppleMailDropIncomingScanner(),
        logger=logger,
    )
    r1 = uc.execute(maildrop_root=root, run_id="r3")
    assert r1.ingested == 1
    shutil.copy2(root / "processed" / "one.json", incoming / "one.json")
    r2 = uc.execute(maildrop_root=root, run_id="r4")
    assert r2.duplicate == 1
    assert r2.ingested == 0
    row = conn.execute("SELECT COUNT(1) AS c FROM messages").fetchone()
    assert int(row["c"]) == 1


def test_ingested_artifact_repository_mark_and_dedupe(conn: object) -> None:
    from app.application.dtos import IncomingMessageDTO
    from app.domain.enums import MessageProcessingStatus

    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    messages = SqliteMessageRepository(conn, clock)
    incoming = IncomingMessageDTO(
        dedupe_key="artifact-test",
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
    mid = messages.insert_message(incoming, body_normalized="b", processing_status=MessageProcessingStatus.INGESTED)

    repo = SqliteIngestedArtifactRepository(conn, clock)
    h = "deadbeef" * 8
    i1 = repo.register_incoming_artifact(content_hash=h, source_type="apple_mail_drop", original_filename="x.json")
    i2 = repo.register_incoming_artifact(content_hash=h, source_type="apple_mail_drop", original_filename="x.json")
    assert i1 == i2
    repo.set_snapshot_id(i1, "snap-x")
    repo.mark_artifact_processed(artifact_id=i1, related_message_id=mid)
    assert repo.check_artifact_already_processed(content_hash=h) is True
    row = conn.execute("SELECT status, related_message_id FROM ingested_artifacts WHERE id = ?", (i1,)).fetchone()
    assert str(row["status"]) == IngestedArtifactStatus.PROCESSED.value
    assert int(row["related_message_id"]) == mid


def test_find_message_id_by_dedupe_key(conn: object) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    from app.application.dtos import IncomingMessageDTO
    from app.domain.enums import MessageProcessingStatus

    messages = SqliteMessageRepository(conn, clock)
    incoming = IncomingMessageDTO(
        dedupe_key="apple_mail_drop:mid-z",
        source=MessageSource.APPLE_MAIL_DROP,
        rfc_message_id="mid-z",
        subject="S",
        sender="s@x.com",
        recipients=(),
        received_at=clock.now(),
        body_plain="b",
        thread_hint=None,
        source_path=None,
    )
    mid = messages.insert_message(incoming, body_normalized="b", processing_status=MessageProcessingStatus.INGESTED)
    found = messages.find_message_id_by_dedupe_key("apple_mail_drop:mid-z")
    assert found == mid

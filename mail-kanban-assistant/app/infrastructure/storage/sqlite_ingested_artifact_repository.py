from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.application.dtos import IngestedArtifactRecordDTO
from app.application.ports import ClockPort, IngestedArtifactRepositoryPort
from app.domain.enums import IngestedArtifactStatus


def _parse_dt(value: str | None, *, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_artifact_row(row: sqlite3.Row, clock: ClockPort) -> IngestedArtifactRecordDTO:
    now = clock.now()
    return IngestedArtifactRecordDTO(
        id=int(row["id"]),
        content_hash=str(row["content_hash"]),
        snapshot_id=row["snapshot_id"],
        source_type=str(row["source_type"]),
        original_filename=str(row["original_filename"]),
        related_message_id=int(row["related_message_id"]) if row["related_message_id"] is not None else None,
        status=IngestedArtifactStatus(str(row["status"])),
        first_seen_at=_parse_dt(row["first_seen_at"], fallback=now),
        processed_at=_parse_dt(row["processed_at"], fallback=now) if row["processed_at"] else None,
        error_text=row["error_text"],
    )


class SqliteIngestedArtifactRepository(IngestedArtifactRepositoryPort):
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def maybe_find_artifact_by_hash_or_snapshot_id(
        self, *, content_hash: str, snapshot_id: str | None
    ) -> IngestedArtifactRecordDTO | None:
        row = self._conn.execute(
            "SELECT * FROM ingested_artifacts WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
        if row is not None:
            return _parse_artifact_row(row, self._clock)
        if snapshot_id:
            row = self._conn.execute(
                "SELECT * FROM ingested_artifacts WHERE snapshot_id = ? ORDER BY id DESC LIMIT 1",
                (snapshot_id,),
            ).fetchone()
            if row is not None:
                return _parse_artifact_row(row, self._clock)
        return None

    def check_artifact_already_processed(self, *, content_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT status FROM ingested_artifacts WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
        return row is not None and str(row["status"]) == IngestedArtifactStatus.PROCESSED.value

    def register_incoming_artifact(self, *, content_hash: str, source_type: str, original_filename: str) -> int:
        now = self._clock.now().isoformat()
        try:
            cur = self._conn.execute(
                """
                INSERT INTO ingested_artifacts (
                  content_hash, snapshot_id, source_type, original_filename,
                  related_message_id, status, first_seen_at, processed_at, error_text
                ) VALUES (?, NULL, ?, ?, NULL, ?, ?, NULL, NULL)
                """,
                (
                    content_hash,
                    source_type,
                    original_filename,
                    IngestedArtifactStatus.PENDING.value,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            self._conn.rollback()
            row = self._conn.execute(
                "SELECT * FROM ingested_artifacts WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            if row is None:
                raise
            aid = int(row["id"])
            if str(row["status"]) == IngestedArtifactStatus.FAILED.value:
                self.reset_failed_artifact_to_pending(aid)
            return aid

    def set_snapshot_id(self, artifact_id: int, snapshot_id: str) -> None:
        self._conn.execute(
            "UPDATE ingested_artifacts SET snapshot_id = ? WHERE id = ?",
            (snapshot_id, artifact_id),
        )
        self._conn.commit()

    def mark_artifact_processed(self, *, artifact_id: int, related_message_id: int) -> None:
        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            UPDATE ingested_artifacts
            SET status = ?, processed_at = ?, related_message_id = ?, error_text = NULL
            WHERE id = ?
            """,
            (IngestedArtifactStatus.PROCESSED.value, now, related_message_id, artifact_id),
        )
        self._conn.commit()

    def mark_artifact_failed(self, *, artifact_id: int, error_text: str) -> None:
        self._conn.execute(
            """
            UPDATE ingested_artifacts
            SET status = ?, error_text = ?, related_message_id = NULL, processed_at = NULL, snapshot_id = NULL
            WHERE id = ?
            """,
            (IngestedArtifactStatus.FAILED.value, error_text[:4000], artifact_id),
        )
        self._conn.commit()

    def reset_failed_artifact_to_pending(self, artifact_id: int) -> None:
        self._conn.execute(
            """
            UPDATE ingested_artifacts
            SET status = ?, error_text = NULL, related_message_id = NULL, processed_at = NULL, snapshot_id = NULL
            WHERE id = ? AND status = ?
            """,
            (IngestedArtifactStatus.PENDING.value, artifact_id, IngestedArtifactStatus.FAILED.value),
        )
        self._conn.commit()

    def find_artifact_with_snapshot_id(self, *, snapshot_id: str, exclude_artifact_id: int) -> IngestedArtifactRecordDTO | None:
        row = self._conn.execute(
            """
            SELECT * FROM ingested_artifacts
            WHERE snapshot_id = ? AND id != ?
            LIMIT 1
            """,
            (snapshot_id, exclude_artifact_id),
        ).fetchone()
        return _parse_artifact_row(row, self._clock) if row is not None else None

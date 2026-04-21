from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Sequence

from app.application.dtos import KanbanDigestSectionDTO, KanbanStatusSummaryDTO, KanbanSyncRecordRowDTO
from app.application.ports import ClockPort, KanbanSyncRepositoryPort
from app.domain.enums import KanbanProvider, KanbanSyncStatus


def _parse_dt(value: str | None, *, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row_get(row: sqlite3.Row, key: str) -> str | None:
    if key not in row.keys():
        return None
    v = row[key]
    return None if v is None else str(v)


def _row_to_dto(row: sqlite3.Row, clock: ClockPort) -> KanbanSyncRecordRowDTO:
    now = clock.now()
    return KanbanSyncRecordRowDTO(
        id=int(row["id"]),
        task_id=int(row["task_id"]),
        provider=KanbanProvider(str(row["provider"])),
        sync_status=KanbanSyncStatus(str(row["sync_status"])),
        external_card_id=row["external_card_id"],
        external_card_url=row["external_card_url"],
        card_fingerprint=str(row["card_fingerprint"]),
        payload_json=str(row["payload_json"]),
        created_at=_parse_dt(row["created_at"], fallback=now),
        synced_at=_parse_dt(row["synced_at"], fallback=now) if row["synced_at"] else None,
        last_attempt_at=_parse_dt(row["last_attempt_at"], fallback=now) if row["last_attempt_at"] else None,
        last_error=row["last_error"],
        retry_count=int(row["retry_count"]),
        last_outbound_action=_row_get(row, "last_outbound_action"),
        last_operation_note=_row_get(row, "last_operation_note"),
        previous_fingerprint=_row_get(row, "previous_fingerprint"),
        previous_external_card_url=_row_get(row, "previous_external_card_url"),
        record_updated_at=_parse_dt(row["record_updated_at"], fallback=now) if _row_get(row, "record_updated_at") else None,
    )


class SqliteKanbanSyncRepository(KanbanSyncRepositoryPort):
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def get_sync_record_for_task(self, task_id: int, provider: KanbanProvider) -> KanbanSyncRecordRowDTO | None:
        row = self._conn.execute(
            "SELECT * FROM kanban_sync_records WHERE task_id = ? AND provider = ? LIMIT 1",
            (task_id, provider.value),
        ).fetchone()
        return _row_to_dto(row, self._clock) if row is not None else None

    def maybe_skip_if_already_synced_same_fingerprint(
        self, *, task_id: int, provider: KanbanProvider, fingerprint: str
    ) -> bool:
        row = self._conn.execute(
            """
            SELECT sync_status, card_fingerprint FROM kanban_sync_records
            WHERE task_id = ? AND provider = ?
            LIMIT 1
            """,
            (task_id, provider.value),
        ).fetchone()
        if row is None:
            return False
        return str(row["sync_status"]) == KanbanSyncStatus.SYNCED.value and str(row["card_fingerprint"]) == fingerprint

    def upsert_pending_sync_record(
        self, *, task_id: int, provider: KanbanProvider, fingerprint: str, payload_json: str
    ) -> int:
        now = self._clock.now().isoformat()
        existing = self.get_sync_record_for_task(task_id, provider)
        if existing is None:
            cur = self._conn.execute(
                """
                INSERT INTO kanban_sync_records (
                  task_id, provider, sync_status, external_card_id, external_card_url,
                  card_fingerprint, payload_json, created_at, synced_at, last_attempt_at, last_error, retry_count,
                  record_updated_at
                ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL, NULL, 0, ?)
                """,
                (
                    task_id,
                    provider.value,
                    KanbanSyncStatus.PENDING.value,
                    fingerprint,
                    payload_json,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

        if existing.sync_status == KanbanSyncStatus.SYNCED and existing.card_fingerprint == fingerprint:
            return existing.id

        self._conn.execute(
            """
            UPDATE kanban_sync_records
            SET sync_status = ?,
                card_fingerprint = ?,
                payload_json = ?,
                external_card_id = NULL,
                external_card_url = NULL,
                synced_at = NULL,
                last_error = NULL,
                record_updated_at = ?
            WHERE id = ?
            """,
            (
                KanbanSyncStatus.PENDING.value,
                fingerprint,
                payload_json,
                now,
                existing.id,
            ),
        )
        self._conn.commit()
        return existing.id

    def mark_sync_success(
        self,
        *,
        record_id: int,
        fingerprint: str,
        external_card_id: str | None,
        external_card_url: str | None,
        outbound_action: str | None = None,
    ) -> None:
        now = self._clock.now().isoformat()
        prev = self._conn.execute(
            "SELECT card_fingerprint, external_card_url FROM kanban_sync_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        prev_fp = str(prev["card_fingerprint"]) if prev is not None else None
        prev_url = str(prev["external_card_url"]) if prev is not None and prev["external_card_url"] else None
        action = (outbound_action or "sync_success")[:128]
        self._conn.execute(
            """
            UPDATE kanban_sync_records
            SET sync_status = ?, synced_at = ?, last_attempt_at = ?, last_error = NULL,
                previous_fingerprint = ?, previous_external_card_url = ?,
                external_card_id = ?, external_card_url = ?, card_fingerprint = ?,
                last_outbound_action = ?, last_operation_note = NULL,
                record_updated_at = ?
            WHERE id = ?
            """,
            (
                KanbanSyncStatus.SYNCED.value,
                now,
                now,
                prev_fp,
                prev_url,
                external_card_id,
                external_card_url,
                fingerprint,
                action,
                now,
                record_id,
            ),
        )
        self._conn.commit()

    def mark_sync_failed(self, *, record_id: int, error: str) -> None:
        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            UPDATE kanban_sync_records
            SET sync_status = ?, last_error = ?, last_attempt_at = ?, retry_count = retry_count + 1,
                last_outbound_action = 'failed', last_operation_note = ?, record_updated_at = ?
            WHERE id = ?
            """,
            (KanbanSyncStatus.FAILED.value, error[:4000], now, error[:512], now, record_id),
        )
        self._conn.commit()

    def mark_sync_skipped(self, *, record_id: int, reason: str) -> None:
        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            UPDATE kanban_sync_records
            SET sync_status = ?, last_error = ?, last_attempt_at = ?,
                last_outbound_action = 'skipped', last_operation_note = ?, record_updated_at = ?
            WHERE id = ?
            """,
            (KanbanSyncStatus.SKIPPED.value, reason[:4000], now, reason[:512], now, record_id),
        )
        self._conn.commit()

    def record_outbound_audit_preserve_synced(
        self, *, record_id: int, outbound_action: str, operation_note: str | None
    ) -> None:
        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            UPDATE kanban_sync_records
            SET last_outbound_action = ?, last_operation_note = ?, last_attempt_at = ?, record_updated_at = ?
            WHERE id = ?
            """,
            (outbound_action[:128], (operation_note or "")[:512] or None, now, now, record_id),
        )
        self._conn.commit()

    def list_task_ids_for_resync_changed(self, provider: KanbanProvider, limit: int) -> tuple[int, ...]:
        rows = self._conn.execute(
            """
            SELECT k.task_id AS tid
            FROM kanban_sync_records k
            JOIN extracted_tasks et ON et.id = k.task_id
            WHERE k.provider = ?
              AND k.sync_status = ?
              AND k.external_card_id IS NOT NULL AND length(trim(k.external_card_id)) > 0
              AND et.status IN ('approved', 'synced')
            ORDER BY datetime(COALESCE(k.record_updated_at, k.synced_at, k.last_attempt_at, k.created_at)) ASC
            LIMIT ?
            """,
            (provider.value, KanbanSyncStatus.SYNCED.value, limit),
        ).fetchall()
        return tuple(int(r["tid"]) for r in rows)

    def list_pending_sync_records(self, provider: KanbanProvider, limit: int) -> Sequence[KanbanSyncRecordRowDTO]:
        rows = self._conn.execute(
            """
            SELECT * FROM kanban_sync_records
            WHERE provider = ? AND sync_status = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (provider.value, KanbanSyncStatus.PENDING.value, limit),
        ).fetchall()
        return tuple(_row_to_dto(r, self._clock) for r in rows)

    def list_failed_sync_records(
        self, provider: KanbanProvider, *, limit: int, max_retry: int
    ) -> Sequence[KanbanSyncRecordRowDTO]:
        rows = self._conn.execute(
            """
            SELECT * FROM kanban_sync_records
            WHERE provider = ? AND sync_status = ? AND retry_count < ?
            ORDER BY datetime(COALESCE(last_attempt_at, created_at)) ASC
            LIMIT ?
            """,
            (provider.value, KanbanSyncStatus.FAILED.value, max_retry, limit),
        ).fetchall()
        return tuple(_row_to_dto(r, self._clock) for r in rows)

    def load_kanban_digest_section(self, *, provider: KanbanProvider, auto_sync_enabled: bool) -> KanbanDigestSectionDTO:
        p = provider.value
        pending = int(
            self._conn.execute(
                "SELECT COUNT(1) AS c FROM kanban_sync_records WHERE provider = ? AND sync_status = ?",
                (p, KanbanSyncStatus.PENDING.value),
            ).fetchone()["c"]
        )
        failed = int(
            self._conn.execute(
                "SELECT COUNT(1) AS c FROM kanban_sync_records WHERE provider = ? AND sync_status = ?",
                (p, KanbanSyncStatus.FAILED.value),
            ).fetchone()["c"]
        )
        synced = int(
            self._conn.execute(
                "SELECT COUNT(1) AS c FROM kanban_sync_records WHERE provider = ? AND sync_status = ?",
                (p, KanbanSyncStatus.SYNCED.value),
            ).fetchone()["c"]
        )
        approved_ready = int(
            self._conn.execute(
                """
                SELECT COUNT(1) AS c FROM extracted_tasks et
                WHERE et.status = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM kanban_sync_records k
                    WHERE k.task_id = et.id AND k.provider = ? AND k.sync_status = ?
                  )
                """,
                ("approved", p, KanbanSyncStatus.SYNCED.value),
            ).fetchone()["c"]
        )
        err_rows = self._conn.execute(
            """
            SELECT last_error FROM kanban_sync_records
            WHERE provider = ? AND last_error IS NOT NULL
            ORDER BY datetime(COALESCE(last_attempt_at, created_at)) DESC
            LIMIT 5
            """,
            (p,),
        ).fetchall()
        errors = tuple(str(r["last_error"]) for r in err_rows if r["last_error"])

        since = (self._clock.now() - timedelta(hours=24)).isoformat()
        outbound_updates_last_24h = int(
            self._conn.execute(
                """
                SELECT COUNT(1) AS c FROM kanban_sync_records
                WHERE provider = ?
                  AND synced_at IS NOT NULL AND datetime(synced_at) >= datetime(?)
                  AND last_outbound_action IN ('create', 'update_existing', 'update', 'sync_success')
                """,
                (p, since),
            ).fetchone()["c"]
        )
        manual_resync_pending = int(
            self._conn.execute(
                """
                SELECT COUNT(1) AS c FROM kanban_sync_records
                WHERE provider = ? AND sync_status = ? AND last_outbound_action = ?
                """,
                (p, KanbanSyncStatus.SYNCED.value, "skip_manual_resync"),
            ).fetchone()["c"]
        )

        return KanbanDigestSectionDTO(
            provider=p,
            auto_sync_enabled=auto_sync_enabled,
            approved_ready_to_sync=approved_ready,
            pending_outbox=pending,
            synced=synced,
            failed=failed,
            recent_errors=errors,
            outbound_updates_last_24h=outbound_updates_last_24h,
            manual_resync_pending=manual_resync_pending,
        )

    def load_status_summary(self, provider: KanbanProvider) -> KanbanStatusSummaryDTO:
        p = provider.value
        rows = self._conn.execute(
            """
            SELECT sync_status, COUNT(1) AS c
            FROM kanban_sync_records
            WHERE provider = ?
            GROUP BY sync_status
            """,
            (p,),
        ).fetchall()
        counts = {s.value: 0 for s in KanbanSyncStatus}
        for row in rows:
            counts[str(row["sync_status"])] = int(row["c"])
        err_rows = self._conn.execute(
            """
            SELECT last_error FROM kanban_sync_records
            WHERE provider = ? AND last_error IS NOT NULL AND sync_status = ?
            ORDER BY datetime(COALESCE(last_attempt_at, created_at)) DESC
            LIMIT 8
            """,
            (p, KanbanSyncStatus.FAILED.value),
        ).fetchall()
        errors = tuple(str(r["last_error"]) for r in err_rows if r["last_error"])

        act_rows = self._conn.execute(
            """
            SELECT last_outbound_action FROM kanban_sync_records
            WHERE provider = ? AND last_outbound_action IS NOT NULL
            ORDER BY datetime(COALESCE(record_updated_at, last_attempt_at, created_at)) DESC
            LIMIT 8
            """,
            (p,),
        ).fetchall()
        actions = tuple(str(r["last_outbound_action"]) for r in act_rows if r["last_outbound_action"])

        since = (self._clock.now() - timedelta(hours=24)).isoformat()
        outbound_updates_last_24h = int(
            self._conn.execute(
                """
                SELECT COUNT(1) AS c FROM kanban_sync_records
                WHERE provider = ?
                  AND synced_at IS NOT NULL AND datetime(synced_at) >= datetime(?)
                  AND last_outbound_action IN ('create', 'update_existing', 'update', 'sync_success')
                """,
                (p, since),
            ).fetchone()["c"]
        )
        manual_resync_pending = int(
            self._conn.execute(
                """
                SELECT COUNT(1) AS c FROM kanban_sync_records
                WHERE provider = ? AND sync_status = ? AND last_outbound_action = ?
                """,
                (p, KanbanSyncStatus.SYNCED.value, "skip_manual_resync"),
            ).fetchone()["c"]
        )

        return KanbanStatusSummaryDTO(
            provider=provider,
            pending=counts.get(KanbanSyncStatus.PENDING.value, 0),
            synced=counts.get(KanbanSyncStatus.SYNCED.value, 0),
            failed=counts.get(KanbanSyncStatus.FAILED.value, 0),
            skipped=counts.get(KanbanSyncStatus.SKIPPED.value, 0),
            last_errors=errors,
            last_outbound_actions=actions,
            manual_resync_pending=manual_resync_pending,
            outbound_updates_last_24h=outbound_updates_last_24h,
        )

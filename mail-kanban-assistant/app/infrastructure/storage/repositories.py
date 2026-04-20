from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Sequence

from app.application.dtos import (
    DailyDigestContextDTO,
    DailyDigestStatsDTO,
    DigestMessageSnapshotDTO,
    DigestReviewSnapshotDTO,
    DigestTaskSnapshotDTO,
    IncomingMessageDTO,
    PersistedExtractedTaskDTO,
    PersistedMessageDTO,
    ReviewEnqueueCommandDTO,
    ReviewListItemDTO,
    SavedCandidateTaskDTO,
    TaskKanbanSourceContextDTO,
)
from app.application.ports import ClockPort
from app.domain.enums import (
    MessageImportance,
    MessageProcessingStatus,
    MessageSource,
    ReplyRequirement,
    ReviewKind,
    ReviewStatus,
    TaskStatus,
)
from app.domain.errors import DuplicateMessageError, ReviewDecisionError
from app.domain.models import ExtractedTask, MorningDigest, TriageResult


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).isoformat()
    return dt.astimezone(UTC).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class SqliteMessageRepository:
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def insert_message(
        self,
        message: IncomingMessageDTO,
        body_normalized: str,
        processing_status: MessageProcessingStatus,
    ) -> int:
        now = self._clock.now().isoformat()
        try:
            cur = self._conn.execute(
                """
                INSERT INTO messages (
                  dedupe_key, source, rfc_message_id, subject, sender, recipients_json,
                  received_at, body_plain, body_normalized, thread_hint, processing_status,
                  created_at, updated_at, source_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.dedupe_key,
                    message.source.value,
                    message.rfc_message_id,
                    message.subject,
                    message.sender,
                    json.dumps(list(message.recipients), ensure_ascii=False),
                    _iso(message.received_at),
                    message.body_plain,
                    body_normalized,
                    message.thread_hint,
                    processing_status.value,
                    now,
                    now,
                    message.source_path,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            if "dedupe_key" in str(exc).lower() or "unique" in str(exc).lower():
                raise DuplicateMessageError("duplicate dedupe_key") from exc
            raise

    def find_message_id_by_dedupe_key(self, dedupe_key: str) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM messages WHERE dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def list_messages_pending_triage(self, limit: int) -> Sequence[PersistedMessageDTO]:
        rows = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE processing_status = ?
            ORDER BY datetime(COALESCE(received_at, created_at)) ASC
            LIMIT ?
            """,
            (MessageProcessingStatus.INGESTED.value, limit),
        ).fetchall()
        return [self._to_dto(r) for r in rows]

    def list_messages_for_task_extraction(self, limit: int) -> Sequence[PersistedMessageDTO]:
        rows = self._conn.execute(
            """
            SELECT m.* FROM messages m
            WHERE m.processing_status = ?
            ORDER BY datetime(COALESCE(m.received_at, m.created_at)) ASC
            LIMIT ?
            """,
            (MessageProcessingStatus.TRIAGED.value, limit),
        ).fetchall()
        return [self._to_dto(r) for r in rows]

    def list_messages_for_digest(self, window_start: datetime, window_end: datetime) -> Sequence[PersistedMessageDTO]:
        rows = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE datetime(COALESCE(received_at, created_at)) >= datetime(?)
              AND datetime(COALESCE(received_at, created_at)) < datetime(?)
            ORDER BY datetime(COALESCE(received_at, created_at)) ASC
            """,
            (window_start.isoformat(), window_end.isoformat()),
        ).fetchall()
        return [self._to_dto(r) for r in rows]

    def update_processing_status(self, message_id: int, status: MessageProcessingStatus) -> None:
        now = self._clock.now().isoformat()
        self._conn.execute(
            "UPDATE messages SET processing_status = ?, updated_at = ? WHERE id = ?",
            (status.value, now, message_id),
        )
        self._conn.commit()

    def _to_dto(self, row: sqlite3.Row) -> PersistedMessageDTO:
        recipients = tuple(json.loads(row["recipients_json"]))
        return PersistedMessageDTO(
            id=int(row["id"]),
            dedupe_key=str(row["dedupe_key"]),
            source=MessageSource(str(row["source"])),
            rfc_message_id=row["rfc_message_id"],
            subject=row["subject"],
            sender=row["sender"],
            recipients=recipients,
            received_at=_parse_dt(row["received_at"]),
            body_plain=str(row["body_plain"]),
            body_normalized=str(row["body_normalized"]),
            thread_hint=row["thread_hint"],
            processing_status=MessageProcessingStatus(str(row["processing_status"])),
        )


class SqliteTriageRepository:
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def save_triage(self, message_id: int, triage: TriageResult, raw_json: str) -> None:
        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            INSERT INTO triage_results (
              message_id, importance, reply_requirement, summary, actionable, confidence,
              reason_codes_json, raw_json, created_at, human_confirmed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(message_id) DO UPDATE SET
              importance=excluded.importance,
              reply_requirement=excluded.reply_requirement,
              summary=excluded.summary,
              actionable=excluded.actionable,
              confidence=excluded.confidence,
              reason_codes_json=excluded.reason_codes_json,
              raw_json=excluded.raw_json,
              created_at=excluded.created_at,
              human_confirmed=triage_results.human_confirmed
            """,
            (
                message_id,
                triage.importance.value,
                triage.reply_requirement.value,
                triage.summary,
                1 if triage.actionable else 0,
                float(triage.confidence),
                json.dumps(list(triage.reason_codes), ensure_ascii=False),
                raw_json,
                now,
            ),
        )
        self._conn.commit()

    def has_triage(self, message_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM triage_results WHERE message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        return row is not None

    def get_triage(self, message_id: int) -> TriageResult | None:
        row = self._conn.execute("SELECT * FROM triage_results WHERE message_id = ?", (message_id,)).fetchone()
        if row is None:
            return None
        codes = tuple(json.loads(row["reason_codes_json"]))
        return TriageResult(
            importance=MessageImportance(str(row["importance"])),
            reply_requirement=ReplyRequirement(str(row["reply_requirement"])),
            summary=str(row["summary"]),
            actionable=bool(row["actionable"]),
            confidence=float(row["confidence"]),
            reason_codes=codes,
        )

    def delete_for_message(self, message_id: int) -> None:
        self._conn.execute("DELETE FROM triage_results WHERE message_id = ?", (message_id,))
        self._conn.commit()

    def set_human_confirmed(self, message_id: int, *, confirmed: bool) -> None:
        self._conn.execute(
            "UPDATE triage_results SET human_confirmed = ? WHERE message_id = ?",
            (1 if confirmed else 0, message_id),
        )
        self._conn.commit()


class SqliteTaskRepository:
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def save_candidate_tasks(
        self, message_id: int, tasks: Sequence[ExtractedTask], dedupe_keys: Sequence[str]
    ) -> Sequence[SavedCandidateTaskDTO]:
        if len(tasks) != len(dedupe_keys):
            raise ValueError("tasks and dedupe_keys length mismatch")
        now = self._clock.now().isoformat()
        saved: list[SavedCandidateTaskDTO] = []
        for task, key in zip(tasks, dedupe_keys, strict=True):
            cur = self._conn.execute(
                """
                INSERT INTO extracted_tasks (
                  message_id, title, description, due_at, confidence, status, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                RETURNING id
                """,
                (
                    message_id,
                    task.title,
                    task.description,
                    _iso(task.due_at),
                    float(task.confidence),
                    task.status.value,
                    key,
                    now,
                ),
            )
            row = cur.fetchone()
            if row is None:
                existing = self._conn.execute(
                    "SELECT id FROM extracted_tasks WHERE dedupe_key = ? LIMIT 1",
                    (key,),
                ).fetchone()
                if existing is not None:
                    saved.append(SavedCandidateTaskDTO(task_id=int(existing["id"]), dedupe_key=key, created=False))
                continue
            saved.append(SavedCandidateTaskDTO(task_id=int(row["id"]), dedupe_key=key, created=True))
        self._conn.commit()
        return tuple(saved)

    def update_task_status(self, task_id: int, status: TaskStatus) -> None:
        self._conn.execute("UPDATE extracted_tasks SET status = ? WHERE id = ?", (status.value, task_id))
        self._conn.commit()

    def message_has_candidate_tasks(self, message_id: int) -> bool:
        row = self._conn.execute(
            """
            SELECT 1 FROM extracted_tasks
            WHERE message_id = ? AND status = ?
            LIMIT 1
            """,
            (message_id, TaskStatus.CANDIDATE.value),
        ).fetchone()
        return row is not None

    def _row_to_kanban_context(self, row: sqlite3.Row) -> TaskKanbanSourceContextDTO:
        task = PersistedExtractedTaskDTO(
            id=int(row["id"]),
            message_id=int(row["message_id"]),
            title=str(row["title"]),
            description=row["description"],
            due_at=_parse_dt(row["due_at"]),
            confidence=float(row["confidence"]),
            status=TaskStatus(str(row["status"])),
            dedupe_key=str(row["dedupe_key"]),
        )
        triage_reply = ReplyRequirement(str(row["tr_reply"])) if row["tr_reply"] is not None else None
        triage_conf = float(row["tr_conf"]) if row["tr_conf"] is not None else None
        triage_imp = MessageImportance(str(row["tr_imp"])) if row["tr_imp"] is not None else None
        return TaskKanbanSourceContextDTO(
            task=task,
            message_subject=row["subject"],
            message_sender=row["sender"],
            triage_summary=row["tr_summary"],
            triage_reply_requirement=triage_reply,
            triage_confidence=triage_conf,
            triage_importance=triage_imp,
        )

    def get_task_kanban_context(self, task_id: int) -> TaskKanbanSourceContextDTO | None:
        row = self._conn.execute(
            """
            SELECT et.*, m.subject, m.sender, t.reply_requirement AS tr_reply,
                   t.summary AS tr_summary, t.confidence AS tr_conf, t.importance AS tr_imp
            FROM extracted_tasks et
            JOIN messages m ON m.id = et.message_id
            LEFT JOIN triage_results t ON t.message_id = m.id
            WHERE et.id = ?
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_kanban_context(row)

    def list_approved_tasks_for_kanban(self, limit: int) -> Sequence[TaskKanbanSourceContextDTO]:
        rows = self._conn.execute(
            """
            SELECT et.*, m.subject, m.sender, t.reply_requirement AS tr_reply,
                   t.summary AS tr_summary, t.confidence AS tr_conf, t.importance AS tr_imp
            FROM extracted_tasks et
            JOIN messages m ON m.id = et.message_id
            LEFT JOIN triage_results t ON t.message_id = m.id
            WHERE et.status = ?
            ORDER BY et.id ASC
            LIMIT ?
            """,
            (TaskStatus.APPROVED.value, limit),
        ).fetchall()
        return tuple(self._row_to_kanban_context(r) for r in rows)


class SqliteMorningDigestRepository:
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def save_digest(self, pipeline_run_id: int | None, digest: MorningDigest) -> int:
        now = self._clock.now().isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO morning_digests (pipeline_run_id, window_start, window_end, markdown, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                pipeline_run_id,
                digest.window_start.isoformat(),
                digest.window_end.isoformat(),
                digest.markdown,
                now,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)


class SqlitePipelineRunRepository:
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def start_run(self, run_id: str, command: str) -> int:
        now = self._clock.now().isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO pipeline_runs (run_id, started_at, command, status)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, now, command, "running"),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, db_id: int, status: str, metadata: str | None) -> None:
        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            UPDATE pipeline_runs
            SET finished_at = ?, status = ?, metadata_json = ?
            WHERE id = ?
            """,
            (now, status, metadata, db_id),
        )
        self._conn.commit()


class SqliteReviewRepository:
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def enqueue(self, cmd: ReviewEnqueueCommandDTO) -> tuple[int, bool]:
        existing = self.find_pending_duplicate(kind=cmd.review_kind, message_id=cmd.message_id, task_id=cmd.related_task_id)
        if existing is not None:
            return existing, False

        now = self._clock.now().isoformat()
        try:
            cur = self._conn.execute(
                """
                INSERT INTO review_items (
                  review_kind, related_message_id, related_task_id, reason_code, reason_text,
                  confidence, payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cmd.review_kind.value,
                    cmd.message_id,
                    cmd.related_task_id,
                    cmd.reason_code,
                    cmd.reason_text,
                    float(cmd.confidence),
                    cmd.payload_json,
                    ReviewStatus.PENDING.value,
                    now,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid), True
        except sqlite3.IntegrityError:
            self._conn.rollback()
            dup = self.find_pending_duplicate(kind=cmd.review_kind, message_id=cmd.message_id, task_id=cmd.related_task_id)
            if dup is None:
                raise
            return dup, False

    def find_pending_duplicate(self, *, kind: ReviewKind, message_id: int, task_id: int | None) -> int | None:
        if kind == ReviewKind.TRIAGE:
            row = self._conn.execute(
                """
                SELECT id FROM review_items
                WHERE status = ? AND review_kind = ? AND related_message_id = ? AND related_task_id IS NULL
                LIMIT 1
                """,
                (ReviewStatus.PENDING.value, kind.value, message_id),
            ).fetchone()
        else:
            if task_id is None:
                return None
            row = self._conn.execute(
                """
                SELECT id FROM review_items
                WHERE status = ? AND review_kind = ? AND related_task_id = ?
                LIMIT 1
                """,
                (ReviewStatus.PENDING.value, kind.value, task_id),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def list_pending(self, limit: int) -> Sequence[ReviewListItemDTO]:
        rows = self._conn.execute(
            """
            SELECT * FROM review_items
            WHERE status = ?
            ORDER BY datetime(created_at) ASC
            LIMIT ?
            """,
            (ReviewStatus.PENDING.value, limit),
        ).fetchall()
        return tuple(self._to_list_item(r) for r in rows)

    def get(self, review_id: int) -> ReviewListItemDTO:
        row = self._conn.execute("SELECT * FROM review_items WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            raise ReviewDecisionError("review item not found")
        return self._to_list_item(row)

    def approve(self, review_id: int, *, decided_by: str, note: str | None) -> None:
        item = self.get(review_id)
        if item.status == ReviewStatus.APPROVED:
            return
        if item.status != ReviewStatus.PENDING:
            raise ReviewDecisionError("review item is not pending")

        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            UPDATE review_items
            SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
            WHERE id = ? AND status = ?
            """,
            (ReviewStatus.APPROVED.value, now, decided_by, note, review_id, ReviewStatus.PENDING.value),
        )
        self._conn.commit()

    def reject(self, review_id: int, *, decided_by: str, note: str | None) -> None:
        item = self.get(review_id)
        if item.status == ReviewStatus.REJECTED:
            return
        if item.status != ReviewStatus.PENDING:
            raise ReviewDecisionError("review item is not pending")

        now = self._clock.now().isoformat()
        self._conn.execute(
            """
            UPDATE review_items
            SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
            WHERE id = ? AND status = ?
            """,
            (ReviewStatus.REJECTED.value, now, decided_by, note, review_id, ReviewStatus.PENDING.value),
        )
        self._conn.commit()

    def _to_list_item(self, row: sqlite3.Row) -> ReviewListItemDTO:
        return ReviewListItemDTO(
            id=int(row["id"]),
            review_kind=ReviewKind(str(row["review_kind"])),
            related_message_id=int(row["related_message_id"]),
            related_task_id=int(row["related_task_id"]) if row["related_task_id"] is not None else None,
            reason_code=str(row["reason_code"]),
            reason_text=str(row["reason_text"]),
            confidence=float(row["confidence"]),
            payload_json=str(row["payload_json"]),
            status=ReviewStatus(str(row["status"])),
            created_at=(_parse_dt(str(row["created_at"])) or self._clock.now()),
            decided_at=_parse_dt(row["decided_at"]) if row["decided_at"] else None,
            decided_by=row["decided_by"],
            decision_note=row["decision_note"],
        )


class SqliteDigestContextRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load_daily_digest_context(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        max_messages: int,
    ) -> DailyDigestContextDTO:
        start_iso = window_start.isoformat()
        end_iso = window_end.isoformat()

        total_in_window = int(
            self._conn.execute(
                """
                SELECT COUNT(1) AS c FROM messages
                WHERE datetime(COALESCE(received_at, created_at)) >= datetime(?)
                  AND datetime(COALESCE(received_at, created_at)) < datetime(?)
                """,
                (start_iso, end_iso),
            ).fetchone()["c"]
        )

        rows = self._conn.execute(
            """
            SELECT m.*, t.importance AS tr_importance, t.reply_requirement AS tr_reply,
                   t.summary AS tr_summary, t.actionable AS tr_actionable
            FROM messages m
            LEFT JOIN triage_results t ON t.message_id = m.id
            WHERE datetime(COALESCE(m.received_at, m.created_at)) >= datetime(?)
              AND datetime(COALESCE(m.received_at, m.created_at)) < datetime(?)
            ORDER BY datetime(COALESCE(m.received_at, m.created_at)) DESC
            LIMIT ?
            """,
            (start_iso, end_iso, max_messages),
        ).fetchall()

        message_ids = [int(r["id"]) for r in rows]
        pending_reviews = int(
            self._conn.execute("SELECT COUNT(1) AS c FROM review_items WHERE status = ?", (ReviewStatus.PENDING.value,)).fetchone()[
                "c"
            ]
        )

        candidate_tasks: list[DigestTaskSnapshotDTO] = []
        if message_ids:
            placeholders = ",".join("?" for _ in message_ids)
            task_rows = self._conn.execute(
                f"""
                SELECT id, message_id, title, confidence, due_at
                FROM extracted_tasks
                WHERE message_id IN ({placeholders}) AND status = ?
                ORDER BY datetime(created_at) DESC
                LIMIT 200
                """,
                (*message_ids, TaskStatus.CANDIDATE.value),
            ).fetchall()
            for tr in task_rows:
                candidate_tasks.append(
                    DigestTaskSnapshotDTO(
                        task_id=int(tr["id"]),
                        message_id=int(tr["message_id"]),
                        title=str(tr["title"]),
                        confidence=float(tr["confidence"]),
                        due_at=tr["due_at"],
                    )
                )

        review_rows = self._conn.execute(
            """
            SELECT id, review_kind, related_message_id, related_task_id, reason_code, reason_text, confidence
            FROM review_items
            WHERE status = ?
            ORDER BY datetime(created_at) ASC
            LIMIT 200
            """,
            (ReviewStatus.PENDING.value,),
        ).fetchall()
        pending_review_items = [
            DigestReviewSnapshotDTO(
                review_id=int(rr["id"]),
                review_kind=ReviewKind(str(rr["review_kind"])),
                message_id=int(rr["related_message_id"]),
                task_id=int(rr["related_task_id"]) if rr["related_task_id"] is not None else None,
                reason_code=str(rr["reason_code"]),
                reason_text=str(rr["reason_text"]),
                confidence=float(rr["confidence"]),
            )
            for rr in review_rows
        ]

        snapshots: list[DigestMessageSnapshotDTO] = []
        for r in rows:
            importance = (
                MessageImportance(str(r["tr_importance"])) if r["tr_importance"] is not None else MessageImportance.MEDIUM
            )
            reply_req = ReplyRequirement(str(r["tr_reply"])) if r["tr_reply"] is not None else ReplyRequirement.NO
            summary = str(r["tr_summary"]) if r["tr_summary"] is not None else "(not triaged)"
            actionable = bool(r["tr_actionable"]) if r["tr_actionable"] is not None else False
            snapshots.append(
                DigestMessageSnapshotDTO(
                    message_id=int(r["id"]),
                    subject=r["subject"],
                    sender=r["sender"],
                    importance=importance,
                    reply_requirement=reply_req,
                    triage_summary=summary,
                    actionable=actionable,
                )
            )

        stats = DailyDigestStatsDTO(
            messages_in_window=total_in_window,
            messages_capped=len(snapshots),
            pending_reviews=pending_reviews,
            candidate_tasks=len(candidate_tasks),
        )

        return DailyDigestContextDTO(
            window_start=window_start,
            window_end=window_end,
            stats=stats,
            messages=tuple(snapshots),
            candidate_tasks=tuple(candidate_tasks),
            pending_reviews=tuple(pending_review_items),
        )

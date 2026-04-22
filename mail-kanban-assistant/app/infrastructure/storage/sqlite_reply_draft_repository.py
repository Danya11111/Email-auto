from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Sequence

from app.application.dtos import ReplyDraftCreateCommandDTO
from app.application.ports import ClockPort, ReplyDraftRepositoryPort
from app.domain.enums import ReplyDraftGenerationMode, ReplyDraftStatus, ReplyTone
from app.domain.reply_draft import ReplyDraft


def _iso_parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class SqliteReplyDraftRepository(ReplyDraftRepositoryPort):
    def __init__(self, conn: sqlite3.Connection, clock: ClockPort) -> None:
        self._conn = conn
        self._clock = clock

    def _row_to_domain(self, row: sqlite3.Row) -> ReplyDraft:
        return ReplyDraft(
            id=int(row["id"]),
            thread_id=str(row["thread_id"]),
            primary_message_id=int(row["primary_message_id"]),
            related_action_item_id=row["related_action_item_id"],
            status=ReplyDraftStatus(str(row["status"])),
            tone=ReplyTone(str(row["tone"])),
            subject_suggestion=str(row["subject_suggestion"]),
            body_text=str(row["body_text"]),
            opening_line=str(row["opening_line"] or ""),
            closing_line=str(row["closing_line"] or ""),
            short_rationale=str(row["short_rationale"]),
            key_points=tuple(json.loads(row["key_points_json"])),
            missing_information=tuple(json.loads(row["missing_information_json"] or "[]")),
            confidence=float(row["confidence"] or 0.0),
            source_message_ids=tuple(json.loads(row["source_message_ids_json"])),
            source_task_ids=tuple(json.loads(row["source_task_ids_json"])),
            source_review_ids=tuple(json.loads(row["source_review_ids_json"])),
            generated_at=_iso_parse(str(row["generated_at"])) or self._clock.now(),
            updated_at=_iso_parse(str(row["updated_at"])) or self._clock.now(),
            approved_at=_iso_parse(row["approved_at"]) if row["approved_at"] else None,
            rejected_at=_iso_parse(row["rejected_at"]) if row["rejected_at"] else None,
            exported_at=_iso_parse(row["exported_at"]) if row["exported_at"] else None,
            generation_fingerprint=str(row["generation_fingerprint"]),
            model_name=row["model_name"],
            generation_mode=ReplyDraftGenerationMode(str(row["generation_mode"])),
            fact_boundary_note=str(row["fact_boundary_note"]),
            user_note=row["user_note"],
        )

    def insert_reply_draft(self, cmd: ReplyDraftCreateCommandDTO, *, created_at_iso: str, updated_at_iso: str) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO reply_drafts (
              thread_id, primary_message_id, related_action_item_id, status, tone,
              subject_suggestion, body_text, opening_line, closing_line, short_rationale,
              key_points_json, missing_information_json, confidence,
              source_message_ids_json, source_task_ids_json, source_review_ids_json,
              generated_at, updated_at, approved_at, rejected_at, exported_at,
              generation_fingerprint, model_name, generation_mode, fact_boundary_note, user_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                cmd.thread_id,
                cmd.primary_message_id,
                cmd.related_action_item_id,
                cmd.status.value,
                cmd.tone.value,
                cmd.subject_suggestion,
                cmd.body_text,
                cmd.opening_line,
                cmd.closing_line,
                cmd.short_rationale,
                json.dumps(list(cmd.key_points), ensure_ascii=False),
                json.dumps(list(cmd.missing_information), ensure_ascii=False),
                float(cmd.confidence),
                json.dumps(list(cmd.source_message_ids), ensure_ascii=False),
                json.dumps(list(cmd.source_task_ids), ensure_ascii=False),
                json.dumps(list(cmd.source_review_ids), ensure_ascii=False),
                created_at_iso,
                updated_at_iso,
                cmd.generation_fingerprint,
                cmd.model_name,
                cmd.generation_mode.value,
                cmd.fact_boundary_note,
                cmd.user_note,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_reply_draft(self, draft_id: int) -> ReplyDraft | None:
        row = self._conn.execute("SELECT * FROM reply_drafts WHERE id = ?", (draft_id,)).fetchone()
        return self._row_to_domain(row) if row is not None else None

    def list_reply_drafts(
        self,
        *,
        status: str | None = None,
        thread_id: str | None = None,
        limit: int = 200,
    ) -> Sequence[ReplyDraft]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if thread_id:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = self._conn.execute(
            f"SELECT * FROM reply_drafts {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return tuple(self._row_to_domain(r) for r in rows)

    def find_latest_for_thread(self, thread_id: str) -> ReplyDraft | None:
        row = self._conn.execute(
            """
            SELECT * FROM reply_drafts
            WHERE thread_id = ?
            ORDER BY datetime(generated_at) DESC, id DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
        return self._row_to_domain(row) if row is not None else None

    def mark_reply_draft_approved(self, draft_id: int, *, decided_by: str, note: str | None, now_iso: str) -> None:
        row = self._conn.execute("SELECT user_note FROM reply_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return
        prev = row["user_note"] or ""
        extra = f"approved_by={decided_by}" + (f"; {note}" if note else "")
        merged = (prev + "\n" + extra).strip() if prev else extra
        self._conn.execute(
            """
            UPDATE reply_drafts
            SET status = ?, approved_at = ?, updated_at = ?, user_note = ?
            WHERE id = ? AND status = ?
            """,
            (ReplyDraftStatus.APPROVED.value, now_iso, now_iso, merged, draft_id, ReplyDraftStatus.GENERATED.value),
        )
        self._conn.commit()

    def mark_reply_draft_rejected(self, draft_id: int, *, decided_by: str, note: str | None, now_iso: str) -> None:
        row = self._conn.execute("SELECT user_note FROM reply_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return
        prev = row["user_note"] or ""
        extra = f"rejected_by={decided_by}" + (f"; {note}" if note else "")
        merged = (prev + "\n" + extra).strip() if prev else extra
        self._conn.execute(
            """
            UPDATE reply_drafts
            SET status = ?, rejected_at = ?, updated_at = ?, user_note = ?
            WHERE id = ? AND status = ?
            """,
            (ReplyDraftStatus.REJECTED.value, now_iso, now_iso, merged, draft_id, ReplyDraftStatus.GENERATED.value),
        )
        self._conn.commit()

    def mark_reply_draft_exported(self, draft_id: int, *, now_iso: str) -> None:
        self._conn.execute(
            """
            UPDATE reply_drafts
            SET status = ?, exported_at = ?, updated_at = ?
            WHERE id = ? AND status IN (?, ?)
            """,
            (
                ReplyDraftStatus.EXPORTED.value,
                now_iso,
                now_iso,
                draft_id,
                ReplyDraftStatus.APPROVED.value,
                ReplyDraftStatus.GENERATED.value,
            ),
        )
        self._conn.commit()

    def mark_reply_draft_stale(self, draft_id: int, *, now_iso: str) -> None:
        self._conn.execute(
            """
            UPDATE reply_drafts
            SET status = ?, updated_at = ?
            WHERE id = ? AND status IN (?, ?, ?)
            """,
            (
                ReplyDraftStatus.STALE.value,
                now_iso,
                draft_id,
                ReplyDraftStatus.GENERATED.value,
                ReplyDraftStatus.APPROVED.value,
                ReplyDraftStatus.EXPORTED.value,
            ),
        )
        self._conn.commit()

    def mark_thread_drafts_stale_except(self, thread_id: str, *, except_draft_id: int | None, now_iso: str) -> int:
        if except_draft_id is None:
            cur = self._conn.execute(
                """
                UPDATE reply_drafts
                SET status = ?, updated_at = ?
                WHERE thread_id = ? AND status = ?
                """,
                (ReplyDraftStatus.STALE.value, now_iso, thread_id, ReplyDraftStatus.GENERATED.value),
            )
        else:
            cur = self._conn.execute(
                """
                UPDATE reply_drafts
                SET status = ?, updated_at = ?
                WHERE thread_id = ? AND id != ? AND status = ?
                """,
                (ReplyDraftStatus.STALE.value, now_iso, thread_id, except_draft_id, ReplyDraftStatus.GENERATED.value),
            )
        self._conn.commit()
        return int(cur.rowcount)

    def maybe_find_same_fingerprint_draft(self, thread_id: str, fingerprint: str) -> ReplyDraft | None:
        row = self._conn.execute(
            """
            SELECT * FROM reply_drafts
            WHERE thread_id = ? AND generation_fingerprint = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (thread_id, fingerprint),
        ).fetchone()
        return self._row_to_domain(row) if row is not None else None

    def count_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT status, COUNT(1) AS c FROM reply_drafts GROUP BY status
            """
        ).fetchall()
        return {str(r["status"]): int(r["c"]) for r in rows}

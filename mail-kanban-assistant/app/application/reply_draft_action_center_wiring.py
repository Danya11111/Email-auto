from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from app.application.action_center_engine import build_action_center_snapshot
from app.application.dtos import ActionCenterRawBundleDTO, ActionCenterSnapshotDTO, ReplyDraftThreadPinDTO
from app.application.llm_input import LlmTextPolicy
from app.application.reply_context_builder import SqliteReplyContextBuilder
from app.application.ports import ClockPort, ReplyDraftActionCenterEnricherPort
from app.application.reply_draft_pins import build_reply_draft_thread_pins
from app.config import AppSettings
from app.infrastructure.storage.repositories import (
    SqliteMessageRepository,
    SqliteReviewRepository,
    SqliteTaskRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_reply_draft_repository import SqliteReplyDraftRepository


@dataclass(frozen=True, slots=True)
class SqliteReplyDraftActionCenterEnricher(ReplyDraftActionCenterEnricherPort):
    conn: sqlite3.Connection
    clock: ClockPort
    settings: AppSettings

    def enrich_snapshot(
        self, bundle: ActionCenterRawBundleDTO, now: datetime
    ) -> tuple[ActionCenterSnapshotDTO, dict[str, ReplyDraftThreadPinDTO]]:
        return build_action_center_snapshot_with_reply_pins(self.conn, self.clock, self.settings, bundle, now)


def build_action_center_snapshot_with_reply_pins(
    conn,
    clock,
    settings: AppSettings,
    bundle: ActionCenterRawBundleDTO,
    now: datetime,
) -> tuple[ActionCenterSnapshotDTO, dict[str, ReplyDraftThreadPinDTO]]:
    snap0 = build_action_center_snapshot(bundle, settings=settings, now=now, reply_draft_pins=None)
    messages = SqliteMessageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    triage = SqliteTriageRepository(conn, clock)
    policy = LlmTextPolicy(
        max_input_chars=int(settings.llm_max_input_chars),
        truncate_strategy=settings.message_body_truncate_strategy,
    )
    builder = SqliteReplyContextBuilder(
        messages=messages,
        tasks=tasks,
        reviews=reviews,
        triage_get=triage.get_triage,
        settings=settings,
        llm_text_policy=policy,
    )
    drafts = SqliteReplyDraftRepository(conn, clock)
    pins = build_reply_draft_thread_pins(
        snapshot=snap0,
        settings=settings,
        drafts=drafts,
        builder=builder,
        now_iso=now.isoformat(),
    )
    snap = build_action_center_snapshot(bundle, settings=settings, now=now, reply_draft_pins=pins)
    return snap, pins

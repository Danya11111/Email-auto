from __future__ import annotations

from datetime import datetime

from app.application.action_center_engine import build_action_center_snapshot
from app.application.dtos import ActionCenterRawBundleDTO
from app.config import AppSettings
from app.domain.enums import ReplyState
from app.domain.reply_draft_errors import ReplyDraftPreconditionError


def resolve_thread_message_ids(
    bundle: ActionCenterRawBundleDTO,
    *,
    settings: AppSettings,
    now: datetime,
    thread_id: str,
) -> tuple[int, ...]:
    snap = build_action_center_snapshot(bundle, settings=settings, now=now, reply_draft_pins=None)
    for t in snap.threads:
        if t.thread_id == thread_id:
            return tuple(t.related_message_ids)
    raise ReplyDraftPreconditionError(f"thread_id not found in current action-center window: {thread_id!r}")


def infer_reply_state_for_thread(
    bundle: ActionCenterRawBundleDTO,
    *,
    settings: AppSettings,
    now: datetime,
    thread_id: str,
) -> ReplyState:
    snap = build_action_center_snapshot(bundle, settings=settings, now=now, reply_draft_pins=None)
    for t in snap.threads:
        if t.thread_id == thread_id:
            return t.reply_state
    raise ReplyDraftPreconditionError(f"thread_id not found in current action-center window: {thread_id!r}")

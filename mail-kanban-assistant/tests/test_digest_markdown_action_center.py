from __future__ import annotations

from datetime import UTC, datetime

from app.application.action_center_engine import build_action_center_snapshot
from app.application.digest_compose_options import DigestComposeOptions
from app.application.digest_markdown import compose_daily_digest_markdown
from app.application.dtos import (
    ActionCenterMessageRowDTO,
    ActionCenterRawBundleDTO,
    DailyDigestContextDTO,
    DailyDigestStatsDTO,
    DigestMessageSnapshotDTO,
    KanbanDigestSectionDTO,
)
from app.config import AppSettings
from app.domain.enums import MessageImportance, ReplyRequirement


def test_executive_summary_uses_precomputed_lines() -> None:
    ws = datetime(2026, 4, 19, 8, tzinfo=UTC)
    we = datetime(2026, 4, 19, 9, tzinfo=UTC)
    ctx = DailyDigestContextDTO(
        window_start=ws,
        window_end=we,
        stats=DailyDigestStatsDTO(messages_in_window=0, messages_capped=0, pending_reviews=0, candidate_tasks=0),
        messages=(),
        candidate_tasks=(),
        pending_reviews=(),
        executive_summary_lines=("line-a", "line-b"),
    )
    md = compose_daily_digest_markdown(ctx=ctx, pipeline_notes={}, options=DigestComposeOptions())
    assert "line-a" in md
    assert "line-b" in md
    assert "Messages in window" not in md


def test_digest_primary_action_center_and_kanban_failed_visible() -> None:
    t0 = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    row = ActionCenterMessageRowDTO(
        message_id=1,
        received_at=t0,
        subject="Ping",
        sender="a@b.com",
        recipients=(),
        thread_hint=None,
        importance=MessageImportance.MEDIUM,
        reply_requirement=ReplyRequirement.NO,
        actionable=False,
        triage_summary="ok",
        triage_confidence=0.8,
    )
    bundle = ActionCenterRawBundleDTO(
        window_start=t0,
        window_end=t0,
        messages=(row,),
        task_pins=(),
        pending_reviews=(),
        kanban_failures=(),
        approved_ready_to_sync=0,
        manual_resync_backlog=0,
    )
    settings = AppSettings(
        thread_grouping_time_window_hours=96,
        action_center_max_items=20,
        action_center_include_informational=False,
        action_center_require_review_for_ambiguous_reply=False,
    )
    snap = build_action_center_snapshot(bundle, settings=settings, now=t0)
    ctx = DailyDigestContextDTO(
        window_start=t0,
        window_end=t0,
        stats=DailyDigestStatsDTO(messages_in_window=1, messages_capped=1, pending_reviews=0, candidate_tasks=0),
        messages=(
            DigestMessageSnapshotDTO(
                message_id=1,
                subject="Ping",
                sender="a@b.com",
                importance=MessageImportance.MEDIUM,
                reply_requirement=ReplyRequirement.NO,
                triage_summary="ok",
                actionable=False,
            ),
        ),
        candidate_tasks=(),
        pending_reviews=(),
        action_center=snap,
        kanban=KanbanDigestSectionDTO(
            provider="local_file",
            auto_sync_enabled=False,
            approved_ready_to_sync=0,
            pending_outbox=0,
            synced=0,
            failed=2,
            recent_errors=("e1",),
            outbound_updates_last_24h=0,
            manual_resync_pending=3,
        ),
    )
    md = compose_daily_digest_markdown(ctx=ctx, pipeline_notes={}, options=DigestComposeOptions())
    assert md.index("Today's action center") < md.index("## Tasks ready")
    assert "Failed sync count" in md
    assert "Manual resync backlog" in md

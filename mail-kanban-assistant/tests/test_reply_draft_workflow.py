from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.application.action_center_engine import build_action_center_snapshot
from app.application.dtos import IncomingMessageDTO
from app.application.reply_context_builder import SqliteReplyContextBuilder
from app.application.reply_draft_action_center_wiring import SqliteReplyDraftActionCenterEnricher
from app.application.reply_draft_pins import build_reply_draft_digest_section
from app.application.reply_draft_policy import generation_allowed_for_reply_state, should_reuse_existing_generated_draft
from app.application.reply_thread_resolution import infer_reply_state_for_thread, resolve_thread_message_ids
from app.application.use_cases.reply_draft_generate import GenerateReplyDraftUseCase
from app.application.use_cases.reply_draft_lifecycle import ApproveReplyDraftUseCase, ExportReplyDraftUseCase
from app.application.llm_input import LlmTextPolicy
from app.config import AppSettings
from app.domain.enums import (
    MessageImportance,
    MessageProcessingStatus,
    MessageSource,
    ReplyDraftGenerationMode,
    ReplyDraftStatus,
    ReplyRequirement,
    ReplyState,
    ReplyTone,
)
from app.domain.models import TriageResult
from app.domain.reply_draft import ReplyDraft
from app.domain.reply_draft_errors import ReplyDraftPreconditionError
from app.infrastructure.storage.repositories import (
    SqliteDigestContextRepository,
    SqliteMessageRepository,
    SqliteReviewRepository,
    SqliteTaskRepository,
    SqliteTriageRepository,
)
from app.infrastructure.storage.sqlite_db import initialize_database, open_connection
from app.infrastructure.storage.sqlite_kanban_sync_repository import SqliteKanbanSyncRepository
from app.infrastructure.storage.sqlite_reply_draft_repository import SqliteReplyDraftRepository
from app.utils.text import normalize_mail_body
from tests.fakes import FakeReplyDraftLLM, FixedClock, NullLogger


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "storage" / "schema.sql").read_text(
        encoding="utf-8"
    )


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "reply_draft.sqlite3"
    connection = open_connection(db_path)
    initialize_database(connection, _schema_sql())
    yield connection
    connection.close()


def _insert_thread(conn, clock: FixedClock) -> tuple[str, ActionCenterRawBundleDTO]:
    settings = AppSettings(
        action_center_lookback_hours=96,
        action_center_max_messages=50,
        action_center_max_items=40,
        thread_grouping_time_window_hours=96,
        reply_overdue_hours=200,
        reply_recommended_hours=24,
        action_center_require_review_for_ambiguous_reply=False,
    )
    messages = SqliteMessageRepository(conn, clock)
    triage = SqliteTriageRepository(conn, clock)
    t0 = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    for i, subj in enumerate(("Re: Project", "Re: Project")):
        mid = messages.insert_message(
            IncomingMessageDTO(
                dedupe_key=f"rd-{i}",
                source=MessageSource.EML,
                rfc_message_id=f"<m{i}@x>",
                subject=subj,
                sender="client@ext.com" if i == 1 else "me@local",
                recipients=("me@local",) if i == 1 else ("client@ext.com",),
                received_at=t0,
                body_plain="Please confirm timeline for next week." if i == 1 else "Thanks for the update.",
                thread_hint="reply-test-thread",
            ),
            body_normalized=normalize_mail_body("x"),
            processing_status=MessageProcessingStatus.TRIAGED,
        )
        triage.save_triage(
            mid,
            TriageResult(
                importance=MessageImportance.HIGH,
                reply_requirement=ReplyRequirement.REQUIRED,
                summary="Client asks for timeline",
                actionable=True,
                confidence=0.9,
                reason_codes=("test",),
            ),
            raw_json="{}",
        )
    digest_ctx = SqliteDigestContextRepository(conn)
    end = clock.now()
    start = end - timedelta(hours=int(settings.action_center_lookback_hours))
    bundle = digest_ctx.load_action_center_raw_bundle(
        window_start=start,
        window_end=end,
        max_message_rows=50,
        kanban_provider=settings.kanban_provider,
    )
    kb = SqliteKanbanSyncRepository(conn, clock).load_kanban_digest_section(
        provider=settings.kanban_provider,
        auto_sync_enabled=False,
    )
    bundle = bundle.model_copy(
        update={"approved_ready_to_sync": kb.approved_ready_to_sync, "manual_resync_backlog": kb.manual_resync_pending}
    )
    snap = build_action_center_snapshot(bundle, settings=settings, now=end, reply_draft_pins=None)
    tid = next(t.thread_id for t in snap.threads if len(t.related_message_ids) == 2)
    return tid, bundle


def test_policy_waiting_for_us_allowed() -> None:
    generation_allowed_for_reply_state(ReplyState.WAITING_FOR_US, force=False, settings=AppSettings())


def test_policy_no_reply_needed_blocked() -> None:
    with pytest.raises(ReplyDraftPreconditionError):
        generation_allowed_for_reply_state(ReplyState.NO_REPLY_NEEDED, force=False, settings=AppSettings())


def test_policy_waiting_for_them_blocked_without_force() -> None:
    with pytest.raises(ReplyDraftPreconditionError):
        generation_allowed_for_reply_state(ReplyState.WAITING_FOR_THEM, force=False, settings=AppSettings())


def test_should_reuse_generated_same_fingerprint() -> None:
    d = ReplyDraft(
        id=1,
        thread_id="t",
        primary_message_id=1,
        related_action_item_id=None,
        status=ReplyDraftStatus.GENERATED,
        tone=ReplyTone.NEUTRAL,
        subject_suggestion="s",
        body_text="b",
        opening_line="",
        closing_line="",
        short_rationale="r",
        key_points=(),
        missing_information=(),
        confidence=0.5,
        source_message_ids=(1,),
        source_task_ids=(),
        source_review_ids=(),
        generated_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        approved_at=None,
        rejected_at=None,
        exported_at=None,
        generation_fingerprint="abc",
        model_name=None,
        generation_mode=ReplyDraftGenerationMode.INITIAL,
        fact_boundary_note="f",
        user_note=None,
    )
    assert should_reuse_existing_generated_draft(d, current_fingerprint="abc", force=False) is True
    assert should_reuse_existing_generated_draft(d, current_fingerprint="xyz", force=False) is False
    assert should_reuse_existing_generated_draft(d, current_fingerprint="abc", force=True) is False


def test_context_builder_trims_and_is_deterministic(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    tid, bundle = _insert_thread(conn, clock)
    end = clock.now()
    rs = infer_reply_state_for_thread(bundle, settings=AppSettings(action_center_require_review_for_ambiguous_reply=False), now=end, thread_id=tid)
    mids = resolve_thread_message_ids(
        bundle,
        settings=AppSettings(action_center_require_review_for_ambiguous_reply=False),
        now=end,
        thread_id=tid,
    )
    settings = AppSettings(
        reply_draft_max_context_messages=1,
        reply_draft_max_input_chars=2000,
        reply_draft_include_tasks=True,
        reply_draft_include_review_notes=True,
        reply_draft_include_action_center_reason=True,
    )
    messages = SqliteMessageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    triage = SqliteTriageRepository(conn, clock)
    policy = LlmTextPolicy(max_input_chars=4000, truncate_strategy=settings.message_body_truncate_strategy)
    builder = SqliteReplyContextBuilder(
        messages=messages,
        tasks=tasks,
        reviews=reviews,
        triage_get=triage.get_triage,
        settings=settings,
        llm_text_policy=policy,
    )
    ctx1 = builder.build_for_thread(
        thread_id=tid,
        message_ids=mids,
        primary_message_id=None,
        reply_state=rs,
        action_center_next_step="Draft reply",
    )
    ctx2 = builder.build_for_thread(
        thread_id=tid,
        message_ids=mids,
        primary_message_id=None,
        reply_state=rs,
        action_center_next_step="Draft reply",
    )
    assert ctx1.model_dump() == ctx2.model_dump()
    assert len(ctx1.messages_included) == 1


def test_generate_reuses_same_fingerprint(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    tid, bundle = _insert_thread(conn, clock)
    settings = AppSettings(action_center_require_review_for_ambiguous_reply=False)
    messages = SqliteMessageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    triage = SqliteTriageRepository(conn, clock)
    policy = LlmTextPolicy(max_input_chars=7000, truncate_strategy=settings.message_body_truncate_strategy)
    builder = SqliteReplyContextBuilder(
        messages=messages,
        tasks=tasks,
        reviews=reviews,
        triage_get=triage.get_triage,
        settings=settings,
        llm_text_policy=policy,
    )
    drafts = SqliteReplyDraftRepository(conn, clock)
    llm = FakeReplyDraftLLM()
    uc = GenerateReplyDraftUseCase(drafts=drafts, llm=llm, builder=builder, clock=clock, logger=logger, settings=settings)
    r1 = uc.execute(run_id="a", thread_id=tid, bundle=bundle, tone=ReplyTone.NEUTRAL, force=False, explicit_regenerate=False)
    assert r1.reused_without_llm is False
    r2 = uc.execute(run_id="b", thread_id=tid, bundle=bundle, tone=ReplyTone.NEUTRAL, force=False, explicit_regenerate=False)
    assert r2.reused_without_llm is True
    assert r2.draft_id == r1.draft_id


def test_export_after_approve(conn, tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    tid, bundle = _insert_thread(conn, clock)
    settings = AppSettings(
        action_center_require_review_for_ambiguous_reply=False,
        reply_draft_require_approval_before_export=True,
        reply_draft_export_dir=tmp_path / "exp",
    )
    messages = SqliteMessageRepository(conn, clock)
    tasks = SqliteTaskRepository(conn, clock)
    reviews = SqliteReviewRepository(conn, clock)
    triage = SqliteTriageRepository(conn, clock)
    policy = LlmTextPolicy(max_input_chars=7000, truncate_strategy=settings.message_body_truncate_strategy)
    builder = SqliteReplyContextBuilder(
        messages=messages,
        tasks=tasks,
        reviews=reviews,
        triage_get=triage.get_triage,
        settings=settings,
        llm_text_policy=policy,
    )
    drafts = SqliteReplyDraftRepository(conn, clock)
    uc = GenerateReplyDraftUseCase(drafts=drafts, llm=FakeReplyDraftLLM(), builder=builder, clock=clock, logger=logger, settings=settings)
    gen = uc.execute(run_id="x", thread_id=tid, bundle=bundle, tone=ReplyTone.NEUTRAL, force=False, explicit_regenerate=False)
    ApproveReplyDraftUseCase(drafts=drafts, clock=clock).execute(gen.draft_id, decided_by="t", note=None)
    from app.application.reply_draft_export_files import LocalReplyDraftExporter

    out = tmp_path / "out.md"
    ExportReplyDraftUseCase(
        drafts=drafts,
        exporter=LocalReplyDraftExporter(),
        clock=clock,
        settings=settings,
    ).execute(gen.draft_id, out_path=out, as_markdown=True)
    d = drafts.get_reply_draft(gen.draft_id)
    assert d is not None
    assert d.status == ReplyDraftStatus.EXPORTED
    assert "## Subject" in out.read_text(encoding="utf-8")


def test_digest_reply_draft_section(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    logger = NullLogger()
    tid, bundle = _insert_thread(conn, clock)
    settings = AppSettings(
        action_center_require_review_for_ambiguous_reply=False,
        digest_lookback_hours=96,
        digest_max_messages=50,
        action_center_lookback_hours=96,
        action_center_max_messages=50,
    )
    end = clock.now()
    enricher = SqliteReplyDraftActionCenterEnricher(conn=conn, clock=clock, settings=settings)
    snap, pins = enricher.enrich_snapshot(bundle, end)
    sec = build_reply_draft_digest_section(snapshot=snap, pins=pins)
    assert isinstance(sec.needing_draft, tuple)


def test_action_center_pin_missing_draft(conn) -> None:
    clock = FixedClock(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
    tid, bundle = _insert_thread(conn, clock)
    settings = AppSettings(action_center_require_review_for_ambiguous_reply=False)
    end = clock.now()
    enricher = SqliteReplyDraftActionCenterEnricher(conn=conn, clock=clock, settings=settings)
    snap, pins = enricher.enrich_snapshot(bundle, end)
    assert pins.get(tid) is not None
    snap2 = build_action_center_snapshot(bundle, settings=settings, now=end, reply_draft_pins=pins)
    thread_items = [i for i in snap2.items if i.source_type == "thread"]
    assert any(i.reply_draft_workflow == "missing" for i in thread_items)

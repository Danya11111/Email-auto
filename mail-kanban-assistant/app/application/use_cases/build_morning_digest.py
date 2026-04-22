from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

from app.application.action_center_engine import build_action_center_snapshot, build_executive_summary_lines
from app.application.digest_compose_options import DigestComposeOptions
from app.application.digest_markdown import compose_daily_digest_markdown
from app.application.dtos import DigestBuildResultDTO
from app.application.ports import (
    ClockPort,
    DigestContextPort,
    KanbanSyncRepositoryPort,
    LoggerPort,
    MorningDigestRepositoryPort,
    ReplyDraftActionCenterEnricherPort,
)
from app.application.reply_draft_pins import build_reply_draft_digest_section, executive_reply_draft_bullets
from app.config import AppSettings
from app.domain.models import MorningDigest


@dataclass(frozen=True, slots=True)
class BuildMorningDigestUseCase:
    digest_context: DigestContextPort
    digests: MorningDigestRepositoryPort
    clock: ClockPort
    logger: LoggerPort
    settings: AppSettings
    kanban_sync: KanbanSyncRepositoryPort | None = None
    reply_draft_action_center: ReplyDraftActionCenterEnricherPort | None = None

    def execute(
        self,
        *,
        run_id: str,
        pipeline_run_db_id: int | None = None,
        pipeline_stats: dict[str, object] | None = None,
        compact: bool = False,
        include_informational: bool = False,
    ) -> DigestBuildResultDTO:
        started = time.perf_counter()
        self.logger.info("digest.start", run_id=run_id)

        end = self.clock.now()
        start = end - timedelta(hours=int(self.settings.digest_lookback_hours))
        ctx = self.digest_context.load_daily_digest_context(
            window_start=start,
            window_end=end,
            max_messages=int(self.settings.digest_max_messages),
        )
        kb = None
        if self.kanban_sync is not None:
            kb = self.kanban_sync.load_kanban_digest_section(
                provider=self.settings.kanban_provider,
                auto_sync_enabled=self.settings.kanban_auto_sync,
            )
            ctx = ctx.model_copy(update={"kanban": kb})

        ac_start = end - timedelta(hours=int(self.settings.action_center_lookback_hours))
        bundle = self.digest_context.load_action_center_raw_bundle(
            window_start=ac_start,
            window_end=end,
            max_message_rows=int(self.settings.action_center_max_messages),
            kanban_provider=self.settings.kanban_provider,
        )
        if kb is not None:
            bundle = bundle.model_copy(
                update={
                    "approved_ready_to_sync": kb.approved_ready_to_sync,
                    "manual_resync_backlog": kb.manual_resync_pending,
                }
            )
        if self.reply_draft_action_center is not None:
            snapshot, pins = self.reply_draft_action_center.enrich_snapshot(bundle, end)
            digest_rd = build_reply_draft_digest_section(snapshot=snapshot, pins=pins)
            rd_preamble = executive_reply_draft_bullets(pins)
        else:
            snapshot = build_action_center_snapshot(bundle, settings=self.settings, now=end, reply_draft_pins=None)
            digest_rd = None
            rd_preamble = ()
        stats_line = (
            f"Action center window {ac_start.isoformat()} → {end.isoformat()}: "
            f"threads={len(snapshot.threads)} items={len(snapshot.items)}"
        )
        max_exec = int(self.settings.action_center_executive_summary_max_items)
        exec_lines = build_executive_summary_lines(
            snapshot, stats_line=stats_line, max_items=max_exec, reply_draft_preamble=rd_preamble
        )
        if self.settings.action_center_use_llm_executive_summary:
            # Reserved: optional tiny structured LLM summary; deterministic path stays default (low-memory).
            pass
        ctx = ctx.model_copy(
            update={
                "action_center": snapshot,
                "executive_summary_lines": exec_lines,
                "reply_draft_digest": digest_rd,
            }
        )

        digest_opts = DigestComposeOptions(
            compact=compact,
            include_informational=include_informational or bool(self.settings.action_center_include_informational),
        )
        markdown = compose_daily_digest_markdown(ctx=ctx, pipeline_notes=pipeline_stats or {}, options=digest_opts)
        digest = MorningDigest(window_start=start, window_end=end, markdown=markdown)
        digest_id = self.digests.save_digest(pipeline_run_id=pipeline_run_db_id, digest=digest)

        duration_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "digest.end",
            run_id=run_id,
            duration_ms=duration_ms,
            messages=len(ctx.messages),
            digest_id=digest_id,
            action_center_items=len(snapshot.items),
        )
        return DigestBuildResultDTO(run_id=run_id, digest_id=digest_id, markdown=digest.markdown)

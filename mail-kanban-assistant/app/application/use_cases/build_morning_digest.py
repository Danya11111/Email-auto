from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

from app.application.digest_markdown import compose_daily_digest_markdown
from app.application.dtos import DigestBuildResultDTO
from app.application.ports import ClockPort, DigestContextPort, KanbanSyncRepositoryPort, LoggerPort, MorningDigestRepositoryPort
from app.domain.enums import KanbanProvider
from app.domain.models import MorningDigest


@dataclass(frozen=True, slots=True)
class BuildMorningDigestUseCase:
    digest_context: DigestContextPort
    digests: MorningDigestRepositoryPort
    clock: ClockPort
    logger: LoggerPort
    lookback_hours: int
    digest_max_messages: int
    kanban_sync: KanbanSyncRepositoryPort | None = None
    kanban_provider: KanbanProvider = KanbanProvider.LOCAL_FILE
    kanban_auto_sync: bool = False

    def execute(
        self,
        *,
        run_id: str,
        pipeline_run_db_id: int | None = None,
        pipeline_stats: dict[str, object] | None = None,
    ) -> DigestBuildResultDTO:
        started = time.perf_counter()
        self.logger.info("digest.start", run_id=run_id)

        end = self.clock.now()
        start = end - timedelta(hours=self.lookback_hours)
        ctx = self.digest_context.load_daily_digest_context(
            window_start=start,
            window_end=end,
            max_messages=self.digest_max_messages,
        )
        if self.kanban_sync is not None:
            kb = self.kanban_sync.load_kanban_digest_section(
                provider=self.kanban_provider,
                auto_sync_enabled=self.kanban_auto_sync,
            )
            ctx = ctx.model_copy(update={"kanban": kb})
        markdown = compose_daily_digest_markdown(ctx=ctx, pipeline_notes=pipeline_stats or {})
        digest = MorningDigest(window_start=start, window_end=end, markdown=markdown)
        digest_id = self.digests.save_digest(pipeline_run_id=pipeline_run_db_id, digest=digest)

        duration_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "digest.end",
            run_id=run_id,
            duration_ms=duration_ms,
            messages=len(ctx.messages),
            digest_id=digest_id,
        )
        return DigestBuildResultDTO(run_id=run_id, digest_id=digest_id, markdown=digest.markdown)

from __future__ import annotations

import time
from dataclasses import dataclass

from app.application.action_center_engine import build_action_center_snapshot
from app.application.dtos import ActionCenterRawBundleDTO, ReplyDraftCreateCommandDTO
from app.application.ports import ClockPort, LoggerPort, ReplyContextBuilderPort, ReplyDraftLLMPort, ReplyDraftRepositoryPort
from app.application.reply_draft_fingerprint import fingerprint_for_reply_context
from app.application.reply_draft_policy import (
    generation_allowed_for_reply_state,
    pick_generation_mode,
    should_reuse_existing_generated_draft,
)
from app.config import AppSettings
from app.domain.enums import ReplyDraftGenerationMode, ReplyDraftStatus, ReplyTone
from app.domain.reply_draft_errors import ReplyDraftGenerationError


def _action_step_for_thread(bundle: ActionCenterRawBundleDTO, settings: AppSettings, now, thread_id: str) -> str | None:
    snap = build_action_center_snapshot(bundle, settings=settings, now=now, reply_draft_pins=None)
    for it in snap.items:
        if it.thread_id == thread_id and it.source_type == "thread":
            return it.recommended_next_step
    return None


@dataclass(frozen=True, slots=True)
class GenerateReplyDraftResultDTO:
    run_id: str
    draft_id: int
    reused_without_llm: bool
    generation_fingerprint: str
    generation_mode: ReplyDraftGenerationMode
    subject_suggestion: str


@dataclass(frozen=True, slots=True)
class GenerateReplyDraftUseCase:
    drafts: ReplyDraftRepositoryPort
    llm: ReplyDraftLLMPort
    builder: ReplyContextBuilderPort
    clock: ClockPort
    logger: LoggerPort
    settings: AppSettings

    def execute(
        self,
        *,
        run_id: str,
        thread_id: str,
        bundle: ActionCenterRawBundleDTO,
        tone: ReplyTone,
        force: bool,
        explicit_regenerate: bool,
    ) -> GenerateReplyDraftResultDTO:
        started = time.perf_counter()
        self.logger.info("reply_draft.generate.start", run_id=run_id, thread_id=thread_id)
        now = self.clock.now()
        from app.application.reply_thread_resolution import infer_reply_state_for_thread, resolve_thread_message_ids

        mids = resolve_thread_message_ids(bundle, settings=self.settings, now=now, thread_id=thread_id)
        rs = infer_reply_state_for_thread(bundle, settings=self.settings, now=now, thread_id=thread_id)
        generation_allowed_for_reply_state(rs, force=force, settings=self.settings)

        step = _action_step_for_thread(bundle, self.settings, now, thread_id)
        ctx = self.builder.build_for_thread(
            thread_id=thread_id,
            message_ids=mids,
            primary_message_id=None,
            reply_state=rs,
            action_center_next_step=step,
        )
        fp = fingerprint_for_reply_context(ctx)
        latest = self.drafts.find_latest_for_thread(thread_id)
        if should_reuse_existing_generated_draft(latest, current_fingerprint=fp, force=force) and latest is not None:
            self.logger.info(
                "reply_draft.generate.reuse_fingerprint",
                run_id=run_id,
                draft_id=latest.id,
                fingerprint=fp,
            )
            return GenerateReplyDraftResultDTO(
                run_id=run_id,
                draft_id=latest.id,
                reused_without_llm=True,
                generation_fingerprint=fp,
                generation_mode=ReplyDraftGenerationMode.INITIAL,
                subject_suggestion=latest.subject_suggestion,
            )

        mode = pick_generation_mode(
            existing_latest=latest,
            current_fingerprint=fp,
            explicit_regenerate=explicit_regenerate,
        )

        try:
            llm_out = self.llm.generate_reply_draft_structured(
                context_json=ctx.model_dump_json(),
                tone=tone.value,
                reply_state=rs.value,
            )
        except Exception as exc:  # noqa: BLE001
            raise ReplyDraftGenerationError(f"reply draft LLM failed: {exc}") from exc

        cmd = ReplyDraftCreateCommandDTO(
            thread_id=thread_id,
            primary_message_id=ctx.primary_message_id,
            related_action_item_id=f"ac:thread:{thread_id}",
            status=ReplyDraftStatus.GENERATED,
            tone=tone,
            subject_suggestion=llm_out.subject_suggestion,
            body_text=llm_out.body_text,
            opening_line=llm_out.opening_line,
            closing_line=llm_out.closing_line,
            short_rationale=llm_out.short_rationale,
            key_points=llm_out.core_points,
            missing_information=llm_out.missing_information,
            confidence=float(llm_out.confidence),
            source_message_ids=tuple(m.message_id for m in ctx.messages_included),
            source_task_ids=ctx.source_task_ids,
            source_review_ids=ctx.source_review_ids,
            generation_fingerprint=fp,
            model_name=self.settings.lm_studio_model,
            generation_mode=mode,
            fact_boundary_note=llm_out.fact_boundary_note,
            user_note=None,
        )
        now_iso = now.isoformat()
        new_id = self.drafts.insert_reply_draft(cmd, created_at_iso=now_iso, updated_at_iso=now_iso)
        self.drafts.mark_thread_drafts_stale_except(thread_id, except_draft_id=new_id, now_iso=now_iso)

        duration_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "reply_draft.generate.end",
            run_id=run_id,
            draft_id=new_id,
            duration_ms=duration_ms,
            mode=mode.value,
        )
        return GenerateReplyDraftResultDTO(
            run_id=run_id,
            draft_id=new_id,
            reused_without_llm=False,
            generation_fingerprint=fp,
            generation_mode=mode,
            subject_suggestion=llm_out.subject_suggestion,
        )

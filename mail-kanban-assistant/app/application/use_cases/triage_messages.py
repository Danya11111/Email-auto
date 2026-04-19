from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.application.dtos import ReviewEnqueueCommandDTO, TriageBatchResultDTO
from app.application.policies import should_enqueue_triage_review, should_extract_tasks, triage_response_to_domain
from app.application.ports import LoggerPort, MessageRepositoryPort, TriageLLMPort, TriageResultRepositoryPort
from app.application.use_cases.enqueue_review_items import EnqueueReviewItemsUseCase
from app.domain.enums import MessageProcessingStatus, ReviewKind


@dataclass(frozen=True, slots=True)
class TriageMessagesUseCase:
    messages: MessageRepositoryPort
    triage: TriageResultRepositoryPort
    llm: TriageLLMPort
    logger: LoggerPort
    enqueue_reviews: EnqueueReviewItemsUseCase
    review_threshold: float

    def execute(self, *, run_id: str, batch_limit: int = 200) -> TriageBatchResultDTO:
        started = time.perf_counter()
        self.logger.info("triage.start", run_id=run_id)

        pending = list(self.messages.list_messages_pending_triage(limit=batch_limit))
        processed = 0
        failures = 0
        reviews_enqueued = 0

        for msg in pending:
            if self.triage.has_triage(msg.id):
                existing = self.triage.get_triage(msg.id)
                if existing is not None and not should_extract_tasks(existing):
                    self.messages.update_processing_status(msg.id, MessageProcessingStatus.TASKS_EXTRACTED)
                else:
                    self.messages.update_processing_status(msg.id, MessageProcessingStatus.TRIAGED)
                continue

            try:
                llm_result = self.llm.triage_message(msg)
                domain = triage_response_to_domain(llm_result)
                raw = llm_result.model_dump_json()
                self.triage.save_triage(msg.id, domain, raw_json=raw)

                needs_review, reason_code, reason_text = should_enqueue_triage_review(
                    domain,
                    review_threshold=self.review_threshold,
                )
                if needs_review:
                    payload = json.dumps(
                        {
                            "message_id": msg.id,
                            "subject": msg.subject,
                            "sender": msg.sender,
                            "importance": domain.importance.value,
                            "reply_requirement": domain.reply_requirement.value,
                            "actionable": domain.actionable,
                            "confidence": domain.confidence,
                            "summary": domain.summary,
                        },
                        ensure_ascii=False,
                    )
                    cmd = ReviewEnqueueCommandDTO(
                        review_kind=ReviewKind.TRIAGE,
                        message_id=msg.id,
                        related_task_id=None,
                        reason_code=reason_code,
                        reason_text=reason_text,
                        confidence=float(domain.confidence),
                        payload_json=payload,
                    )
                    res = self.enqueue_reviews.execute(run_id=run_id, commands=[cmd])
                    reviews_enqueued += res.inserted
                    self.messages.update_processing_status(msg.id, MessageProcessingStatus.AWAITING_REVIEW)
                elif not should_extract_tasks(domain):
                    self.messages.update_processing_status(msg.id, MessageProcessingStatus.TASKS_EXTRACTED)
                else:
                    self.messages.update_processing_status(msg.id, MessageProcessingStatus.TRIAGED)
                processed += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                self.logger.error(
                    "triage.message_failed",
                    run_id=run_id,
                    message_id=msg.id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        duration_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "triage.end",
            run_id=run_id,
            duration_ms=duration_ms,
            processed=processed,
            failures=failures,
            reviews_enqueued=reviews_enqueued,
            candidates=len(pending),
        )
        return TriageBatchResultDTO(
            run_id=run_id,
            processed=processed,
            failures=failures,
            reviews_enqueued=reviews_enqueued,
        )

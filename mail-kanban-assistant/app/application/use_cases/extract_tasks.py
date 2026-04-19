from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.application.dtos import ExtractTasksBatchResultDTO, ReviewEnqueueCommandDTO
from app.application.policies import TaskAutomationPolicy, maybe_sync_to_kanban, should_enqueue_task_review, should_extract_tasks
from app.application.ports import (
    KanbanPort,
    LoggerPort,
    MessageRepositoryPort,
    TaskExtractionLLMPort,
    TaskRepositoryPort,
    TriageResultRepositoryPort,
)
from app.application.use_cases.enqueue_review_items import EnqueueReviewItemsUseCase
from app.domain.enums import MessageProcessingStatus, ReviewKind, TaskStatus
from app.domain.models import ExtractedTask


@dataclass(frozen=True, slots=True)
class ExtractTasksUseCase:
    messages: MessageRepositoryPort
    triage_repo: TriageResultRepositoryPort
    tasks_llm: TaskExtractionLLMPort
    tasks: TaskRepositoryPort
    kanban: KanbanPort
    logger: LoggerPort
    enqueue_reviews: EnqueueReviewItemsUseCase
    review_threshold: float

    def execute(self, *, run_id: str, policy: TaskAutomationPolicy, batch_limit: int = 200) -> ExtractTasksBatchResultDTO:
        started = time.perf_counter()
        self.logger.info("extract_tasks.start", run_id=run_id)

        candidates = list(self.messages.list_messages_for_task_extraction(limit=batch_limit))
        messages_processed = 0
        tasks_created = 0
        failures = 0
        reviews_enqueued = 0

        for msg in candidates:
            try:
                triage_domain = self.triage_repo.get_triage(msg.id)
                if triage_domain is None or not should_extract_tasks(triage_domain):
                    self.messages.update_processing_status(msg.id, MessageProcessingStatus.TASKS_EXTRACTED)
                    continue

                if self.tasks.message_has_candidate_tasks(msg.id):
                    self.messages.update_processing_status(msg.id, MessageProcessingStatus.TASKS_EXTRACTED)
                    continue

                extracted = list(self.tasks_llm.extract_tasks(msg, triage_summary=triage_domain.summary))
                domain_tasks: list[ExtractedTask] = []
                dedupe_keys: list[str] = []
                for item in extracted:
                    domain_tasks.append(
                        ExtractedTask(
                            title=item.title,
                            description=item.description,
                            due_at=item.due_at,
                            confidence=item.confidence,
                            status=TaskStatus.CANDIDATE,
                        )
                    )
                    dedupe_keys.append(f"{msg.id}:{item.title.strip().lower()}")

                saved = self.tasks.save_candidate_tasks(msg.id, domain_tasks, dedupe_keys)
                tasks_created += sum(1 for _ in saved)

                for dt in domain_tasks:
                    maybe_sync_to_kanban(kanban=self.kanban, task=dt, message=msg, policy=policy)

                review_cmds: list[ReviewEnqueueCommandDTO] = []
                for item, saved_row in zip(extracted, saved, strict=True):
                    needs_review, reason_code, reason_text = should_enqueue_task_review(
                        item,
                        review_threshold=self.review_threshold,
                    )
                    if not needs_review:
                        continue
                    payload = json.dumps(
                        {
                            "message_id": msg.id,
                            "task_id": saved_row.task_id,
                            "title": item.title,
                            "confidence": item.confidence,
                            "due_at": item.due_at.isoformat() if item.due_at else None,
                        },
                        ensure_ascii=False,
                    )
                    review_cmds.append(
                        ReviewEnqueueCommandDTO(
                            review_kind=ReviewKind.TASK,
                            message_id=msg.id,
                            related_task_id=saved_row.task_id,
                            reason_code=reason_code,
                            reason_text=reason_text,
                            confidence=float(item.confidence),
                            payload_json=payload,
                        )
                    )

                if review_cmds:
                    res = self.enqueue_reviews.execute(run_id=run_id, commands=review_cmds)
                    reviews_enqueued += res.inserted

                self.messages.update_processing_status(msg.id, MessageProcessingStatus.TASKS_EXTRACTED)
                messages_processed += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                self.logger.error(
                    "extract_tasks.message_failed",
                    run_id=run_id,
                    message_id=msg.id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        duration_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "extract_tasks.end",
            run_id=run_id,
            duration_ms=duration_ms,
            messages_processed=messages_processed,
            tasks_created=tasks_created,
            failures=failures,
            reviews_enqueued=reviews_enqueued,
            candidates=len(candidates),
        )
        return ExtractTasksBatchResultDTO(
            run_id=run_id,
            messages_processed=messages_processed,
            tasks_created=tasks_created,
            failures=failures,
            reviews_enqueued=reviews_enqueued,
        )

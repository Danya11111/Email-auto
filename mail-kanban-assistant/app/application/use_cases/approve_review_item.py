from __future__ import annotations

from dataclasses import dataclass

from app.application.ports import LoggerPort, MessageRepositoryPort, ReviewRepositoryPort, TaskRepositoryPort, TriageResultRepositoryPort
from app.domain.enums import MessageProcessingStatus, ReviewKind, TaskStatus
from app.domain.errors import ReviewDecisionError


@dataclass(frozen=True, slots=True)
class ApproveReviewItemUseCase:
    reviews: ReviewRepositoryPort
    messages: MessageRepositoryPort
    triage: TriageResultRepositoryPort
    tasks: TaskRepositoryPort
    logger: LoggerPort

    def execute(self, *, review_id: int, decided_by: str, note: str | None) -> None:
        item = self.reviews.get(review_id)

        if item.review_kind == ReviewKind.TRIAGE:
            self.triage.set_human_confirmed(item.related_message_id, confirmed=True)
            self.messages.update_processing_status(item.related_message_id, MessageProcessingStatus.TRIAGED)
        elif item.review_kind == ReviewKind.TASK:
            if item.related_task_id is None:
                raise ReviewDecisionError("task review is missing related_task_id")
            self.tasks.update_task_status(item.related_task_id, TaskStatus.APPROVED)

        self.reviews.approve(review_id, decided_by=decided_by, note=note)
        self.logger.info(
            "review.approved",
            review_id=review_id,
            kind=item.review_kind.value,
            message_id=item.related_message_id,
            task_id=item.related_task_id,
            decided_by=decided_by,
        )

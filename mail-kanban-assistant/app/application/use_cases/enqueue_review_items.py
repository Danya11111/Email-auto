from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.application.dtos import EnqueueReviewItemsResultDTO, ReviewEnqueueCommandDTO
from app.application.ports import LoggerPort, ReviewRepositoryPort


@dataclass(frozen=True, slots=True)
class EnqueueReviewItemsUseCase:
    reviews: ReviewRepositoryPort
    logger: LoggerPort

    def execute(self, *, run_id: str, commands: Sequence[ReviewEnqueueCommandDTO]) -> EnqueueReviewItemsResultDTO:
        inserted = 0
        skipped = 0
        for cmd in commands:
            _rid, created = self.reviews.enqueue(cmd)
            if created:
                inserted += 1
            else:
                skipped += 1

        self.logger.info(
            "review.enqueue.batch",
            run_id=run_id,
            inserted=inserted,
            skipped_duplicates=skipped,
            commands=len(commands),
        )
        return EnqueueReviewItemsResultDTO(inserted=inserted, skipped_duplicates=skipped)

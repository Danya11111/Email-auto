from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.application.dtos import ReviewListItemDTO
from app.application.ports import ReviewRepositoryPort


@dataclass(frozen=True, slots=True)
class ListPendingReviewsUseCase:
    reviews: ReviewRepositoryPort

    def execute(self, *, limit: int = 200) -> Sequence[ReviewListItemDTO]:
        return tuple(self.reviews.list_pending(limit=limit))

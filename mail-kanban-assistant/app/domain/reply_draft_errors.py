from __future__ import annotations

from app.domain.errors import DomainError


class ReplyDraftError(DomainError):
    """Base for reply draft workflow failures."""


class ReplyDraftNotFoundError(ReplyDraftError):
    ...


class ReplyDraftGenerationError(ReplyDraftError):
    ...


class ReplyDraftPreconditionError(ReplyDraftError):
    ...

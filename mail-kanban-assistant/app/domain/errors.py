from __future__ import annotations


class DomainError(Exception):
    """Base class for domain-level failures."""


class ValidationError(DomainError):
    """Raised when domain invariants or value constraints are violated."""


class DuplicateMessageError(DomainError):
    """Raised when a message with the same deduplication identity already exists."""


class UnsupportedMailSourceError(DomainError):
    """Raised when a mail source is not available or not implemented."""


class ReviewDecisionError(DomainError):
    """Raised when a review decision cannot be applied (missing item, wrong state, etc.)."""

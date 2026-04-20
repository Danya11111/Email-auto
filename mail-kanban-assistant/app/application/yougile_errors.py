"""Map YouGile HTTP outcomes to operator-facing messages (sync, doctor, discovery)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class YougileErrorKind(StrEnum):
    AUTH = "auth"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BAD_RESPONSE = "bad_response"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


def classify_yougile_http_status(status_code: int) -> YougileErrorKind:
    if status_code in (401,):
        return YougileErrorKind.AUTH
    if status_code in (403,):
        return YougileErrorKind.FORBIDDEN
    if status_code == 404:
        return YougileErrorKind.NOT_FOUND
    if status_code == 429:
        return YougileErrorKind.RATE_LIMITED
    if 500 <= status_code <= 599:
        return YougileErrorKind.SERVER_ERROR
    return YougileErrorKind.UNKNOWN


def extract_yougile_api_error_detail(data: Any) -> str | None:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip()
        msg = data.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    return None


def format_yougile_provider_error(
    *,
    status_code: int,
    data: Any,
    fallback_body: str,
    context: str = "request",
) -> str:
    """Single string for KanbanProviderCreateResult.error_message and logs."""
    kind = classify_yougile_http_status(status_code)
    detail = extract_yougile_api_error_detail(data)
    tail = detail or (fallback_body[:600] if fallback_body else "")
    if kind == YougileErrorKind.AUTH:
        return f"YouGile {context}: authentication failed (HTTP {status_code}). Check YOUGILE_API_KEY. {tail}".strip()
    if kind == YougileErrorKind.FORBIDDEN:
        return f"YouGile {context}: access forbidden (HTTP {status_code}). Key may lack permission for this resource. {tail}".strip()
    if kind == YougileErrorKind.NOT_FOUND:
        return f"YouGile {context}: not found (HTTP {status_code}). Check board/column/task IDs. {tail}".strip()
    if kind == YougileErrorKind.RATE_LIMITED:
        return f"YouGile {context}: rate limited (HTTP 429). Slow down sync; YouGile allows ~50 req/min/company. {tail}".strip()
    if kind == YougileErrorKind.SERVER_ERROR:
        return f"YouGile {context}: server error (HTTP {status_code}). Retry later. {tail}".strip()
    if status_code >= 400:
        return f"YouGile {context}: HTTP {status_code}. {tail}".strip()
    return f"YouGile {context}: unexpected HTTP {status_code}. {tail}".strip()


def format_yougile_transport_error(exc: BaseException, *, context: str = "request") -> str:
    name = type(exc).__name__
    msg = str(exc).strip()
    return f"YouGile {context}: network/transport error ({name}): {msg}".strip()

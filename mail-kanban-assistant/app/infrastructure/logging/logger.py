from __future__ import annotations

import json
import logging
from typing import Any

from app.application.ports import LoggerPort


class StructuredLoggerAdapter(LoggerPort):
    """Thin adapter mapping structured fields to stdlib logging."""

    def __init__(self, name: str = "mail_assistant") -> None:
        self._log = logging.getLogger(name)

    def _emit(self, level: int, event: str, fields: dict[str, Any]) -> None:
        payload = {"event": event, **fields}
        self._log.log(level, json.dumps(payload, default=str, ensure_ascii=False))

    def info(self, event: str, **fields: object) -> None:
        self._emit(logging.INFO, event, dict(fields))

    def warning(self, event: str, **fields: object) -> None:
        self._emit(logging.WARNING, event, dict(fields))

    def error(self, event: str, **fields: object) -> None:
        self._emit(logging.ERROR, event, dict(fields))

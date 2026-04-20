from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from app.application.dtos import PersistedMessageDTO
from app.application.ports import KanbanPort, LoggerPort
from app.domain.enums import KanbanCardStatus
from app.domain.models import ExtractedTask, KanbanCardDraft, KanbanProviderCreateResult


def yougile_api_v2_root(base_url: str) -> str:
    """Normalize host or full API root to `.../api-v2` (no trailing slash)."""
    b = (base_url or "").strip().rstrip("/")
    if not b:
        b = "https://ru.yougile.com"
    if b.endswith("/api-v2"):
        return b
    return f"{b}/api-v2"


def yougile_public_origin(api_v2_root: str) -> str:
    if api_v2_root.endswith("/api-v2"):
        return api_v2_root[: -len("/api-v2")]
    return api_v2_root.rstrip("/")


class _SequentialRateLimiter:
    """Soft spacing between HTTP calls (company-wide ~50 rpm in YouGile docs)."""

    def __init__(self, requests_per_minute: float) -> None:
        rpm = float(requests_per_minute)
        if rpm < 1.0:
            rpm = 1.0
        if rpm > 50.0:
            rpm = 50.0
        self._min_interval = 60.0 / rpm
        self._next_allowed = 0.0

    def wait_turn(self) -> None:
        now = time.monotonic()
        wait_for = self._next_allowed - now
        if wait_for > 0.0:
            time.sleep(wait_for)
            now = time.monotonic()
        self._next_allowed = now + self._min_interval


class YougileKanbanAdapter(KanbanPort):
    """YouGile REST API v2 (`/api-v2/tasks`) with Bearer token; sequential requests + soft rate limit."""

    def __init__(
        self,
        *,
        api_v2_root: str,
        api_key: str,
        board_id: str,
        column_id_todo: str,
        column_id_done: str,
        column_id_blocked: str,
        timeout_seconds: float,
        requests_per_minute: int,
        max_description_chars: int,
        include_internal_ids: bool,
        attach_source_metadata: bool,
        logger: LoggerPort,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api = yougile_api_v2_root(api_v2_root)
        self._public = yougile_public_origin(self._api)
        self._key = api_key.strip()
        self._board_id = board_id.strip()
        self._col_todo = column_id_todo.strip()
        self._col_done = column_id_done.strip()
        self._col_blocked = column_id_blocked.strip()
        self._timeout = float(timeout_seconds)
        self._max_desc = max(256, int(max_description_chars))
        self._include_fp = include_internal_ids
        self._attach_meta = attach_source_metadata
        self._logger = logger
        self._limiter = _SequentialRateLimiter(float(requests_per_minute))
        self._http = http_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @contextmanager
    def _client(self) -> Iterator[httpx.Client]:
        if self._http is not None:
            yield self._http
        else:
            with httpx.Client(timeout=self._timeout) as c:
                yield c

    def _config_error(self) -> str | None:
        if not self._key:
            return "Missing YOUGILE_API_KEY"
        if not self._col_todo:
            return "Missing YOUGILE_COLUMN_ID_TODO"
        return None

    def _column_for_status(self, status: KanbanCardStatus) -> str:
        if status == KanbanCardStatus.DONE and self._col_done:
            return self._col_done
        if status == KanbanCardStatus.BLOCKED and self._col_blocked:
            return self._col_blocked
        return self._col_todo

    def _compose_description(self, draft: KanbanCardDraft) -> str:
        body = draft.description.strip()
        lines: list[str] = [body] if body else []
        lines.append(f"Priority (mail-assistant): {draft.priority.value}")
        if self._include_fp:
            lines.append(f"Fingerprint: {draft.fingerprint}")
        if self._attach_meta:
            lines.append("---")
            lines.append("Synced by mail-kanban-assistant (local-first).")
        text = "\n".join(lines).strip()
        if len(text) > self._max_desc:
            text = text[: self._max_desc - 1] + "…"
        return text

    def _deadline_json(self, draft: KanbanCardDraft) -> dict[str, Any] | None:
        if draft.due_at is None:
            return None
        ms = int(draft.due_at.timestamp() * 1000)
        return {"deadline": ms, "withTime": True}

    def _task_url(self, task_id: str) -> str | None:
        if not self._board_id:
            return None
        return f"{self._public}/team/#/board/{self._board_id}/task/{task_id}"

    def _parse_task_id(self, data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        tid = data.get("id")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
        content = data.get("content")
        if isinstance(content, dict):
            tid2 = content.get("id")
            if isinstance(tid2, str) and tid2.strip():
                return tid2.strip()
        return None

    def _error_message(self, resp: httpx.Response) -> str:
        try:
            doc = resp.json()
            if isinstance(doc, dict) and doc.get("error"):
                return f"HTTP {resp.status_code}: {doc.get('error')}"
        except json.JSONDecodeError:
            pass
        return f"HTTP {resp.status_code}: {resp.text[:800]}"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Single HTTP round-trip (no automatic POST retry to avoid duplicate tasks)."""
        self._limiter.wait_turn()
        url = f"{self._api}{path}" if path.startswith("/") else f"{self._api}/{path}"
        started = time.perf_counter()
        with self._client() as client:
            resp = client.request(method, url, headers=self._headers(), json=json_body)
        ms = int((time.perf_counter() - started) * 1000)
        self.logger_info_latency(method, path, resp.status_code, ms)
        if resp.status_code == 429:
            self._logger.warning("kanban.yougile.rate_limited", status=429, hint="space_sync_runs_or_raise_rpm_slightly")
        try:
            return resp.status_code, resp.json()
        except json.JSONDecodeError:
            return resp.status_code, None

    def logger_info_latency(self, method: str, path: str, status: int, ms: int) -> None:
        self._logger.info(
            "kanban.yougile.http",
            method=method,
            path=path,
            status=status,
            duration_ms=ms,
        )

    def create_task_card(self, task: ExtractedTask, message: PersistedMessageDTO) -> str | None:
        self._logger.info("kanban.yougile.extract_path_skipped", message_id=message.id)
        return None

    def create_card(self, draft: KanbanCardDraft) -> KanbanProviderCreateResult:
        err = self._config_error()
        if err:
            return KanbanProviderCreateResult(False, None, None, err)
        column_id = self._column_for_status(draft.card_status)
        body: dict[str, Any] = {
            "title": draft.title.strip()[:1024] or "Task",
            "columnId": column_id,
            "description": self._compose_description(draft),
        }
        dl = self._deadline_json(draft)
        if dl is not None:
            body["deadline"] = dl
        try:
            status, data = self._request_json("POST", "/tasks", json_body=body)
            if status not in (200, 201):
                return KanbanProviderCreateResult(False, None, None, self._error_message_from_status(status, data))
            tid = self._parse_task_id(data)
            if not tid:
                return KanbanProviderCreateResult(False, None, None, "YouGile create: missing task id in response")
            url = self._task_url(tid)
            self._logger.info("kanban.yougile.task_created", task_id=tid)
            return KanbanProviderCreateResult(True, tid, url, None)
        except httpx.HTTPError as exc:
            self._logger.error("kanban.yougile.http_error", error=str(exc))
            return KanbanProviderCreateResult(False, None, None, str(exc))

    def _error_message_from_status(self, status: int, data: Any) -> str:
        if isinstance(data, dict) and data.get("error"):
            return f"HTTP {status}: {data.get('error')}"
        return f"HTTP {status}: empty or non-JSON body"

    def update_card(self, draft: KanbanCardDraft, *, external_card_id: str) -> KanbanProviderCreateResult:
        err = self._config_error()
        if err:
            return KanbanProviderCreateResult(False, None, None, err)
        tid = external_card_id.strip()
        if not tid:
            return KanbanProviderCreateResult(False, None, None, "Missing external YouGile task id for update")
        column_id = self._column_for_status(draft.card_status)
        body: dict[str, Any] = {
            "title": draft.title.strip()[:1024] or "Task",
            "columnId": column_id,
            "description": self._compose_description(draft),
        }
        dl = self._deadline_json(draft)
        if dl is not None:
            body["deadline"] = dl
        try:
            status, data = self._request_json("PUT", f"/tasks/{tid}", json_body=body)
            if status not in (200, 201):
                return KanbanProviderCreateResult(False, None, None, self._error_message_from_status(status, data))
            self._logger.info("kanban.yougile.task_updated", task_id=tid)
            url = self._task_url(tid)
            return KanbanProviderCreateResult(True, tid, url, None)
        except httpx.HTTPError as exc:
            self._logger.error("kanban.yougile.http_error", error=str(exc))
            return KanbanProviderCreateResult(False, None, None, str(exc))

    def healthcheck(self) -> bool:
        if self._config_error():
            return False
        if not self._board_id:
            return False
        try:
            status, _ = self._request_json("GET", f"/boards/{self._board_id}")
            return status == 200
        except httpx.HTTPError:
            return False

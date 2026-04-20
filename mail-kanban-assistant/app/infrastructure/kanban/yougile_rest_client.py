"""YouGile REST read helpers (boards/columns discovery, probes). Kept separate from KanbanPort write path."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from app.application.dtos import (
    YougileDiscoveryBoardDTO,
    YougileDiscoveryColumnDTO,
    YougileWorkspaceDiscoveryDTO,
)
from app.application.ports import LoggerPort
from app.application.yougile_errors import format_yougile_provider_error, format_yougile_transport_error
from app.config import AppSettings
from app.infrastructure.kanban.yougile_adapter import yougile_api_v2_root


class _Rate:
    def __init__(self, rpm: float) -> None:
        r = float(rpm)
        if r < 1.0:
            r = 1.0
        if r > 50.0:
            r = 50.0
        self._interval = 60.0 / r
        self._next = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        w = self._next - now
        if w > 0.0:
            time.sleep(w)
            now = time.monotonic()
        self._next = now + self._interval


def _extract_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("content", "data", "items", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
    return []


def _board_from_obj(obj: Any) -> YougileDiscoveryBoardDTO | None:
    if not isinstance(obj, dict):
        return None
    bid = obj.get("id")
    title = obj.get("title")
    if not isinstance(bid, str) or not isinstance(title, str):
        return None
    pid = obj.get("projectId")
    return YougileDiscoveryBoardDTO(
        id=bid.strip(),
        title=title.strip() or "(untitled)",
        deleted=bool(obj.get("deleted")),
        project_id=str(pid).strip() if isinstance(pid, str) and pid.strip() else None,
    )


def _column_from_obj(obj: Any) -> YougileDiscoveryColumnDTO | None:
    if not isinstance(obj, dict):
        return None
    cid = obj.get("id")
    title = obj.get("title")
    bid = obj.get("boardId")
    if not isinstance(cid, str) or not isinstance(title, str) or not isinstance(bid, str):
        return None
    return YougileDiscoveryColumnDTO(
        id=cid.strip(),
        title=title.strip() or "(untitled)",
        board_id=bid.strip(),
        deleted=bool(obj.get("deleted")),
    )


class YougileRestClient:
    """Read-only YouGile API v2 client (discovery / doctor probes)."""

    def __init__(
        self,
        *,
        api_v2_root: str,
        api_key: str,
        timeout_seconds: float,
        requests_per_minute: int,
        logger: LoggerPort,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api = yougile_api_v2_root(api_v2_root)
        self._key = api_key.strip()
        self._timeout = float(timeout_seconds)
        self._logger = logger
        self._rate = _Rate(float(requests_per_minute))
        self._http = http_client

    @classmethod
    def from_settings(cls, settings: AppSettings, logger: LoggerPort, http_client: httpx.Client | None = None) -> YougileRestClient:
        return cls(
            api_v2_root=settings.yougile_base_url,
            api_key=settings.yougile_api_key,
            timeout_seconds=float(settings.yougile_request_timeout_seconds),
            requests_per_minute=int(settings.yougile_requests_per_minute),
            logger=logger,
            http_client=http_client,
        )

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

    def request_json(self, method: str, path: str) -> tuple[int, Any, str]:
        """Returns (status, json_or_none, raw_text_slice)."""
        self._rate.wait()
        url = f"{self._api}{path}" if path.startswith("/") else f"{self._api}/{path}"
        started = time.perf_counter()
        try:
            with self._client() as client:
                resp = client.request(method, url, headers=self._headers())
            raw = resp.text[:4000]
            ms = int((time.perf_counter() - started) * 1000)
            self._logger.info("yougile.rest", method=method, path=path, status=resp.status_code, duration_ms=ms)
            try:
                return resp.status_code, resp.json(), raw
            except json.JSONDecodeError:
                return resp.status_code, None, raw
        except httpx.HTTPError as exc:
            self._logger.warning("yougile.rest.transport", method=method, path=path, error=str(exc))
            raise

    def discover_workspace(self) -> YougileWorkspaceDiscoveryDTO:
        warnings: list[str] = []
        if not self._key:
            return YougileWorkspaceDiscoveryDTO(ok=False, error="Missing YOUGILE_API_KEY", boards=(), columns=(), warnings=())
        try:
            st_b, data_b, raw_b = self.request_json("GET", "/boards")
        except httpx.HTTPError as exc:
            return YougileWorkspaceDiscoveryDTO(
                ok=False,
                error=format_yougile_transport_error(exc, context="discover boards"),
                boards=(),
                columns=(),
                warnings=(),
            )
        if st_b != 200:
            return YougileWorkspaceDiscoveryDTO(
                ok=False,
                error=format_yougile_provider_error(status_code=st_b, data=data_b, fallback_body=raw_b, context="GET /boards"),
                boards=(),
                columns=(),
                warnings=(),
            )
        boards_raw = _extract_list(data_b)
        boards: list[YougileDiscoveryBoardDTO] = []
        for item in boards_raw:
            b = _board_from_obj(item)
            if b is not None and not b.deleted:
                boards.append(b)
        if not boards and boards_raw:
            warnings.append("Boards payload parsed to zero rows; API shape may differ — inspect JSON mode.")

        try:
            st_c, data_c, raw_c = self.request_json("GET", "/columns")
        except httpx.HTTPError as exc:
            return YougileWorkspaceDiscoveryDTO(
                ok=True,
                error=None,
                boards=tuple(boards),
                columns=(),
                warnings=tuple(warnings + [format_yougile_transport_error(exc, context="discover columns")]),
            )
        if st_c != 200:
            warnings.append(
                format_yougile_provider_error(status_code=st_c, data=data_c, fallback_body=raw_c, context="GET /columns")
            )
            return YougileWorkspaceDiscoveryDTO(ok=True, boards=tuple(boards), columns=(), warnings=tuple(warnings))

        cols_raw = _extract_list(data_c)
        columns: list[YougileDiscoveryColumnDTO] = []
        for item in cols_raw:
            c = _column_from_obj(item)
            if c is not None and not c.deleted:
                columns.append(c)
        if not columns and cols_raw:
            warnings.append("Columns payload parsed to zero rows; API shape may differ — inspect JSON mode.")

        return YougileWorkspaceDiscoveryDTO(ok=True, boards=tuple(boards), columns=tuple(columns), warnings=tuple(warnings))

    def get_board_status(self, board_id: str) -> tuple[int, Any, str]:
        bid = board_id.strip()
        return self.request_json("GET", f"/boards/{bid}")

    def get_column_status(self, column_id: str) -> tuple[int, Any, str]:
        cid = column_id.strip()
        return self.request_json("GET", f"/columns/{cid}")

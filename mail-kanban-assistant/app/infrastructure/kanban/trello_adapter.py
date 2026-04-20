from __future__ import annotations

import httpx

from app.application.dtos import PersistedMessageDTO
from app.application.ports import KanbanPort, LoggerPort
from app.domain.models import ExtractedTask, KanbanCardDraft, KanbanProviderCreateResult


class TrelloKanbanAdapter(KanbanPort):
    """Minimal Trello REST client (no SDK). Requires API key + token + list id."""

    _BASE = "https://api.trello.com/1"

    def __init__(
        self,
        *,
        api_key: str,
        token: str,
        list_id_todo: str,
        logger: LoggerPort,
        timeout_seconds: float = 25.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._token = token.strip()
        self._list_id = list_id_todo.strip()
        self._logger = logger
        self._timeout = timeout_seconds

    def create_task_card(self, task: ExtractedTask, message: PersistedMessageDTO) -> str | None:
        self._logger.info("kanban.trello.extract_path_skipped", message_id=message.id)
        return None

    def create_card(self, draft: KanbanCardDraft) -> KanbanProviderCreateResult:
        if not self._api_key or not self._token or not self._list_id:
            return KanbanProviderCreateResult(
                success=False,
                external_card_id=None,
                external_card_url=None,
                error_message="Missing TRELLO_API_KEY, TRELLO_TOKEN, or TRELLO_LIST_ID_TODO",
            )
        params = {
            "key": self._api_key,
            "token": self._token,
            "idList": self._list_id,
            "name": draft.title[:16384],
            "desc": draft.description[:16384],
        }
        if draft.due_at is not None:
            params["due"] = draft.due_at.isoformat()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(f"{self._BASE}/cards", params=params)
            if resp.status_code >= 400:
                body = resp.text[:800]
                self._logger.warning("kanban.trello.create_failed", status=resp.status_code, body=body)
                return KanbanProviderCreateResult(
                    success=False,
                    external_card_id=None,
                    external_card_url=None,
                    error_message=f"HTTP {resp.status_code}: {body}",
                )
            data = resp.json()
            cid = str(data.get("id", "")) or None
            url = str(data.get("url") or data.get("shortUrl") or "") or None
            self._logger.info("kanban.trello.card_created", card_id=cid)
            return KanbanProviderCreateResult(success=True, external_card_id=cid, external_card_url=url, error_message=None)
        except httpx.HTTPError as exc:
            self._logger.error("kanban.trello.http_error", error=str(exc))
            return KanbanProviderCreateResult(success=False, external_card_id=None, external_card_url=None, error_message=str(exc))

    def healthcheck(self) -> bool:
        if not self._api_key or not self._token:
            return False
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    f"{self._BASE}/members/me",
                    params={"key": self._api_key, "token": self._token, "fields": "username"},
                )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

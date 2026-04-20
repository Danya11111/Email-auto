from __future__ import annotations

import json
from pathlib import Path

from app.application.dtos import PersistedMessageDTO
from app.application.ports import KanbanPort, LoggerPort
from app.domain.models import ExtractedTask, KanbanCardDraft, KanbanProviderCreateResult


class LocalFileKanbanAdapter(KanbanPort):
    """Writes one JSON card per task under kanban_root/cards/ (idempotent by stable filename)."""

    def __init__(self, *, root_dir: Path, logger: LoggerPort) -> None:
        self._root = root_dir
        self._logger = logger

    def _cards_dir(self) -> Path:
        return self._root / "cards"

    def create_task_card(self, task: ExtractedTask, message: PersistedMessageDTO) -> str | None:
        self._logger.info(
            "kanban.local_file.extract_path_skipped",
            message_id=message.id,
            hint="Use kanban-sync for approved tasks",
        )
        return None

    def create_card(self, draft: KanbanCardDraft) -> KanbanProviderCreateResult:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            cdir = self._cards_dir()
            cdir.mkdir(parents=True, exist_ok=True)
            path = cdir / f"task_{draft.internal_task_id}.json"
            payload = {
                "dedupe_marker": draft.dedupe_marker,
                "title": draft.title,
                "description": draft.description,
                "due_at": draft.due_at.isoformat() if draft.due_at else None,
                "priority": draft.priority.value,
                "card_status": draft.card_status.value,
                "labels": list(draft.labels),
                "fingerprint": draft.fingerprint,
                "internal_task_id": draft.internal_task_id,
                "source_message_id": draft.source_message_id,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            external_id = f"local_file:{draft.internal_task_id}"
            url = path.resolve().as_uri()
            self._logger.info("kanban.local_file.card_written", path=str(path), task_id=draft.internal_task_id)
            return KanbanProviderCreateResult(success=True, external_card_id=external_id, external_card_url=url, error_message=None)
        except OSError as exc:
            self._logger.error("kanban.local_file.write_failed", error=str(exc))
            return KanbanProviderCreateResult(
                success=False, external_card_id=None, external_card_url=None, error_message=str(exc)
            )

    def healthcheck(self) -> bool:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            probe = self._root / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            return True
        except OSError:
            return False

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.kanban_resync_policy import KanbanOutboundPlan, plan_kanban_outbound
from app.config import AppSettings
from app.domain.enums import KanbanCardStatus, KanbanPriority, KanbanProvider, KanbanSyncStatus, MessageImportance, ReplyRequirement, TaskStatus
from app.domain.models import KanbanCardDraft


class _MemSync:
    def __init__(self, *, synced_fp: str | None = None, ext_id: str | None = "ext-1", status: KanbanSyncStatus = KanbanSyncStatus.SYNCED):
        self._fp = synced_fp
        self._ext = ext_id
        self._st = status

    def maybe_skip_if_already_synced_same_fingerprint(self, *, task_id: int, provider: KanbanProvider, fingerprint: str) -> bool:
        _ = (task_id, provider)
        if self._st != KanbanSyncStatus.SYNCED or self._fp is None:
            return False
        return fingerprint == self._fp

    def get_sync_record_for_task(self, task_id: int, provider: KanbanProvider):
        _ = (task_id, provider)
        if self._fp is None:
            return None
        from app.application.dtos import KanbanSyncRecordRowDTO

        return KanbanSyncRecordRowDTO(
            id=1,
            task_id=1,
            provider=provider,
            sync_status=self._st,
            external_card_id=self._ext,
            external_card_url=None,
            card_fingerprint=self._fp or "",
            payload_json="{}",
            created_at=datetime.now(tz=UTC),
            synced_at=datetime.now(tz=UTC),
            last_attempt_at=None,
            last_error=None,
            retry_count=0,
        )


def _draft(fp: str = "a") -> KanbanCardDraft:
    return KanbanCardDraft(
        internal_task_id=1,
        source_message_id=1,
        title="t",
        description="d",
        due_at=None,
        priority=KanbanPriority.LOW,
        card_status=KanbanCardStatus.TODO,
        labels=(),
        dedupe_marker="x",
        fingerprint=fp,
    )


def test_plan_skip_same_fingerprint() -> None:
    sync = _MemSync(synced_fp="same")
    s = AppSettings()
    assert plan_kanban_outbound(provider=KanbanProvider.YOUGILE, settings=s, sync=sync, task_id=1, draft=_draft("same")) == KanbanOutboundPlan.SKIP_SAME_FINGERPRINT


def test_plan_yougile_skip_manual_on_fp_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_ENABLE_UPDATE_EXISTING", "false")
    s = AppSettings()
    sync = _MemSync(synced_fp="old")
    assert (
        plan_kanban_outbound(provider=KanbanProvider.YOUGILE, settings=s, sync=sync, task_id=1, draft=_draft("new"))
        == KanbanOutboundPlan.SKIP_MANUAL_RESYNC
    )


def test_plan_yougile_update_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_ENABLE_UPDATE_EXISTING", "true")
    s = AppSettings()
    sync = _MemSync(synced_fp="old")
    assert plan_kanban_outbound(provider=KanbanProvider.YOUGILE, settings=s, sync=sync, task_id=1, draft=_draft("new")) == KanbanOutboundPlan.UPDATE_EXISTING


def test_plan_local_file_recreate_on_fp_change() -> None:
    s = AppSettings()
    sync = _MemSync(synced_fp="old")
    assert plan_kanban_outbound(provider=KanbanProvider.LOCAL_FILE, settings=s, sync=sync, task_id=1, draft=_draft("new")) == KanbanOutboundPlan.CREATE


def test_plan_failed_resume_update_same_fp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUGILE_ENABLE_UPDATE_EXISTING", "false")
    s = AppSettings()
    sync = _MemSync(synced_fp="fp1", ext_id="e1", status=KanbanSyncStatus.FAILED)
    assert plan_kanban_outbound(provider=KanbanProvider.YOUGILE, settings=s, sync=sync, task_id=1, draft=_draft("fp1")) == KanbanOutboundPlan.UPDATE_EXISTING

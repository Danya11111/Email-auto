from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.application.ports import ClockPort, ReplyDraftExporterPort, ReplyDraftRepositoryPort
from app.application.reply_draft_policy import assert_export_preconditions
from app.config import AppSettings
from app.domain.enums import ReplyDraftStatus
from app.domain.reply_draft_errors import ReplyDraftNotFoundError, ReplyDraftPreconditionError


@dataclass(frozen=True, slots=True)
class ApproveReplyDraftUseCase:
    drafts: ReplyDraftRepositoryPort
    clock: ClockPort

    def execute(self, draft_id: int, *, decided_by: str, note: str | None) -> None:
        d = self.drafts.get_reply_draft(draft_id)
        if d is None:
            raise ReplyDraftNotFoundError(f"draft {draft_id} not found")
        if d.status != ReplyDraftStatus.GENERATED:
            raise ReplyDraftPreconditionError(f"approve requires status=generated, got {d.status.value}")
        self.drafts.mark_reply_draft_approved(draft_id, decided_by=decided_by, note=note, now_iso=self.clock.now().isoformat())


@dataclass(frozen=True, slots=True)
class RejectReplyDraftUseCase:
    drafts: ReplyDraftRepositoryPort
    clock: ClockPort

    def execute(self, draft_id: int, *, decided_by: str, note: str | None) -> None:
        d = self.drafts.get_reply_draft(draft_id)
        if d is None:
            raise ReplyDraftNotFoundError(f"draft {draft_id} not found")
        if d.status != ReplyDraftStatus.GENERATED:
            raise ReplyDraftPreconditionError(f"reject requires status=generated, got {d.status.value}")
        self.drafts.mark_reply_draft_rejected(draft_id, decided_by=decided_by, note=note, now_iso=self.clock.now().isoformat())


@dataclass(frozen=True, slots=True)
class ExportReplyDraftUseCase:
    drafts: ReplyDraftRepositoryPort
    exporter: ReplyDraftExporterPort
    clock: ClockPort
    settings: AppSettings

    def execute(self, draft_id: int, *, out_path: Path, as_markdown: bool) -> Path:
        d = self.drafts.get_reply_draft(draft_id)
        if d is None:
            raise ReplyDraftNotFoundError(f"draft {draft_id} not found")
        assert_export_preconditions(d, settings=self.settings)
        if as_markdown:
            path = self.exporter.export_markdown(draft=d, path=out_path)
        else:
            path = self.exporter.export_plain_text(draft=d, path=out_path)
        self.drafts.mark_reply_draft_exported(draft_id, now_iso=self.clock.now().isoformat())
        return path
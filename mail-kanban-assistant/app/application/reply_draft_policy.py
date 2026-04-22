from __future__ import annotations

from app.config import AppSettings
from app.domain.enums import ReplyDraftGenerationMode, ReplyDraftStatus, ReplyState
from app.domain.reply_draft import ReplyDraft
from app.domain.reply_draft_errors import ReplyDraftPreconditionError


def reply_states_allowing_generation() -> frozenset[ReplyState]:
    return frozenset(
        {
            ReplyState.WAITING_FOR_US,
            ReplyState.OVERDUE_FOR_US,
            ReplyState.REPLY_RECOMMENDED_TODAY,
        }
    )


def generation_allowed_for_reply_state(
    reply_state: ReplyState,
    *,
    force: bool,
    settings: AppSettings,
) -> None:
    if reply_state in reply_states_allowing_generation():
        return
    if reply_state == ReplyState.WAITING_FOR_THEM and force:
        return
    if reply_state == ReplyState.NO_REPLY_NEEDED and force and settings.reply_draft_allow_force_on_no_reply_needed:
        return
    if reply_state == ReplyState.NO_REPLY_NEEDED:
        raise ReplyDraftPreconditionError(
            "reply draft generation blocked: reply_state is no_reply_needed (use --force only if policy allows)"
        )
    if reply_state == ReplyState.WAITING_FOR_THEM:
        raise ReplyDraftPreconditionError("reply draft generation discouraged: waiting_for_them (use --force to override)")
    if reply_state == ReplyState.AMBIGUOUS:
        raise ReplyDraftPreconditionError("reply draft generation blocked: ambiguous reply_state (resolve reviews first)")
    raise ReplyDraftPreconditionError(f"reply draft generation not allowed for reply_state={reply_state.value}")


def assert_export_preconditions(draft: ReplyDraft, *, settings: AppSettings) -> None:
    if settings.reply_draft_require_approval_before_export and draft.status != ReplyDraftStatus.APPROVED:
        raise ReplyDraftPreconditionError("export requires approved draft (REPLY_DRAFT_REQUIRE_APPROVAL_BEFORE_EXPORT=true)")
    if draft.status not in (ReplyDraftStatus.APPROVED, ReplyDraftStatus.EXPORTED):
        if not settings.reply_draft_require_approval_before_export and draft.status == ReplyDraftStatus.GENERATED:
            return
        raise ReplyDraftPreconditionError(f"export not allowed in status={draft.status.value}")


def assert_regenerate_preconditions(draft: ReplyDraft, *, force: bool) -> None:
    if draft.status in (ReplyDraftStatus.GENERATED, ReplyDraftStatus.STALE, ReplyDraftStatus.REJECTED):
        return
    if draft.status == ReplyDraftStatus.APPROVED and not force:
        raise ReplyDraftPreconditionError("regenerate blocked: approved draft (use --force for controlled regeneration)")
    if draft.status == ReplyDraftStatus.EXPORTED and not force:
        raise ReplyDraftPreconditionError("regenerate blocked: exported draft (use --force)")


def pick_generation_mode(
    *,
    existing_latest: ReplyDraft | None,
    current_fingerprint: str,
    explicit_regenerate: bool,
) -> ReplyDraftGenerationMode:
    if explicit_regenerate:
        return ReplyDraftGenerationMode.REGENERATE
    if existing_latest is not None and existing_latest.generation_fingerprint != current_fingerprint:
        return ReplyDraftGenerationMode.REFRESH_AFTER_THREAD_CHANGE
    return ReplyDraftGenerationMode.INITIAL


def should_reuse_existing_generated_draft(
    existing: ReplyDraft | None,
    *,
    current_fingerprint: str,
    force: bool,
) -> bool:
    if force or existing is None:
        return False
    if existing.generation_fingerprint != current_fingerprint:
        return False
    if existing.status == ReplyDraftStatus.GENERATED:
        return True
    if existing.status == ReplyDraftStatus.APPROVED:
        return True
    return False

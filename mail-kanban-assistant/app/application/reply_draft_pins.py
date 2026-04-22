from __future__ import annotations

from app.application.dtos import ActionCenterSnapshotDTO, ReplyDraftDigestSectionDTO, ReplyDraftThreadPinDTO
from app.application.reply_context_builder import SqliteReplyContextBuilder
from app.application.reply_draft_fingerprint import fingerprint_for_reply_context
from app.application.reply_draft_policy import reply_states_allowing_generation
from app.config import AppSettings
from app.domain.enums import ReplyDraftStatus, ReplyState
from app.infrastructure.storage.sqlite_reply_draft_repository import SqliteReplyDraftRepository


def _action_step_for_thread(snapshot: ActionCenterSnapshotDTO, thread_id: str) -> str | None:
    for it in snapshot.items:
        if it.thread_id == thread_id and it.source_type == "thread":
            return it.recommended_next_step
    return None


def build_reply_draft_thread_pins(
    *,
    snapshot: ActionCenterSnapshotDTO,
    settings: AppSettings,
    drafts: SqliteReplyDraftRepository,
    builder: SqliteReplyContextBuilder,
    now_iso: str,
) -> dict[str, ReplyDraftThreadPinDTO]:
    """One pin per thread that participates in reply draft workflow (deterministic)."""
    pins: dict[str, ReplyDraftThreadPinDTO] = {}
    allowed = reply_states_allowing_generation()

    for summary in snapshot.threads:
        if not summary.include_in_action_center:
            continue
        tid = summary.thread_id
        latest = drafts.find_latest_for_thread(tid)
        rs = summary.reply_state
        needs_reply = rs in allowed
        if not needs_reply and latest is None:
            continue

        try:
            ctx = builder.build_for_thread(
                thread_id=tid,
                message_ids=summary.related_message_ids,
                primary_message_id=None,
                reply_state=rs,
                action_center_next_step=_action_step_for_thread(snapshot, tid),
            )
        except ValueError:
            continue
        current_fp = fingerprint_for_reply_context(ctx)

        wf = "none"
        latest_id = latest.id if latest else None
        latest_status = latest.status if latest else None
        stored_fp = latest.generation_fingerprint if latest else None

        if latest is None:
            wf = "missing" if needs_reply else "none"
        elif latest.status == ReplyDraftStatus.REJECTED:
            wf = "missing" if needs_reply else "none"
        elif latest.status == ReplyDraftStatus.STALE:
            wf = "stale"
        elif stored_fp != current_fp:
            wf = "stale"
            if settings.reply_draft_mark_stale_on_thread_change and latest.status == ReplyDraftStatus.GENERATED:
                drafts.mark_reply_draft_stale(latest.id, now_iso=now_iso)
                latest_status = ReplyDraftStatus.STALE
        elif latest.status == ReplyDraftStatus.GENERATED:
            wf = "ready_review"
        elif latest.status == ReplyDraftStatus.APPROVED and latest.exported_at is None:
            wf = "approved_not_exported"
        elif latest.status == ReplyDraftStatus.EXPORTED:
            wf = "none"
        else:
            wf = "none"

        pins[tid] = ReplyDraftThreadPinDTO(
            thread_id=tid,
            current_fingerprint=current_fp,
            latest_draft_id=latest_id,
            latest_status=latest_status,
            stored_fingerprint=stored_fp,
            workflow=wf,
        )
    return pins


def build_reply_draft_digest_section(
    *,
    snapshot: ActionCenterSnapshotDTO,
    pins: dict[str, ReplyDraftThreadPinDTO],
) -> ReplyDraftDigestSectionDTO:
    needing: list[str] = []
    ready: list[str] = []
    stale: list[str] = []
    appr: list[str] = []

    for tid, pin in pins.items():
        subj = next((t.subject_line for t in snapshot.threads if t.thread_id == tid), tid)
        line = f"{tid} — {subj[:100]}"
        if pin.workflow == "missing":
            needing.append(line)
        elif pin.workflow == "ready_review":
            ready.append(line)
        elif pin.workflow == "stale":
            stale.append(line)
        elif pin.workflow == "approved_not_exported":
            appr.append(line)

    return ReplyDraftDigestSectionDTO(
        needing_draft=tuple(sorted(needing)),
        ready_for_review=tuple(sorted(ready)),
        stale=tuple(sorted(stale)),
        approved_not_exported=tuple(sorted(appr)),
    )


def executive_reply_draft_bullets(pins: dict[str, ReplyDraftThreadPinDTO]) -> tuple[str, ...]:
    c = {"missing": 0, "ready_review": 0, "stale": 0, "approved_not_exported": 0}
    for p in pins.values():
        if p.workflow in c:
            c[p.workflow] += 1
    if sum(c.values()) == 0:
        return ()
    parts = []
    if c["missing"]:
        parts.append(f"{c['missing']} thread(s) need a reply draft")
    if c["ready_review"]:
        parts.append(f"{c['ready_review']} draft(s) ready for review")
    if c["stale"]:
        parts.append(f"{c['stale']} stale draft(s) (context changed)")
    if c["approved_not_exported"]:
        parts.append(f"{c['approved_not_exported']} approved draft(s) not exported yet")
    return ("Reply draft workload: " + "; ".join(parts) + ".",)

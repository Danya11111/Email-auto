from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.application.dtos import ReplyDraftContextDTO, ReplyDraftContextMessageDTO
from app.application.llm_input import LlmTextPolicy, prepare_body_for_llm
from app.application.ports import MessageRepositoryPort, ReplyContextBuilderPort, ReviewRepositoryPort, TaskRepositoryPort
from app.application.thread_subject import normalize_subject
from app.config import AppSettings
from app.domain.enums import ReplyState
from app.domain.models import TriageResult


class SqliteReplyContextBuilder(ReplyContextBuilderPort):
    """Builds bounded, auditable context for reply draft generation (no full-thread dump)."""

    def __init__(
        self,
        *,
        messages: MessageRepositoryPort,
        tasks: TaskRepositoryPort,
        reviews: ReviewRepositoryPort,
        triage_get: Callable[[int], TriageResult | None],
        settings: AppSettings,
        llm_text_policy: LlmTextPolicy,
    ) -> None:
        self._messages = messages
        self._tasks = tasks
        self._reviews = reviews
        self._triage_get = triage_get
        self._settings = settings
        self._llm_text_policy = llm_text_policy

    def build_for_thread(
        self,
        *,
        thread_id: str,
        message_ids: Sequence[int],
        primary_message_id: int | None,
        reply_state: ReplyState,
        action_center_next_step: str | None,
    ) -> ReplyDraftContextDTO:
        mids = tuple(sorted(int(x) for x in message_ids))
        rows = list(self._messages.list_messages_by_ids(mids))
        by_id = {m.id: m for m in rows}
        if not rows:
            raise ValueError("no messages for thread context")

        ordered = sorted(rows, key=lambda m: (m.received_at or datetime.min.replace(tzinfo=UTC), m.id))
        primary = primary_message_id if primary_message_id in by_id else ordered[-1].id
        primary_msg = by_id[primary]
        triage_primary = self._triage_get(primary)
        latest_summary = triage_primary.summary if triage_primary else "(no triage summary)"

        max_msgs = max(1, int(self._settings.reply_draft_max_context_messages))
        tail = ordered[-max_msgs:]

        per_msg_chars = max(400, int(self._settings.reply_draft_max_input_chars) // max(1, len(tail)))

        def excerpt(body: str) -> str:
            pol = LlmTextPolicy(
                max_input_chars=per_msg_chars,
                truncate_strategy=self._settings.message_body_truncate_strategy,
            )
            return prepare_body_for_llm(body, pol)

        ctx_msgs: list[ReplyDraftContextMessageDTO] = []
        char_est = 0
        for m in tail:
            ex = excerpt(m.body_plain)
            char_est += len(ex) + 80
            ctx_msgs.append(
                ReplyDraftContextMessageDTO(
                    message_id=m.id,
                    received_at=m.received_at,
                    direction="inbound_latest" if m.id == primary else "thread",
                    sender=m.sender,
                    subject=m.subject,
                    body_excerpt=ex,
                )
            )

        norm_subj = normalize_subject(primary_msg.subject) or "(no subject)"

        task_points: list[str] = []
        source_task_ids: list[int] = []
        if self._settings.reply_draft_include_tasks:
            for t in self._tasks.list_tasks_for_message_ids(mids):
                line = f"t{t.id}: {t.title.strip()}"
                if t.due_at:
                    line += f" (due {t.due_at.isoformat()})"
                task_points.append(line[:240])
                source_task_ids.append(t.id)

        review_notes: list[str] = []
        source_review_ids: list[int] = []
        if self._settings.reply_draft_include_review_notes:
            for r in self._reviews.list_pending_for_message_ids(mids):
                source_review_ids.append(r.id)
                review_notes.append(f"r{r.id} [{r.review_kind.value}] {r.reason_code}: {r.reason_text[:200]}")

        ac_step = action_center_next_step if self._settings.reply_draft_include_action_center_reason else None

        deadlines = [ln for ln in task_points if "due " in ln]

        safe_facts: list[str] = [
            f"thread_subject={norm_subj}",
            f"primary_message_id={primary}",
            f"reply_state={reply_state.value}",
        ]
        if triage_primary:
            safe_facts.append(f"triage_reply_requirement={triage_primary.reply_requirement.value}")

        unknown = [
            "Exact mailbox owner identity vs counterparty may be ambiguous from headers alone.",
            "Do not invent dates, amounts, or commitments not explicitly present in excerpts.",
        ]

        budget = int(self._settings.reply_draft_max_input_chars)
        used = sum(len(m.body_excerpt) + 80 for m in ctx_msgs)
        if used > budget and ctx_msgs:
            over = used - budget + 64
            per = int(over // max(1, len(ctx_msgs)))
            trimmed: list[ReplyDraftContextMessageDTO] = []
            for m in ctx_msgs:
                ex = m.body_excerpt
                if per > 0 and len(ex) > 120:
                    cut = max(120, len(ex) - per)
                    ex = ex[:cut] + "…"
                trimmed.append(m.model_copy(update={"body_excerpt": ex}))
            ctx_msgs = trimmed
            char_est = sum(len(m.body_excerpt) + 80 for m in ctx_msgs)

        return ReplyDraftContextDTO(
            thread_id=thread_id,
            normalized_subject=norm_subj[:200],
            reply_state=reply_state,
            primary_message_id=primary,
            latest_inbound_summary=latest_summary[:800],
            triage_reply_requirement=triage_primary.reply_requirement if triage_primary else None,
            triage_importance=triage_primary.importance if triage_primary else None,
            triage_summary_primary=triage_primary.summary[:600] if triage_primary else None,
            messages_included=tuple(ctx_msgs),
            extracted_task_points=tuple(task_points[:12]),
            pending_review_notes=tuple(review_notes[:8]),
            action_center_next_step=(ac_step[:400] if ac_step else None),
            deadlines=tuple(deadlines[:8]),
            safe_facts=tuple(safe_facts[:12]),
            unknown_or_unverified=tuple(unknown),
            context_char_estimate=char_est,
            source_task_ids=tuple(sorted(source_task_ids)),
            source_review_ids=tuple(sorted(source_review_ids)),
        )

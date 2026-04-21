from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.application.dtos import TaskKanbanSourceContextDTO
from app.domain.enums import KanbanCardStatus, KanbanPriority, MessageImportance, TaskStatus
from app.domain.models import KanbanCardDraft


@dataclass(frozen=True, slots=True)
class KanbanMappingOptions:
    max_title_chars: int = 120
    max_desc_chars: int = 4000
    include_review_metadata: bool = True
    include_message_metadata: bool = True
    default_card_status: KanbanCardStatus = KanbanCardStatus.TODO
    assignee_external_id: str | None = None


def resolve_card_status_for_kanban_task(task_status: TaskStatus, default: KanbanCardStatus) -> KanbanCardStatus:
    """Map persisted task lifecycle to logical card column state (drives fingerprint + YouGile column policy)."""
    if task_status == TaskStatus.SYNCED:
        return KanbanCardStatus.DONE
    if task_status == TaskStatus.REJECTED:
        return KanbanCardStatus.BLOCKED
    if task_status == TaskStatus.CANDIDATE:
        return default
    return KanbanCardStatus.TODO if task_status == TaskStatus.APPROVED else default


def triage_importance_to_priority(importance: MessageImportance | None) -> KanbanPriority:
    if importance in (MessageImportance.HIGH, MessageImportance.CRITICAL):
        return KanbanPriority.HIGH
    if importance == MessageImportance.LOW:
        return KanbanPriority.LOW
    return KanbanPriority.MEDIUM


def compute_card_fingerprint(
    *,
    internal_task_id: int,
    source_message_id: int,
    title: str,
    description: str,
    due_at_iso: str | None,
    priority: KanbanPriority,
    card_status: KanbanCardStatus,
    labels: tuple[str, ...],
    dedupe_marker: str,
) -> str:
    payload = {
        "card_status": card_status.value,
        "dedupe_marker": dedupe_marker,
        "description": description,
        "due_at": due_at_iso,
        "internal_task_id": internal_task_id,
        "labels": list(labels),
        "priority": priority.value,
        "source_message_id": source_message_id,
        "title": title,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_kanban_card_draft(ctx: TaskKanbanSourceContextDTO, options: KanbanMappingOptions) -> KanbanCardDraft:
    title = ctx.task.title.strip().replace("\n", " ")
    if len(title) > options.max_title_chars:
        title = title[: options.max_title_chars - 1] + "…"

    lines: list[str] = [
        f"Internal task id: t{ctx.task.id}",
        f"Internal message id: m{ctx.task.message_id}",
    ]
    if options.include_message_metadata:
        lines.append(f"Message subject: {ctx.message_subject or '(none)'}")
        lines.append(f"Message sender: {ctx.message_sender or '(unknown)'}")
    lines.append(f"Task title: {ctx.task.title.strip()}")
    if ctx.task.description:
        desc_snip = ctx.task.description.strip().replace("\n", " ")
        lines.append(f"Task description: {desc_snip[:800]}")
    if ctx.task.due_at is not None:
        lines.append(f"Task due: {ctx.task.due_at.isoformat()}")
    lines.append(f"Task confidence: {ctx.task.confidence:.2f}")
    if options.include_review_metadata:
        if ctx.triage_summary:
            lines.append(f"Triage summary: {ctx.triage_summary.strip()[:600]}")
        if ctx.triage_reply_requirement is not None:
            lines.append(f"Reply requirement: {ctx.triage_reply_requirement.value}")
        if ctx.triage_confidence is not None:
            lines.append(f"Triage confidence: {ctx.triage_confidence:.2f}")

    description = "\n".join(lines)
    if len(description) > options.max_desc_chars:
        description = description[: options.max_desc_chars - 1] + "…"

    priority = triage_importance_to_priority(ctx.triage_importance)
    labels = ("source:mail-assistant", f"message:{ctx.task.message_id}")
    dedupe_marker = f"mail-assistant:task:{ctx.task.id}:v1"
    due_iso = ctx.task.due_at.isoformat() if ctx.task.due_at else None

    fingerprint = compute_card_fingerprint(
        internal_task_id=ctx.task.id,
        source_message_id=ctx.task.message_id,
        title=title,
        description=description,
        due_at_iso=due_iso,
        priority=priority,
        card_status=options.default_card_status,
        labels=labels,
        dedupe_marker=dedupe_marker,
    )

    return KanbanCardDraft(
        internal_task_id=ctx.task.id,
        source_message_id=ctx.task.message_id,
        title=title,
        description=description,
        due_at=ctx.task.due_at,
        priority=priority,
        card_status=options.default_card_status,
        labels=labels,
        dedupe_marker=dedupe_marker,
        fingerprint=fingerprint,
        assignee_external_id=options.assignee_external_id,
        placement_task_status=ctx.task.status,
    )

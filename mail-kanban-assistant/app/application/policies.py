from __future__ import annotations

from dataclasses import dataclass

from app.application.dtos import PersistedMessageDTO, TaskExtractionItemDTO, TriageLLMResponseDTO
from app.application.ports import KanbanPort
from app.domain.enums import MessageImportance
from app.domain.models import ExtractedTask, TriageResult


@dataclass(frozen=True, slots=True)
class TaskAutomationPolicy:
    confidence_threshold: float
    auto_create_kanban: bool


def triage_response_to_domain(dto: TriageLLMResponseDTO) -> TriageResult:
    return TriageResult(
        importance=dto.importance,
        reply_requirement=dto.reply_requirement,
        summary=dto.summary,
        actionable=dto.actionable,
        confidence=dto.confidence,
        reason_codes=dto.reason_codes,
    )


def should_extract_tasks(triage: TriageResult) -> bool:
    return triage.actionable is True


def confidence_allows_auto_kanban(confidence: float, policy: TaskAutomationPolicy) -> bool:
    return policy.auto_create_kanban and confidence >= policy.confidence_threshold


def maybe_sync_to_kanban(
    *,
    kanban: KanbanPort,
    task: ExtractedTask,
    message: PersistedMessageDTO,
    policy: TaskAutomationPolicy,
) -> str | None:
    """Human-in-the-loop default: only sync when explicitly enabled and confidence is high."""

    if not confidence_allows_auto_kanban(task.confidence, policy):
        return None
    return kanban.create_task_card(task, message)


def triage_is_incomplete(triage: TriageResult) -> bool:
    if not triage.summary.strip():
        return True
    if triage.confidence < 0.0 or triage.confidence > 1.0:
        return True
    return False


def should_enqueue_triage_review(triage: TriageResult, *, review_threshold: float) -> tuple[bool, str, str]:
    """Return (enqueue, reason_code, reason_text)."""

    if triage.confidence < review_threshold:
        return True, "low_confidence", f"Model confidence {triage.confidence:.2f} is below review threshold {review_threshold:.2f}"

    if triage_is_incomplete(triage):
        return True, "incomplete_triage", "Triage output looks incomplete or inconsistent (empty summary or invalid confidence)."

    if triage.importance in {MessageImportance.HIGH, MessageImportance.CRITICAL} and triage.confidence < 0.85:
        return (
            True,
            "high_impact_uncertain",
            "High/critical importance requires extra certainty; confidence is below the high-impact bar (0.85).",
        )

    return False, "", ""


def should_enqueue_task_review(item: TaskExtractionItemDTO, *, review_threshold: float) -> tuple[bool, str, str]:
    if item.confidence < review_threshold:
        return True, "low_task_confidence", f"Task confidence {item.confidence:.2f} is below review threshold {review_threshold:.2f}"

    if not item.title.strip():
        return True, "missing_title", "Extracted task title is empty."

    if item.description is None and len(item.title.strip()) < 4:
        return True, "thin_task", "Task looks too underspecified to trust without review."

    return False, "", ""


def can_auto_approve_task(item: TaskExtractionItemDTO, *, review_threshold: float) -> bool:
    """MVP: never auto-approve; all approvals go through explicit review or manual promotion."""

    _ = (item, review_threshold)
    return False

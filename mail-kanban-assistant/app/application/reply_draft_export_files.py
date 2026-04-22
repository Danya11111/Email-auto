from __future__ import annotations

import re
from pathlib import Path

from app.application.ports import ReplyDraftExporterPort
from app.domain.reply_draft import ReplyDraft


def _slug_subject(subject: str, *, max_len: int = 48) -> str:
    s = re.sub(r"[^\w\s-]", "", subject, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[-\s]+", "-", s)
    return (s[:max_len] or "reply").strip("-")


class LocalReplyDraftExporter(ReplyDraftExporterPort):
    """Writes local markdown / plain text artifacts (never sends mail)."""

    def export_markdown(self, *, draft: ReplyDraft, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Reply draft {draft.id}",
            "",
            f"**Thread:** `{draft.thread_id}`",
            f"**Status:** {draft.status.value}",
            f"**Tone:** {draft.tone.value}",
            "",
            "## Subject",
            draft.subject_suggestion,
            "",
            "## To review",
            draft.short_rationale,
            "",
            "### Missing information",
            "\n".join(f"- {x}" for x in draft.missing_information) or "- (none listed)",
            "",
            "## Body",
            draft.body_text,
            "",
            "## Fact boundary",
            draft.fact_boundary_note,
            "",
            "### Sources",
            f"- messages: {', '.join(f'm{x}' for x in draft.source_message_ids)}",
            f"- tasks: {', '.join(f't{x}' for x in draft.source_task_ids) or '(none)'}",
            f"- reviews: {', '.join(f'r{x}' for x in draft.source_review_ids) or '(none)'}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def export_plain_text(self, *, draft: ReplyDraft, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            f"Subject: {draft.subject_suggestion}\n\n"
            f"To review:\n{draft.short_rationale}\n\n"
            f"Missing information:\n"
            + ("\n".join(f"- {x}" for x in draft.missing_information) or "- (none)")
            + "\n\n"
            f"Body:\n{draft.body_text}\n\n"
            f"Fact boundary:\n{draft.fact_boundary_note}\n"
        )
        path.write_text(text, encoding="utf-8")
        return path


def default_export_path(*, export_dir: Path, draft: ReplyDraft, suffix: str) -> Path:
    slug = _slug_subject(draft.subject_suggestion)
    return export_dir / f"reply-draft-{draft.id}-{slug}.{suffix}"

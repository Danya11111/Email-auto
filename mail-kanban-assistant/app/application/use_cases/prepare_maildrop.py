from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.application.ports import MaildropFilesystemPort


@dataclass(frozen=True, slots=True)
class PrepareMaildropUseCase:
    fs: MaildropFilesystemPort

    def execute(self, maildrop_root: Path) -> str:
        self.fs.ensure_maildrop_layout(maildrop_root)
        root = maildrop_root.resolve()
        lines = [
            "Maildrop layout ready (idempotent).",
            f"Root: {root}",
            f"  incoming/  — drop JSON snapshots here (Apple Mail automation)",
            f"  processed/ — successfully ingested files are moved here",
            f"  failed/    — invalid snapshots land here",
            f"  exported/  — optional scratch area for manual exports",
            "",
            "Next steps:",
            "  1) Point Apple Mail rules / JXA helper at incoming/",
            "  2) Run: mail-assistant ingest-apple-mail-drop --path <maildrop_root>",
            "  3) Run triage / extract-tasks / run-daily as usual",
        ]
        return "\n".join(lines) + "\n"

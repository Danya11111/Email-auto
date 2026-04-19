from __future__ import annotations

from pathlib import Path
from typing import Sequence

from app.application.ports import AppleMailDropScannerPort


class AppleMailDropIncomingScanner(AppleMailDropScannerPort):
    """Filesystem-only discovery of JSON snapshot files in maildrop/incoming."""

    def list_incoming_json_paths(self, maildrop_root: Path) -> Sequence[Path]:
        incoming = maildrop_root / "incoming"
        if not incoming.exists():
            return ()
        paths = sorted(incoming.glob("*.json"), key=lambda p: p.name.lower())
        return tuple(p for p in paths if p.is_file())

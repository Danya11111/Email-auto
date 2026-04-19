from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.application.ports import LoggerPort, MaildropFilesystemPort


class OsMaildropFilesystem(MaildropFilesystemPort):
    def __init__(self, logger: LoggerPort) -> None:
        self._logger = logger

    def ensure_maildrop_layout(self, maildrop_root: Path) -> None:
        for name in ("incoming", "processed", "failed", "exported"):
            (maildrop_root / name).mkdir(parents=True, exist_ok=True)

    def move_to_processed(self, src: Path, maildrop_root: Path) -> Path:
        return self._move_under(src, maildrop_root / "processed")

    def move_to_failed(self, src: Path, maildrop_root: Path) -> Path:
        return self._move_under(src, maildrop_root / "failed")

    def _move_under(self, src: Path, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem}__{uuid.uuid4().hex}{src.suffix}"
        shutil.move(str(src), str(dest))
        self._logger.info("maildrop.file_moved", src=str(src), dest=str(dest))
        return dest

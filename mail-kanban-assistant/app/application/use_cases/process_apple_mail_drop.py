from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.application.apple_mail_snapshot import AppleMailDropSnapshotFile, snapshot_to_incoming_message_dto
from app.application.dtos import AppleMailDropIngestSummaryDTO
from app.application.ports import (
    AppleMailDropScannerPort,
    IngestedArtifactRepositoryPort,
    LoggerPort,
    MaildropFilesystemPort,
    MessageRepositoryPort,
)
from app.domain.enums import IngestedArtifactStatus, MessageProcessingStatus, MessageSource
from app.domain.errors import DuplicateMessageError
from app.utils.text import normalize_mail_body


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class ProcessAppleMailDropUseCase:
    messages: MessageRepositoryPort
    artifacts: IngestedArtifactRepositoryPort
    fs: MaildropFilesystemPort
    scanner: AppleMailDropScannerPort
    logger: LoggerPort

    def execute(self, *, maildrop_root: Path, run_id: str) -> AppleMailDropIngestSummaryDTO:
        started = time.perf_counter()
        self.fs.ensure_maildrop_layout(maildrop_root)
        paths = list(self.scanner.list_incoming_json_paths(maildrop_root))

        found = len(paths)
        ingested = 0
        duplicate = 0
        failed = 0
        moved_processed = 0
        moved_failed = 0

        self.logger.info("apple_mail_drop.start", run_id=run_id, found=found, maildrop_root=str(maildrop_root.resolve()))

        for path in paths:
            try:
                res = self._process_one_file(path=path, maildrop_root=maildrop_root, run_id=run_id)
                ingested += res["ingested"]
                duplicate += res["duplicate"]
                failed += res["failed"]
                moved_processed += res["moved_processed"]
                moved_failed += res["moved_failed"]
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.logger.error(
                    "apple_mail_drop.unexpected_file_error",
                    run_id=run_id,
                    path=str(path),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        duration_ms = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "apple_mail_drop.end",
            run_id=run_id,
            duration_ms=duration_ms,
            found=found,
            ingested=ingested,
            duplicate=duplicate,
            failed=failed,
            moved_processed=moved_processed,
            moved_failed=moved_failed,
        )

        return AppleMailDropIngestSummaryDTO(
            run_id=run_id,
            found=found,
            ingested=ingested,
            duplicate=duplicate,
            failed=failed,
            moved_processed=moved_processed,
            moved_failed=moved_failed,
        )

    def _process_one_file(self, *, path: Path, maildrop_root: Path, run_id: str) -> dict[str, int]:
        ingested = duplicate = failed = moved_processed = moved_failed = 0

        data = path.read_bytes()
        content_hash = _sha256_hex(data)
        existing = self.artifacts.maybe_find_artifact_by_hash_or_snapshot_id(content_hash=content_hash, snapshot_id=None)
        if existing is not None and existing.status == IngestedArtifactStatus.PROCESSED:
            duplicate += 1
            self.fs.move_to_processed(path, maildrop_root)
            moved_processed += 1
            self.logger.info(
                "apple_mail_drop.skip_duplicate_hash",
                run_id=run_id,
                path=str(path),
                artifact_id=existing.id,
            )
            return {
                "ingested": ingested,
                "duplicate": duplicate,
                "failed": failed,
                "moved_processed": moved_processed,
                "moved_failed": moved_failed,
            }

        artifact_id = self.artifacts.register_incoming_artifact(
            content_hash=content_hash,
            source_type=MessageSource.APPLE_MAIL_DROP.value,
            original_filename=path.name,
        )

        try:
            snapshot = AppleMailDropSnapshotFile.model_validate_json(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            self.artifacts.mark_artifact_failed(artifact_id=artifact_id, error_text=f"{type(exc).__name__}: {exc}")
            self.fs.move_to_failed(path, maildrop_root)
            failed += 1
            moved_failed += 1
            self.logger.warning(
                "apple_mail_drop.snapshot_invalid",
                run_id=run_id,
                path=str(path),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return {
                "ingested": ingested,
                "duplicate": duplicate,
                "failed": failed,
                "moved_processed": moved_processed,
                "moved_failed": moved_failed,
            }

        other = self.artifacts.find_artifact_with_snapshot_id(
            snapshot_id=snapshot.snapshot_id, exclude_artifact_id=artifact_id
        )
        if other is not None:
            if other.status == IngestedArtifactStatus.PROCESSED and other.related_message_id is not None:
                # Do not assign snapshot_id here: UNIQUE(snapshot_id) would collide with the first artifact.
                self.artifacts.mark_artifact_processed(
                    artifact_id=artifact_id, related_message_id=other.related_message_id
                )
                duplicate += 1
                self.fs.move_to_processed(path, maildrop_root)
                moved_processed += 1
                self.logger.info(
                    "apple_mail_drop.skip_duplicate_snapshot_id",
                    run_id=run_id,
                    path=str(path),
                    snapshot_id=snapshot.snapshot_id,
                )
                return {
                    "ingested": ingested,
                    "duplicate": duplicate,
                    "failed": failed,
                    "moved_processed": moved_processed,
                    "moved_failed": moved_failed,
                }
            self.artifacts.mark_artifact_failed(
                artifact_id=artifact_id,
                error_text="snapshot_id conflict with another pending artifact",
            )
            self.fs.move_to_failed(path, maildrop_root)
            failed += 1
            moved_failed += 1
            return {
                "ingested": ingested,
                "duplicate": duplicate,
                "failed": failed,
                "moved_processed": moved_processed,
                "moved_failed": moved_failed,
            }

        incoming = snapshot_to_incoming_message_dto(snapshot=snapshot, source_path=str(path.resolve()))
        normalized = normalize_mail_body(incoming.body_plain)

        try:
            self.artifacts.set_snapshot_id(artifact_id, snapshot.snapshot_id)
            message_id = self.messages.insert_message(
                incoming,
                body_normalized=normalized,
                processing_status=MessageProcessingStatus.INGESTED,
            )
            ingested += 1
        except DuplicateMessageError:
            existing_id = self.messages.find_message_id_by_dedupe_key(incoming.dedupe_key)
            if existing_id is None:
                self.artifacts.mark_artifact_failed(
                    artifact_id=artifact_id,
                    error_text="DuplicateMessageError but message row not found by dedupe_key",
                )
                self.fs.move_to_failed(path, maildrop_root)
                failed += 1
                moved_failed += 1
                return {
                    "ingested": ingested,
                    "duplicate": duplicate,
                    "failed": failed,
                    "moved_processed": moved_processed,
                    "moved_failed": moved_failed,
                }
            message_id = existing_id
            duplicate += 1
        except Exception as exc:  # noqa: BLE001
            self.artifacts.mark_artifact_failed(artifact_id=artifact_id, error_text=f"{type(exc).__name__}: {exc}")
            self.fs.move_to_failed(path, maildrop_root)
            failed += 1
            moved_failed += 1
            self.logger.error(
                "apple_mail_drop.insert_failed",
                run_id=run_id,
                path=str(path),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return {
                "ingested": ingested,
                "duplicate": duplicate,
                "failed": failed,
                "moved_processed": moved_processed,
                "moved_failed": moved_failed,
            }

        self.artifacts.mark_artifact_processed(artifact_id=artifact_id, related_message_id=message_id)
        self.fs.move_to_processed(path, maildrop_root)
        moved_processed += 1

        return {
            "ingested": ingested,
            "duplicate": duplicate,
            "failed": failed,
            "moved_processed": moved_processed,
            "moved_failed": moved_failed,
        }

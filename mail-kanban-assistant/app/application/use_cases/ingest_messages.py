from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.application.dtos import IngestResultDTO
from app.application.ports import LoggerPort, MessageReaderPort, MessageRepositoryPort, PipelineRunRepositoryPort
from app.domain.enums import MessageProcessingStatus
from app.domain.errors import DuplicateMessageError
from app.utils.text import normalize_mail_body


@dataclass(frozen=True, slots=True)
class IngestMessagesUseCase:
    messages: MessageRepositoryPort
    pipeline_runs: PipelineRunRepositoryPort
    logger: LoggerPort

    def execute(
        self,
        reader: MessageReaderPort,
        *,
        run_id: str,
        command: str,
        record_pipeline: bool = True,
    ) -> IngestResultDTO:
        started = time.perf_counter()
        self.logger.info("ingest.start", run_id=run_id, command=command)

        pipeline_db_id = self.pipeline_runs.start_run(run_id=run_id, command=command) if record_pipeline else None
        inserted = 0
        duplicates = 0
        failures = 0

        try:
            batch = list(reader.read_messages())
            for item in batch:
                normalized = normalize_mail_body(item.body_plain)
                try:
                    self.messages.insert_message(
                        item,
                        body_normalized=normalized,
                        processing_status=MessageProcessingStatus.INGESTED,
                    )
                    inserted += 1
                except DuplicateMessageError:
                    duplicates += 1
                except Exception as exc:  # noqa: BLE001 - boundary: log and continue
                    failures += 1
                    self.logger.error(
                        "ingest.message_failed",
                        run_id=run_id,
                        dedupe_key=item.dedupe_key,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )

            duration_ms = int((time.perf_counter() - started) * 1000)
            self.logger.info(
                "ingest.end",
                run_id=run_id,
                duration_ms=duration_ms,
                inserted=inserted,
                duplicates=duplicates,
                failures=failures,
                items=len(batch),
            )
            if pipeline_db_id is not None:
                self.pipeline_runs.finish_run(
                    pipeline_db_id,
                    status="ok",
                    metadata=json.dumps(
                        {"inserted": inserted, "duplicates": duplicates, "failures": failures, "items": len(batch)}
                    ),
                )
            return IngestResultDTO(run_id=run_id, inserted=inserted, duplicates=duplicates, failures=failures)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.logger.error(
                "ingest.failed",
                run_id=run_id,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if pipeline_db_id is not None:
                self.pipeline_runs.finish_run(pipeline_db_id, status="error", metadata=json.dumps({"error": str(exc)}))
            raise

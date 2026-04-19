from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Sequence, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.application.dtos import DigestLLMResponseDTO, PersistedMessageDTO, TaskExtractionItemDTO, TriageLLMResponseDTO
from app.application.llm_input import LlmTextPolicy, prepare_body_for_llm
from app.application.ports import DigestLLMPort, LoggerPort, TaskExtractionLLMPort, TriageLLMPort
from app.infrastructure.llm import prompts
from app.infrastructure.llm.schemas import DigestStructuredResponse, TaskExtractionStructuredResponse

T = TypeVar("T", bound=BaseModel)


class LlmTransportError(RuntimeError):
    ...


class LlmResponseValidationError(RuntimeError):
    ...


class LmStudioStructuredClient(TriageLLMPort, TaskExtractionLLMPort, DigestLLMPort):
    """OpenAI-compatible LM Studio gateway with structured JSON validation."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        max_output_tokens: int,
        llm_text_policy: LlmTextPolicy,
        logger: LoggerPort,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._max_output_tokens = int(max_output_tokens)
        self._llm_text_policy = llm_text_policy
        self._logger = logger
        self._http = httpx.Client(base_url=self._base_url, timeout=httpx.Timeout(self._timeout))

    def close(self) -> None:
        self._http.close()

    def triage_message(self, message: PersistedMessageDTO) -> TriageLLMResponseDTO:
        body = prepare_body_for_llm(message.body_plain, self._llm_text_policy)
        user = prompts.triage_user_prompt(message.subject, message.sender, body_excerpt=body)
        return self._complete_and_validate(
            schema_name="triage",
            system=prompts.TRIAGE_SYSTEM,
            user=user,
            response_model=TriageLLMResponseDTO,
        )

    def extract_tasks(self, message: PersistedMessageDTO, triage_summary: str) -> Sequence[TaskExtractionItemDTO]:
        body = prepare_body_for_llm(message.body_plain, self._llm_text_policy)
        user = prompts.task_extraction_user_prompt(
            message.subject,
            message.sender,
            triage_summary=triage_summary,
            body_excerpt=body,
        )
        parsed = self._complete_and_validate(
            schema_name="task_extraction",
            system=prompts.TASK_EXTRACTION_SYSTEM,
            user=user,
            response_model=TaskExtractionStructuredResponse,
        )
        return tuple(parsed.tasks)

    def build_digest_markdown(self, window_start: datetime, window_end: datetime, payload_json: str) -> DigestLLMResponseDTO:
        _ = (window_start, window_end)
        # Kept for compatibility; daily digest is primarily assembled deterministically in application code.
        user = prompts.digest_user_prompt(payload_json)
        parsed = self._complete_and_validate(
            schema_name="morning_digest",
            system=prompts.DIGEST_SYSTEM,
            user=user,
            response_model=DigestStructuredResponse,
        )
        return DigestLLMResponseDTO(markdown=parsed.markdown)

    def _complete_and_validate(
        self,
        *,
        schema_name: str,
        system: str,
        user: str,
        response_model: type[T],
    ) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            started = time.perf_counter()
            try:
                content = self._chat_completion_content(
                    schema_name=schema_name,
                    system=system,
                    user=user,
                    response_model=response_model,
                    attempt=attempt,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                self._logger.info(
                    "llm.call.ok",
                    schema=schema_name,
                    attempt=attempt,
                    latency_ms=latency_ms,
                    model=self._model,
                )
                return response_model.model_validate_json(content)
            except (httpx.HTTPError, ValidationError, LlmTransportError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                latency_ms = int((time.perf_counter() - started) * 1000)
                self._logger.warning(
                    "llm.call.retry",
                    schema=schema_name,
                    attempt=attempt,
                    latency_ms=latency_ms,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                time.sleep(min(2 ** (attempt - 1), 8))

        assert last_error is not None
        raise LlmResponseValidationError(f"LLM failed after {self._max_retries} attempts: {last_error}") from last_error

    def _chat_completion_content(
        self,
        *,
        schema_name: str,
        system: str,
        user: str,
        response_model: type[BaseModel],
        attempt: int,
    ) -> str:
        json_schema = {
            "name": schema_name,
            "schema": response_model.model_json_schema(),
            "strict": False,
        }

        body_primary: dict[str, Any] = {
            "model": self._model,
            "temperature": 0.2,
            "max_tokens": self._max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_schema", "json_schema": json_schema},
        }

        try:
            return self._post_chat_completions(body_primary, attempt=attempt, mode="json_schema")
        except LlmTransportError:
            body_fallback: dict[str, Any] = {
                "model": self._model,
                "temperature": 0.2,
                "max_tokens": self._max_output_tokens,
                "messages": [
                    {
                        "role": "system",
                        "content": system
                        + "\n\nJSON schema (validate your output against it): "
                        + json.dumps(json_schema["schema"], ensure_ascii=False),
                    },
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            }
            return self._post_chat_completions(body_fallback, attempt=attempt, mode="json_object")

    def _post_chat_completions(self, body: dict[str, Any], *, attempt: int, mode: str) -> str:
        try:
            resp = self._http.post("/chat/completions", json=body)
            if resp.status_code >= 400:
                raise LlmTransportError(f"HTTP {resp.status_code}: {resp.text[:2000]}")
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LlmTransportError("empty model content")
            return content
        except httpx.HTTPError as exc:
            self._logger.warning(
                "llm.http_error",
                mode=mode,
                attempt=attempt,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise LlmTransportError(str(exc)) from exc

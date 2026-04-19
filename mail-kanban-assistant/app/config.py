from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.enums import MessageBodyTruncateStrategy


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", validation_alias="APP_ENV")

    database_path: Path = Field(default=Path("./data/mail_assistant.sqlite3"), validation_alias="DATABASE_PATH")
    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")

    lm_studio_base_url: str = Field(default="http://localhost:1234/v1", validation_alias="LM_STUDIO_BASE_URL")
    lm_studio_model: str = Field(default="qwen3-8b", validation_alias="LM_STUDIO_MODEL")
    lm_timeout_seconds: float = Field(default=120.0, validation_alias="LM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=3, validation_alias="LLM_MAX_RETRIES")
    llm_max_input_chars: int = Field(default=7000, validation_alias="LLM_MAX_INPUT_CHARS")
    llm_max_output_tokens: int = Field(default=512, validation_alias="LLM_MAX_OUTPUT_TOKENS")
    llm_concurrency: int = Field(default=1, validation_alias="LLM_CONCURRENCY")

    digest_lookback_hours: int = Field(default=24, validation_alias="DIGEST_LOOKBACK_HOURS")
    digest_max_messages: int = Field(default=30, validation_alias="DIGEST_MAX_MESSAGES")

    triage_batch_size: int = Field(default=10, validation_alias="TRIAGE_BATCH_SIZE")
    task_extraction_batch_size: int = Field(default=10, validation_alias="TASK_EXTRACTION_BATCH_SIZE")

    task_confidence_threshold: float = Field(default=0.75, validation_alias="TASK_CONFIDENCE_THRESHOLD")
    review_confidence_threshold: float = Field(default=0.72, validation_alias="REVIEW_CONFIDENCE_THRESHOLD")
    auto_create_kanban_tasks: bool = Field(default=False, validation_alias="AUTO_CREATE_KANBAN_TASKS")

    message_body_truncate_strategy: MessageBodyTruncateStrategy = Field(
        default=MessageBodyTruncateStrategy.HEAD_TAIL,
        validation_alias="MESSAGE_BODY_TRUNCATE_STRATEGY",
    )

    mail_eml_dir: Path | None = Field(default=None, validation_alias="MAIL_EML_DIR")
    mail_mbox_path: Path | None = Field(default=None, validation_alias="MAIL_MBOX_PATH")

    maildrop_root: Path = Field(
        default=Path("./data/maildrop"),
        validation_alias="MAILDROP_ROOT",
        description="Root for Apple Mail JSON snapshot drop workflow (incoming/processed/failed).",
    )

    launchd_label: str = Field(default="com.local.mailassistant", validation_alias="LAUNCHD_LABEL")

    @field_validator("mail_eml_dir", "mail_mbox_path", mode="before")
    @classmethod
    def _empty_paths_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("maildrop_root", mode="before")
    @classmethod
    def _strip_maildrop_root(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return Path("./data/maildrop")
        return value

    @field_validator("message_body_truncate_strategy", mode="before")
    @classmethod
    def _normalize_truncate_strategy(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("llm_concurrency")
    @classmethod
    def _cap_concurrency(cls, value: int) -> int:
        # MVP: sequential processing only (safe on 8GB unified memory machines).
        return 1 if int(value) < 1 else 1

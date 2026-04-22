from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.enums import KanbanCardStatus, KanbanProvider, MessageBodyTruncateStrategy


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

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

    action_center_lookback_hours: int = Field(default=72, validation_alias="ACTION_CENTER_LOOKBACK_HOURS")
    action_center_max_items: int = Field(default=40, validation_alias="ACTION_CENTER_MAX_ITEMS")
    action_center_max_messages: int = Field(default=300, validation_alias="ACTION_CENTER_MAX_MESSAGES")
    action_center_include_informational: bool = Field(default=False, validation_alias="ACTION_CENTER_INCLUDE_INFORMATIONAL")
    reply_overdue_hours: int = Field(default=48, validation_alias="REPLY_OVERDUE_HOURS")
    reply_recommended_hours: int = Field(default=24, validation_alias="REPLY_RECOMMENDED_HOURS")
    thread_grouping_time_window_hours: int = Field(default=96, validation_alias="THREAD_GROUPING_TIME_WINDOW_HOURS")
    action_center_use_llm_executive_summary: bool = Field(
        default=False,
        validation_alias="ACTION_CENTER_USE_LLM_EXECUTIVE_SUMMARY",
        description="Reserved: deterministic executive summary is default (low-memory).",
    )
    action_center_executive_summary_max_items: int = Field(default=4, validation_alias="ACTION_CENTER_EXECUTIVE_SUMMARY_MAX_ITEMS")
    action_center_require_review_for_ambiguous_reply: bool = Field(
        default=True,
        validation_alias="ACTION_CENTER_REQUIRE_REVIEW_FOR_AMBIGUOUS_REPLY",
        description="When true, pending reviews force ReplyState.AMBIGUOUS for the thread.",
    )

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

    kanban_provider: KanbanProvider = Field(default=KanbanProvider.LOCAL_FILE, validation_alias="KANBAN_PROVIDER")
    kanban_auto_sync: bool = Field(default=False, validation_alias="KANBAN_AUTO_SYNC")
    kanban_root_dir: Path = Field(default=Path("./data/kanban/local_board"), validation_alias="KANBAN_ROOT_DIR")
    kanban_default_status: KanbanCardStatus = Field(default=KanbanCardStatus.TODO, validation_alias="KANBAN_DEFAULT_STATUS")
    kanban_sync_batch_size: int = Field(default=50, validation_alias="KANBAN_SYNC_BATCH_SIZE")
    kanban_retry_limit: int = Field(default=5, validation_alias="KANBAN_RETRY_LIMIT")
    kanban_include_review_metadata: bool = Field(default=True, validation_alias="KANBAN_INCLUDE_REVIEW_METADATA")
    kanban_include_message_metadata: bool = Field(default=True, validation_alias="KANBAN_INCLUDE_MESSAGE_METADATA")
    kanban_max_title_chars: int = Field(default=120, validation_alias="KANBAN_MAX_TITLE_CHARS")
    kanban_max_desc_chars: int = Field(default=4000, validation_alias="KANBAN_MAX_DESC_CHARS")
    kanban_http_timeout_seconds: float = Field(default=25.0, validation_alias="KANBAN_HTTP_TIMEOUT_SECONDS")

    trello_api_key: str = Field(default="", validation_alias="TRELLO_API_KEY")
    trello_token: str = Field(default="", validation_alias="TRELLO_TOKEN")
    trello_board_id: str = Field(default="", validation_alias="TRELLO_BOARD_ID")
    trello_list_id_todo: str = Field(default="", validation_alias="TRELLO_LIST_ID_TODO")
    trello_list_id_done: str = Field(default="", validation_alias="TRELLO_LIST_ID_DONE")
    trello_list_id_blocked: str = Field(default="", validation_alias="TRELLO_LIST_ID_BLOCKED")

    yougile_base_url: str = Field(default="https://ru.yougile.com", validation_alias="YOUGILE_BASE_URL")
    yougile_api_key: str = Field(default="", validation_alias="YOUGILE_API_KEY")
    yougile_company_id: str = Field(default="", validation_alias="YOUGILE_COMPANY_ID")
    yougile_board_id: str = Field(
        default="",
        validation_alias="YOUGILE_BOARD_ID",
        description="Board id from YouGile; .env.example documents 92f15y2g9ymf on ru.yougile.com for this workspace.",
    )
    yougile_column_id_todo: str = Field(default="", validation_alias="YOUGILE_COLUMN_ID_TODO")
    yougile_column_id_done: str = Field(default="", validation_alias="YOUGILE_COLUMN_ID_DONE")
    yougile_column_id_blocked: str = Field(default="", validation_alias="YOUGILE_COLUMN_ID_BLOCKED")
    yougile_request_timeout_seconds: float = Field(default=25.0, validation_alias="YOUGILE_REQUEST_TIMEOUT_SECONDS")
    yougile_requests_per_minute: int = Field(default=40, validation_alias="YOUGILE_REQUESTS_PER_MINUTE")
    yougile_enable_update_existing: bool = Field(default=False, validation_alias="YOUGILE_ENABLE_UPDATE_EXISTING")
    yougile_include_internal_ids: bool = Field(default=True, validation_alias="YOUGILE_INCLUDE_INTERNAL_IDS")
    yougile_attach_source_metadata: bool = Field(default=True, validation_alias="YOUGILE_ATTACH_SOURCE_METADATA")
    yougile_max_description_chars: int = Field(default=12000, validation_alias="YOUGILE_MAX_DESCRIPTION_CHARS")
    yougile_priority_sticker_name: str = Field(default="", validation_alias="YOUGILE_PRIORITY_STICKER_NAME")
    yougile_priority_state_low: str = Field(default="", validation_alias="YOUGILE_PRIORITY_STATE_LOW")
    yougile_priority_state_medium: str = Field(default="", validation_alias="YOUGILE_PRIORITY_STATE_MEDIUM")
    yougile_priority_state_high: str = Field(default="", validation_alias="YOUGILE_PRIORITY_STATE_HIGH")
    yougile_priority_state_critical: str = Field(default="", validation_alias="YOUGILE_PRIORITY_STATE_CRITICAL")
    yougile_default_assignee_external_id: str = Field(
        default="",
        validation_alias="YOUGILE_DEFAULT_ASSIGNEE_EXTERNAL_ID",
        description="Scaffold: future YouGile assignee user id; not sent on API in MVP.",
    )

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

    @field_validator("kanban_provider", mode="before")
    @classmethod
    def _normalize_kanban_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("kanban_default_status", mode="before")
    @classmethod
    def _normalize_kanban_default_status(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("yougile_base_url", mode="before")
    @classmethod
    def _strip_yougile_base(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            return "https://ru.yougile.com"
        return value.strip().rstrip("/")

    @field_validator("yougile_requests_per_minute")
    @classmethod
    def _clamp_yougile_rpm(cls, value: int) -> int:
        v = int(value)
        if v < 1:
            return 1
        if v > 50:
            return 50
        return v

    @field_validator(
        "yougile_api_key",
        "yougile_company_id",
        "yougile_board_id",
        "yougile_column_id_todo",
        "yougile_column_id_done",
        "yougile_column_id_blocked",
        "yougile_priority_sticker_name",
        "yougile_priority_state_low",
        "yougile_priority_state_medium",
        "yougile_priority_state_high",
        "yougile_priority_state_critical",
        "yougile_default_assignee_external_id",
        mode="before",
    )
    @classmethod
    def _strip_optional_yougile_strings(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("yougile_enable_update_existing", "yougile_include_internal_ids", "yougile_attach_source_metadata", mode="before")
    @classmethod
    def _bool_yougile_flags(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return value

    @field_validator("llm_concurrency")
    @classmethod
    def _cap_concurrency(cls, value: int) -> int:
        # MVP: sequential processing only (safe on 8GB unified memory machines).
        return 1 if int(value) < 1 else 1

    @field_validator(
        "action_center_lookback_hours",
        "action_center_max_items",
        "action_center_max_messages",
        "reply_overdue_hours",
        "reply_recommended_hours",
        "thread_grouping_time_window_hours",
        "action_center_executive_summary_max_items",
    )
    @classmethod
    def _positive_int_action_center(cls, value: int) -> int:
        v = int(value)
        if v < 1:
            return 1
        return v

    @field_validator(
        "action_center_include_informational",
        "action_center_use_llm_executive_summary",
        "action_center_require_review_for_ambiguous_reply",
        mode="before",
    )
    @classmethod
    def _bool_action_center_flags(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return value

    reply_draft_max_context_messages: int = Field(default=4, validation_alias="REPLY_DRAFT_MAX_CONTEXT_MESSAGES")
    reply_draft_max_input_chars: int = Field(default=4500, validation_alias="REPLY_DRAFT_MAX_INPUT_CHARS")
    reply_draft_default_tone: str = Field(default="neutral", validation_alias="REPLY_DRAFT_DEFAULT_TONE")
    reply_draft_allow_force_on_no_reply_needed: bool = Field(
        default=False,
        validation_alias="REPLY_DRAFT_ALLOW_FORCE_ON_NO_REPLY_NEEDED",
    )
    reply_draft_export_dir: Path = Field(default=Path("./data/reply_drafts_export"), validation_alias="REPLY_DRAFT_EXPORT_DIR")
    reply_draft_mark_stale_on_thread_change: bool = Field(
        default=True,
        validation_alias="REPLY_DRAFT_MARK_STALE_ON_THREAD_CHANGE",
    )
    reply_draft_require_approval_before_export: bool = Field(
        default=True,
        validation_alias="REPLY_DRAFT_REQUIRE_APPROVAL_BEFORE_EXPORT",
        description="When true, export is blocked until approve (safe human-in-the-loop default).",
    )
    reply_draft_include_action_center_reason: bool = Field(
        default=True,
        validation_alias="REPLY_DRAFT_INCLUDE_ACTION_CENTER_REASON",
    )
    reply_draft_include_tasks: bool = Field(default=True, validation_alias="REPLY_DRAFT_INCLUDE_TASKS")
    reply_draft_include_review_notes: bool = Field(default=True, validation_alias="REPLY_DRAFT_INCLUDE_REVIEW_NOTES")

    @field_validator("reply_draft_max_context_messages")
    @classmethod
    def _reply_draft_ctx_msgs(cls, value: int) -> int:
        v = int(value)
        return 1 if v < 1 else (12 if v > 12 else v)

    @field_validator("reply_draft_max_input_chars")
    @classmethod
    def _reply_draft_input_chars(cls, value: int) -> int:
        v = int(value)
        return 1500 if v < 1500 else (20000 if v > 20000 else v)

    @field_validator("reply_draft_default_tone", mode="before")
    @classmethod
    def _reply_draft_tone(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "neutral"
        return value

    @field_validator(
        "reply_draft_allow_force_on_no_reply_needed",
        "reply_draft_mark_stale_on_thread_change",
        "reply_draft_require_approval_before_export",
        "reply_draft_include_action_center_reason",
        "reply_draft_include_tasks",
        "reply_draft_include_review_notes",
        mode="before",
    )
    @classmethod
    def _bool_reply_draft_flags(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return value

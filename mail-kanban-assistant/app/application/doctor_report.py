from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from app.application.lm_studio_probe import lm_studio_models_probe_url
from app.application.ports import HttpProbePort, KanbanPort
from app.config import AppSettings
from app.domain.enums import KanbanProvider


@dataclass(frozen=True, slots=True)
class DoctorLineDTO:
    level: str  # OK | WARN | FAIL
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReportDTO:
    lines: tuple[DoctorLineDTO, ...]

    def render_text(self) -> str:
        out: list[str] = ["Mail Kanban Assistant — environment doctor", ""]
        for line in self.lines:
            out.append(f"[{line.level}] {line.message}")
        out.append("")
        return "\n".join(out)

    def render_json(self) -> str:
        payload = {"lines": [{"level": line.level, "message": line.message} for line in self.lines]}
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True, slots=True)
class DoctorEnvironmentUseCase:
    http: HttpProbePort

    def execute(
        self,
        settings: AppSettings,
        *,
        repo_root: Path,
        wrapper_script: Path | None,
        kanban_port: KanbanPort | None = None,
    ) -> DoctorReportDTO:
        lines: list[DoctorLineDTO] = []

        db_path = settings.database_path
        if db_path.parent.exists() and db_path.parent.is_dir():
            lines.append(DoctorLineDTO("OK", f"SQLite parent directory exists: {db_path.parent.resolve()}"))
        else:
            lines.append(DoctorLineDTO("WARN", f"SQLite parent missing (init-db will create): {db_path.parent.resolve()}"))

        if db_path.exists():
            lines.append(DoctorLineDTO("OK", f"SQLite database file exists: {db_path.resolve()}"))
        else:
            lines.append(DoctorLineDTO("WARN", f"SQLite database not found yet: {db_path.resolve()} (run init-db)"))

        drop = settings.maildrop_root
        if drop.exists() and drop.is_dir():
            lines.append(DoctorLineDTO("OK", f"MAILDROP_ROOT exists: {drop.resolve()}"))
        else:
            lines.append(
                DoctorLineDTO(
                    "WARN",
                    f"MAILDROP_ROOT not found: {drop.resolve()} (run mail-assistant prepare-maildrop --path ...)",
                )
            )

        if drop.exists():
            for sub in ("incoming", "processed", "failed", "exported"):
                p = drop / sub
                if p.is_dir():
                    lines.append(DoctorLineDTO("OK", f"Maildrop subdirectory exists: {p}"))
                else:
                    lines.append(DoctorLineDTO("FAIL", f"Maildrop subdirectory missing: {p}"))

        model = (settings.lm_studio_model or "").strip()
        if model:
            lines.append(DoctorLineDTO("OK", f"LM_STUDIO_MODEL is set: {model!r}"))
        else:
            lines.append(DoctorLineDTO("WARN", "LM_STUDIO_MODEL is empty"))

        probe_url = lm_studio_models_probe_url(settings.lm_studio_base_url)
        status = self.http.get_status(probe_url, timeout_seconds=3.0)
        if status is None:
            lines.append(
                DoctorLineDTO(
                    "WARN",
                    f"LM Studio probe unreachable ({probe_url}); server may be stopped (best-effort check).",
                )
            )
        elif 200 <= status < 300:
            lines.append(DoctorLineDTO("OK", f"LM Studio HTTP reachable: {probe_url} (status {status})"))
        else:
            lines.append(DoctorLineDTO("WARN", f"LM Studio HTTP non-2xx: {probe_url} (status {status})"))

        if wrapper_script is not None:
            if wrapper_script.is_file():
                lines.append(DoctorLineDTO("OK", f"launchd wrapper script exists: {wrapper_script.resolve()}"))
            else:
                lines.append(
                    DoctorLineDTO(
                        "WARN",
                        f"Expected launchd wrapper not found: {wrapper_script} (print-launchd can still render a template).",
                    )
                )

        plist_hint = repo_root / "app" / "scheduler" / "launchd" / "com.local.mailassistant.plist.example"
        if plist_hint.is_file():
            lines.append(DoctorLineDTO("OK", f"Example plist present: {plist_hint}"))
        else:
            lines.append(DoctorLineDTO("WARN", f"Example plist missing: {plist_hint}"))

        lines.append(DoctorLineDTO("OK", f"Kanban provider: {settings.kanban_provider.value}"))
        lines.append(DoctorLineDTO("OK", f"Kanban auto-sync: {'enabled' if settings.kanban_auto_sync else 'disabled'}"))
        if settings.kanban_provider == KanbanProvider.LOCAL_FILE:
            kr = settings.kanban_root_dir.resolve()
            try:
                kr.mkdir(parents=True, exist_ok=True)
                probe = kr / ".doctor_probe"
                probe.write_text("ok", encoding="utf-8")
                lines.append(DoctorLineDTO("OK", f"Kanban local root writable: {kr}"))
            except OSError as exc:
                lines.append(DoctorLineDTO("FAIL", f"Kanban local root not writable ({kr}): {exc}"))
        elif settings.kanban_provider == KanbanProvider.TRELLO:
            missing: list[str] = []
            if not (settings.trello_api_key or "").strip():
                missing.append("TRELLO_API_KEY")
            if not (settings.trello_token or "").strip():
                missing.append("TRELLO_TOKEN")
            if not (settings.trello_list_id_todo or "").strip():
                missing.append("TRELLO_LIST_ID_TODO")
            if missing:
                lines.append(
                    DoctorLineDTO(
                        "FAIL",
                        f"Trello provider selected but missing env/settings: {', '.join(missing)}",
                    )
                )
            else:
                lines.append(DoctorLineDTO("OK", "Trello mandatory settings appear present (not validating network here)."))
        elif settings.kanban_provider == KanbanProvider.YOUGILE:
            missing: list[str] = []
            if not (settings.yougile_api_key or "").strip():
                missing.append("YOUGILE_API_KEY")
            if not (settings.yougile_column_id_todo or "").strip():
                missing.append("YOUGILE_COLUMN_ID_TODO")
            if missing:
                lines.append(
                    DoctorLineDTO(
                        "FAIL",
                        f"YouGile provider selected but missing env/settings: {', '.join(missing)}",
                    )
                )
            else:
                lines.append(DoctorLineDTO("OK", "YouGile mandatory settings appear present (API key + TODO column)."))
            if not (settings.yougile_board_id or "").strip():
                lines.append(DoctorLineDTO("WARN", "YOUGILE_BOARD_ID empty — adapter healthcheck may fail."))
            if not (settings.yougile_column_id_done or "").strip():
                lines.append(
                    DoctorLineDTO(
                        "WARN",
                        "YOUGILE_COLUMN_ID_DONE empty — synced/done-like tasks fall back to TODO column (see column policy warnings in logs).",
                    )
                )
            if not (settings.yougile_column_id_blocked or "").strip():
                lines.append(
                    DoctorLineDTO(
                        "WARN",
                        "YOUGILE_COLUMN_ID_BLOCKED empty — blocked/rejected mapping falls back to TODO column.",
                    )
                )
            if not settings.yougile_enable_update_existing:
                lines.append(
                    DoctorLineDTO(
                        "OK",
                        "YOUGILE_ENABLE_UPDATE_EXISTING=false (safe default: no silent remote edits after fingerprint changes).",
                    )
                )
            else:
                lines.append(
                    DoctorLineDTO(
                        "WARN",
                        "YOUGILE_ENABLE_UPDATE_EXISTING=true — remote cards receive safe field updates when local fingerprint drifts.",
                    )
                )
            rl = int(settings.kanban_retry_limit)
            if rl < 3:
                lines.append(
                    DoctorLineDTO("WARN", f"KANBAN_RETRY_LIMIT={rl} is low; transient API errors may exhaust retries quickly.")
                )
            if rl > 30:
                lines.append(
                    DoctorLineDTO("WARN", f"KANBAN_RETRY_LIMIT={rl} is high; watch backlog size vs provider rate limits.")
                )
            lines.append(
                DoctorLineDTO(
                    "OK",
                    f"YouGile rate target: {int(settings.yougile_requests_per_minute)} req/min (API limit 50/company).",
                )
            )
        elif settings.kanban_provider == KanbanProvider.STUB:
            lines.append(DoctorLineDTO("WARN", "Kanban provider is stub — no external/local cards will be created."))

        lines.append(DoctorLineDTO("OK", f"Action center lookback: {int(settings.action_center_lookback_hours)}h"))
        lines.append(DoctorLineDTO("OK", f"Action center max items: {int(settings.action_center_max_items)}"))
        lines.append(DoctorLineDTO("OK", f"Action center max message rows: {int(settings.action_center_max_messages)}"))
        lines.append(DoctorLineDTO("OK", f"Thread grouping time window: {int(settings.thread_grouping_time_window_hours)}h"))
        lines.append(DoctorLineDTO("OK", f"Reply overdue threshold: {int(settings.reply_overdue_hours)}h"))
        lines.append(DoctorLineDTO("OK", f"Reply recommended window: {int(settings.reply_recommended_hours)}h"))
        lines.append(
            DoctorLineDTO(
                "OK" if not settings.action_center_use_llm_executive_summary else "WARN",
                "ACTION_CENTER_USE_LLM_EXECUTIVE_SUMMARY: "
                + ("off (default deterministic summary)" if not settings.action_center_use_llm_executive_summary else "on (reserved; still deterministic today)"),
            )
        )
        lines.append(
            DoctorLineDTO(
                "OK",
                "ACTION_CENTER_REQUIRE_REVIEW_FOR_AMBIGUOUS_REPLY: "
                + ("on" if settings.action_center_require_review_for_ambiguous_reply else "off"),
            )
        )

        if kanban_port is not None:
            try:
                ok = kanban_port.healthcheck()
                lines.append(
                    DoctorLineDTO("OK" if ok else "WARN", f"Kanban adapter healthcheck: {'ok' if ok else 'failed'}")
                )
            except Exception as exc:  # noqa: BLE001
                lines.append(DoctorLineDTO("WARN", f"Kanban adapter healthcheck raised: {type(exc).__name__}: {exc}"))

        return DoctorReportDTO(lines=tuple(lines))

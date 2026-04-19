from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from app.application.ports import HttpProbePort
from app.config import AppSettings


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


@dataclass(frozen=True, slots=True)
class DoctorEnvironmentUseCase:
    http: HttpProbePort

    def execute(
        self,
        settings: AppSettings,
        *,
        repo_root: Path,
        wrapper_script: Path | None,
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

        base = settings.lm_studio_base_url.rstrip("/") + "/"
        probe_url = urljoin(base, "models")
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

        return DoctorReportDTO(lines=tuple(lines))

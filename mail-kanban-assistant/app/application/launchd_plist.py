from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LaunchdPlistSpecDTO:
    label: str
    wrapper_script: Path
    working_directory: Path
    digest_out: Path
    stdout_path: Path
    stderr_path: Path
    hour: int
    minute: int
    maildrop_root: Path | None = None
    run_log_path: Path | None = None


def render_launchd_plist_xml(spec: LaunchdPlistSpecDTO) -> str:
    """Build a LaunchAgent plist using absolute paths (no shell dependency in ProgramArguments)."""

    wrapper = str(spec.wrapper_script.resolve())
    wd = str(spec.working_directory.resolve())
    digest = str(spec.digest_out.resolve())
    out_log = str(spec.stdout_path.resolve())
    err_log = str(spec.stderr_path.resolve())

    env: dict[str, str] = {
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "MAIL_KANBAN_REPO_ROOT": wd,
        "MAIL_KANBAN_DIGEST_OUT": digest,
    }
    if spec.maildrop_root is not None:
        env["MAILDROP_ROOT"] = str(spec.maildrop_root.resolve())
    if spec.run_log_path is not None:
        env["MAIL_KANBAN_RUN_LOG"] = str(spec.run_log_path.resolve())

    payload: dict[str, object] = {
        "Label": spec.label,
        "ProgramArguments": ["/bin/bash", wrapper, "run-daily"],
        "WorkingDirectory": wd,
        "EnvironmentVariables": env,
        "StartCalendarInterval": {"Hour": int(spec.hour), "Minute": int(spec.minute)},
        "StandardOutPath": out_log,
        "StandardErrorPath": err_log,
    }

    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
    return data.decode("utf-8")

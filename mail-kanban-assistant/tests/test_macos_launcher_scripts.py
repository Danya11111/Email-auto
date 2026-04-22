from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.application.launchd_plist import LaunchdPlistSpecDTO, render_launchd_plist_xml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bash_n(script: Path) -> None:
    bash_exe: str | None = None
    if sys.platform == "win32":
        prefixes: list[str] = []
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            v = os.environ.get(key, "")
            if v:
                prefixes.append(v)
        for prefix in prefixes:
            candidate = Path(prefix) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                bash_exe = str(candidate)
                break
        if bash_exe is None:
            which = shutil.which("bash")
            if which and "system32" in which.lower():
                pytest.skip("Git Bash not found; WSL bash cannot -n check Windows paths from this harness")
            bash_exe = which
    else:
        bash_exe = shutil.which("bash")
    if bash_exe is None:
        pytest.skip("bash not available")
    subprocess.run([bash_exe, "-n", str(script)], check=True)


def test_run_mail_assistant_daily_shell_passes_bash_syntax() -> None:
    script = _repo_root() / "scripts" / "macos" / "run-mail-assistant-daily.sh"
    _bash_n(script)


def test_run_mail_assistant_command_passes_bash_syntax() -> None:
    script = _repo_root() / "scripts" / "macos" / "run-mail-assistant.command"
    _bash_n(script)


def test_open_latest_digest_command_passes_bash_syntax() -> None:
    script = _repo_root() / "scripts" / "macos" / "open-latest-digest.command"
    _bash_n(script)


def test_launchd_plist_includes_run_log_env(tmp_path: Path) -> None:
    rr = tmp_path / "repo"
    run_log = rr / "data" / "logs" / "launchd-daily.log"
    spec = LaunchdPlistSpecDTO(
        label="com.example.mailassistant",
        wrapper_script=rr / "scripts" / "macos" / "run-mail-assistant-daily.sh",
        working_directory=rr,
        digest_out=rr / "data" / "digest.md",
        stdout_path=rr / "out.log",
        stderr_path=rr / "err.log",
        hour=7,
        minute=0,
        maildrop_root=rr / "data" / "maildrop",
        run_log_path=run_log,
    )
    xml = render_launchd_plist_xml(spec)
    payload = plistlib.loads(xml.encode("utf-8"))
    env = payload["EnvironmentVariables"]
    assert env["MAIL_KANBAN_RUN_LOG"] == str(run_log.resolve())

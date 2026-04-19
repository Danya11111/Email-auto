from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from app.application.dtos import DigestLLMResponseDTO, TriageLLMResponseDTO
from app.domain.enums import MessageImportance, ReplyRequirement
from app.interfaces.cli import app


class _FakeLmStudio:
    def triage_message(self, message):  # noqa: ANN001
        return TriageLLMResponseDTO(
            importance=MessageImportance.LOW,
            reply_requirement=ReplyRequirement.NO,
            summary="ok",
            actionable=False,
            confidence=0.9,
            reason_codes=("cli_smoke",),
        )

    def extract_tasks(self, message, triage_summary: str):  # noqa: ANN001
        _ = (message, triage_summary)
        return ()

    def build_digest_markdown(self, window_start, window_end, payload_json: str):  # noqa: ANN001
        _ = (window_start, window_end, payload_json)
        return DigestLLMResponseDTO(markdown="# unused\n")

    def close(self) -> None:
        return None


def test_cli_smoke_init_ingest_triage_extract_digest_review_run_daily(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.bootstrap.make_lm_studio_client", lambda settings, logger: _FakeLmStudio())

    db_path = tmp_path / "cli.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0

    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    assert runner.invoke(app, ["ingest-eml", "--path", str(inbox)], env={**os.environ}).exit_code == 0

    assert runner.invoke(app, ["triage"], env={**os.environ}).exit_code == 0
    assert runner.invoke(app, ["extract-tasks"], env={**os.environ}).exit_code == 0

    assert runner.invoke(app, ["review-list"], env={**os.environ}).exit_code == 0

    digest_path = tmp_path / "digest.md"
    assert runner.invoke(app, ["build-digest", "--out", str(digest_path)], env={**os.environ}).exit_code == 0
    assert digest_path.exists()

    assert runner.invoke(app, ["run-daily"], env={**os.environ}).exit_code == 0

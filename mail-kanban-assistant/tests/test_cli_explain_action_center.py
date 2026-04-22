from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from app.application.dtos import DigestLLMResponseDTO, TriageLLMResponseDTO
from app.domain.enums import MessageImportance, ReplyRequirement
from app.interfaces.cli import app


class _FakeLmStudio:
    def triage_message(self, message):  # noqa: ANN001
        return TriageLLMResponseDTO(
            importance=MessageImportance.HIGH,
            reply_requirement=ReplyRequirement.REQUIRED,
            summary="please reply",
            actionable=True,
            confidence=0.91,
            reason_codes=("cli_explain",),
        )

    def extract_tasks(self, message, triage_summary: str):  # noqa: ANN001
        _ = (message, triage_summary)
        return ()

    def build_digest_markdown(self, window_start, window_end, payload_json: str):  # noqa: ANN001
        _ = (window_start, window_end, payload_json)
        return DigestLLMResponseDTO(markdown="# unused\n")

    def close(self) -> None:
        return None


def test_cli_explain_message_thread_action_item(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.bootstrap.make_lm_studio_client", lambda settings, logger: _FakeLmStudio())

    db_path = tmp_path / "explain.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "one.eml").write_text(
        "From: alice@test.com\n"
        "To: bob@test.com\n"
        "Subject: Contract follow-up\n"
        "Message-ID: <cli-explain-1@test>\n"
        "Date: Mon, 19 Apr 2026 12:00:00 +0000\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Please confirm.\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0
    assert runner.invoke(app, ["ingest-eml", "--path", str(inbox)], env={**os.environ}).exit_code == 0
    assert runner.invoke(app, ["triage"], env={**os.environ}).exit_code == 0

    r1 = runner.invoke(app, ["explain-message", "--message-id", "1"], env={**os.environ})
    assert r1.exit_code == 0
    assert "m1" in r1.stdout

    r2 = runner.invoke(app, ["action-center", "--json"], env={**os.environ})
    assert r2.exit_code == 0
    data = json.loads(r2.stdout)
    assert data.get("threads")
    tid = str(data["threads"][0]["thread_id"])
    r3 = runner.invoke(app, ["explain-thread", "--thread-id", tid], env={**os.environ})
    assert r3.exit_code == 0
    assert tid in r3.stdout

    item_id = next((i["item_id"] for i in data.get("items", []) if i.get("source_type") == "thread"), None)
    assert item_id is not None
    r4 = runner.invoke(app, ["explain-action-item", "--item-id", item_id], env={**os.environ})
    assert r4.exit_code == 0
    assert item_id in r4.stdout

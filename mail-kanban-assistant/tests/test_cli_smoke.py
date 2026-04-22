from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from app.application.dtos import DigestLLMResponseDTO, ReplyDraftStructuredLLMItemDTO, TriageLLMResponseDTO
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

    def generate_reply_draft_structured(self, *, context_json: str, tone: str, reply_state: str):  # noqa: ANN001
        _ = (context_json, tone, reply_state)
        return ReplyDraftStructuredLLMItemDTO(
            subject_suggestion="Re: smoke",
            opening_line="Hi,",
            core_points=("Thanks for your note.",),
            closing_line="Best,",
            body_text="Hi,\n\nThanks for your note.\n\nBest,",
            short_rationale="Smoke stub.",
            missing_information=(),
            confidence=0.5,
            fact_boundary_note="Stub output for tests.",
        )

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

    r_ac = runner.invoke(app, ["action-center", "--compact"], env={**os.environ})
    assert r_ac.exit_code == 0
    assert "Action center" in r_ac.stdout

    export_path = tmp_path / "ac.md"
    assert runner.invoke(app, ["action-center-export", "--out", str(export_path)], env={**os.environ}).exit_code == 0
    assert export_path.exists()

    assert runner.invoke(app, ["run-daily"], env={**os.environ}).exit_code == 0


def test_cli_prepare_maildrop_doctor_print_launchd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "d.sqlite3"))
    maildrop = tmp_path / "maildrop"
    monkeypatch.setenv("MAILDROP_ROOT", str(maildrop))

    class _Probe:
        def get_status(self, url: str, *, timeout_seconds: float) -> int | None:  # noqa: ARG002
            return 200

    monkeypatch.setattr("app.interfaces.cli.UrllibHttpProbe", _Probe)

    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0
    assert runner.invoke(app, ["prepare-maildrop", "--path", str(maildrop)], env={**os.environ}).exit_code == 0

    repo_root = Path(__file__).resolve().parents[1]
    r = runner.invoke(app, ["doctor", "--repo-root", str(repo_root)], env={**os.environ})
    assert r.exit_code == 0
    assert "[OK]" in r.stdout or "[WARN]" in r.stdout

    r2 = runner.invoke(
        app,
        ["print-launchd", "--repo-root", str(repo_root), "--digest-out", str(tmp_path / "digest.md")],
        env={**os.environ},
    )
    assert r2.exit_code == 0
    assert "Label" in r2.stdout

    out_plist = tmp_path / "agent.plist"
    r3 = runner.invoke(
        app,
        [
            "install-launchd",
            "--output",
            str(out_plist),
            "--repo-root",
            str(repo_root),
        ],
        env={**os.environ},
    )
    assert r3.exit_code == 0
    assert out_plist.exists()


def test_cli_doctor_yougile_missing_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "yg_doc.sqlite3"))
    monkeypatch.setenv("KANBAN_PROVIDER", "yougile")
    monkeypatch.delenv("YOUGILE_API_KEY", raising=False)
    monkeypatch.delenv("YOUGILE_COLUMN_ID_TODO", raising=False)

    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0
    repo_root = Path(__file__).resolve().parents[1]
    r = runner.invoke(app, ["doctor", "--repo-root", str(repo_root)], env={**os.environ})
    assert r.exit_code == 0
    assert "YOUGILE_API_KEY" in r.stdout


def test_cli_kanban_yougile_dry_run_does_not_require_live_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "yg_cli.sqlite3"))
    monkeypatch.setenv("KANBAN_PROVIDER", "yougile")
    monkeypatch.setenv("YOUGILE_API_KEY", "test-key-not-used-on-dry-run")
    monkeypatch.setenv("YOUGILE_COLUMN_ID_TODO", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("YOUGILE_BOARD_ID", "00000000-0000-0000-0000-000000000002")

    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0
    r = runner.invoke(app, ["kanban-sync", "--dry-run"], env={**os.environ})
    assert r.exit_code == 0
    assert "updated=" in r.stdout


def test_cli_kanban_preview_status_dry_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "kb_cli.sqlite3"))
    monkeypatch.setenv("KANBAN_ROOT_DIR", str(tmp_path / "kanban_cli"))
    monkeypatch.setenv("KANBAN_PROVIDER", "local_file")

    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0

    r1 = runner.invoke(app, ["kanban-preview"], env={**os.environ})
    assert r1.exit_code == 0
    assert "approved_ready=" in r1.stdout

    r2 = runner.invoke(app, ["kanban-status"], env={**os.environ})
    assert r2.exit_code == 0
    assert "pending=" in r2.stdout

    r3 = runner.invoke(app, ["kanban-sync", "--dry-run"], env={**os.environ})
    assert r3.exit_code == 0
    assert "kanban-sync done:" in r3.stdout
    assert "updated=" in r3.stdout

    r3b = runner.invoke(app, ["kanban-resync-changed", "--dry-run"], env={**os.environ})
    assert r3b.exit_code == 0
    assert "kanban-resync-changed" in r3b.stdout

    r3c = runner.invoke(app, ["kanban-show-task-sync", "--task-id", "1", "--json"], env={**os.environ})
    assert r3c.exit_code == 0
    assert "task_id" in r3c.stdout

    r3d = runner.invoke(app, ["kanban-status", "--json"], env={**os.environ})
    assert r3d.exit_code == 0
    assert "pending" in r3d.stdout

    r4 = runner.invoke(app, ["kanban-export-local"], env={**os.environ})
    assert r4.exit_code == 0
    assert "wrote" in r4.stdout


def test_cli_ingest_apple_mail_drop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "m.sqlite3"))
    maildrop = tmp_path / "maildrop"
    monkeypatch.setenv("MAILDROP_ROOT", str(maildrop))

    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0
    assert runner.invoke(app, ["prepare-maildrop"], env={**os.environ}).exit_code == 0

    incoming = maildrop / "incoming"
    snap = {
        "snapshot_id": "cli-snap",
        "source": "apple_mail_drop",
        "message_id": "cli-mid",
        "thread_id": None,
        "mailbox_name": None,
        "account_name": None,
        "subject": "CLI",
        "sender_name": None,
        "sender_email": "x@y.com",
        "to": [],
        "cc": [],
        "bcc": [],
        "date": "2026-04-19T10:00:00+00:00",
        "body_text": "hello cli",
        "body_preview": None,
        "unread": None,
        "flagged": None,
        "received_at": "2026-04-19T10:00:00+00:00",
        "collected_at": "2026-04-19T11:00:00+00:00",
        "attachments_summary": None,
        "raw_metadata": {},
    }
    (incoming / "cli.json").write_text(__import__("json").dumps(snap), encoding="utf-8")

    monkeypatch.setattr("app.bootstrap.make_lm_studio_client", lambda settings, logger: _FakeLmStudio())
    r = runner.invoke(app, ["ingest-apple-mail-drop"], env={**os.environ})
    assert r.exit_code == 0
    assert "ingested=1" in r.stdout


def test_cli_reply_draft_list_empty_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "rd.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    runner = CliRunner()
    assert runner.invoke(app, ["init-db"], env={**os.environ}).exit_code == 0
    r = runner.invoke(app, ["reply-draft-list"], env={**os.environ})
    assert r.exit_code == 0
    assert "no reply drafts" in r.stdout.lower()

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from app.application.doctor_report import DoctorEnvironmentUseCase
from app.application.launchd_plist import LaunchdPlistSpecDTO, render_launchd_plist_xml
from app.application.policies import TaskAutomationPolicy
from app.application.use_cases import IngestMessagesUseCase
from app.application.use_cases.prepare_maildrop import PrepareMaildropUseCase
from app.bootstrap import (
    build_process_apple_mail_drop_use_case,
    build_wiring,
    init_database,
    run_daily,
)
from app.config import AppSettings
from app.infrastructure.clock import SystemClock
from app.infrastructure.fs.maildrop_filesystem import OsMaildropFilesystem
from app.infrastructure.http.http_probe import UrllibHttpProbe
from app.infrastructure.logging.logger import StructuredLoggerAdapter
from app.infrastructure.mail.eml_reader import EmlDirectoryReader
from app.infrastructure.mail.mbox_reader import MboxFileReader
from app.infrastructure.storage.repositories import SqlitePipelineRunRepository
from app.infrastructure.storage.sqlite_db import open_connection
from app.utils.ids import new_run_id

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def _configure_logging(log_level: str = typer.Option("INFO", "--log-level", help="Python logging level name.")) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")


@app.command("init-db")
def init_db() -> None:
    settings = AppSettings()
    init_database(settings)
    typer.echo(f"Database initialized at {settings.database_path.resolve()}")


@app.command("ingest-eml")
def ingest_eml(path: Path = typer.Option(..., "--path", exists=True, help="Directory with .eml files.")) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    uc = IngestMessagesUseCase(messages=w.messages, pipeline_runs=pipeline, logger=logger)
    run_id = new_run_id()
    try:
        res = uc.execute(EmlDirectoryReader(path), run_id=run_id, command="ingest-eml", record_pipeline=True)
        typer.echo(
            f"ingest-eml done: inserted={res.inserted} duplicates={res.duplicates} failures={res.failures} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("ingest-mbox")
def ingest_mbox(path: Path = typer.Option(..., "--path", exists=True, help="Path to .mbox file.")) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    pipeline = SqlitePipelineRunRepository(conn, clock)
    uc = IngestMessagesUseCase(messages=w.messages, pipeline_runs=pipeline, logger=logger)
    run_id = new_run_id()
    try:
        res = uc.execute(MboxFileReader(path), run_id=run_id, command="ingest-mbox", record_pipeline=True)
        typer.echo(
            f"ingest-mbox done: inserted={res.inserted} duplicates={res.duplicates} failures={res.failures} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("triage")
def triage() -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    run_id = new_run_id()
    try:
        res = w.triage_uc.execute(run_id=run_id, batch_limit=int(settings.triage_batch_size))
        typer.echo(
            f"triage done: processed={res.processed} failures={res.failures} "
            f"reviews_enqueued={res.reviews_enqueued} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("extract-tasks")
def extract_tasks() -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    policy = TaskAutomationPolicy(
        confidence_threshold=settings.task_confidence_threshold,
        auto_create_kanban=settings.auto_create_kanban_tasks,
    )
    run_id = new_run_id()
    try:
        res = w.extract_uc.execute(
            run_id=run_id,
            policy=policy,
            batch_limit=int(settings.task_extraction_batch_size),
        )
        typer.echo(
            "extract-tasks done: "
            f"messages_processed={res.messages_processed} tasks_created={res.tasks_created} failures={res.failures} "
            f"reviews_enqueued={res.reviews_enqueued} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("build-digest")
def build_digest(
    out: Path | None = typer.Option(None, "--out", help="Optional path to write digest markdown."),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    run_id = new_run_id()
    try:
        res = w.digest_uc.execute(run_id=run_id, pipeline_run_db_id=None, pipeline_stats=None)
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(res.markdown, encoding="utf-8")
        typer.echo(res.markdown)
    finally:
        w.llm.close()
        conn.close()


@app.command("review-list")
def review_list(limit: int = typer.Option(50, "--limit", min=1, max=500)) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        items = w.list_reviews_uc.execute(limit=limit)
        if not items:
            typer.echo("No pending reviews.")
            return
        for it in items:
            typer.echo(
                f"r{it.id}\t{it.review_kind.value}\tm{it.related_message_id}"
                + (f"\tt{it.related_task_id}" if it.related_task_id is not None else "")
                + f"\tconf={it.confidence:.2f}\t{it.reason_code}\t{it.reason_text}"
            )
    finally:
        w.llm.close()
        conn.close()


@app.command("review-approve")
def review_approve(
    review_id: int = typer.Option(..., "--review-id"),
    note: str | None = typer.Option(None, "--note"),
    decided_by: str = typer.Option("manual_cli", "--decided-by"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        w.approve_review_uc.execute(review_id=review_id, decided_by=decided_by, note=note)
        typer.echo(f"approved review r{review_id}")
    finally:
        w.llm.close()
        conn.close()


@app.command("review-reject")
def review_reject(
    review_id: int = typer.Option(..., "--review-id"),
    note: str | None = typer.Option(None, "--note"),
    decided_by: str = typer.Option("manual_cli", "--decided-by"),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        w.reject_review_uc.execute(review_id=review_id, decided_by=decided_by, note=note)
        typer.echo(f"rejected review r{review_id}")
    finally:
        w.llm.close()
        conn.close()


@app.command("review-export")
def review_export(
    out: Path = typer.Option(..., "--out", help="Write pending reviews as JSON to this path."),
    limit: int = typer.Option(200, "--limit", min=1, max=2000),
) -> None:
    settings = AppSettings()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    try:
        items = w.list_reviews_uc.execute(limit=limit)
        payload = [it.model_dump(mode="json") for it in items]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"wrote {len(payload)} pending reviews to {out.resolve()}")
    finally:
        w.llm.close()
        conn.close()


@app.command("prepare-maildrop")
def prepare_maildrop_cmd(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        help="Maildrop root directory (creates incoming/processed/failed/exported). Defaults to MAILDROP_ROOT.",
    ),
) -> None:
    settings = AppSettings()
    root = path.resolve() if path is not None else settings.maildrop_root.resolve()
    logger = StructuredLoggerAdapter()
    uc = PrepareMaildropUseCase(fs=OsMaildropFilesystem(logger))
    typer.echo(uc.execute(root))


@app.command("ingest-apple-mail-drop")
def ingest_apple_mail_drop_cmd(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        help="Maildrop root (reads incoming/*.json). Defaults to MAILDROP_ROOT from settings.",
    ),
) -> None:
    settings = AppSettings()
    root = path.resolve() if path is not None else settings.maildrop_root.resolve()
    conn = open_connection(settings.database_path)
    clock = SystemClock()
    logger = StructuredLoggerAdapter()
    w = build_wiring(conn, clock, logger, settings)
    uc = build_process_apple_mail_drop_use_case(conn, clock, logger)
    run_id = new_run_id()
    try:
        res = uc.execute(maildrop_root=root, run_id=run_id)
        typer.echo(
            "ingest-apple-mail-drop done: "
            f"found={res.found} ingested={res.ingested} duplicate={res.duplicate} failed={res.failed} "
            f"moved_processed={res.moved_processed} moved_failed={res.moved_failed} run_id={res.run_id}"
        )
    finally:
        w.llm.close()
        conn.close()


@app.command("doctor")
def doctor_cmd(
    repo_root: Optional[Path] = typer.Option(
        None,
        "--repo-root",
        help="Repository root for relative checks (defaults to current working directory).",
    ),
    wrapper: Optional[Path] = typer.Option(
        None,
        "--wrapper",
        help="Optional path to launchd wrapper script (defaults to <repo>/scripts/macos/run-mail-assistant-daily.sh).",
    ),
) -> None:
    settings = AppSettings()
    rr = repo_root.resolve() if repo_root is not None else Path.cwd().resolve()
    wr = wrapper.resolve() if wrapper is not None else (rr / "scripts" / "macos" / "run-mail-assistant-daily.sh")
    uc = DoctorEnvironmentUseCase(http=UrllibHttpProbe())
    report = uc.execute(settings, repo_root=rr, wrapper_script=wr)
    typer.echo(report.render_text())


def _default_launchd_log_paths(repo_root: Path) -> tuple[Path, Path]:
    if sys.platform == "darwin":
        base = Path.home() / "Library/Logs/mail-assistant"
        return base / "stdout.log", base / "stderr.log"
    return repo_root / "data" / "logs" / "launchd-stdout.log", repo_root / "data" / "logs" / "launchd-stderr.log"


@app.command("print-launchd")
def print_launchd_cmd(
    repo_root: Optional[Path] = typer.Option(None, "--repo-root", help="Checkout root (absolute paths in plist)."),
    wrapper: Optional[Path] = typer.Option(None, "--wrapper", help="Wrapper script path."),
    digest_out: Optional[Path] = typer.Option(None, "--digest-out", help="Digest output path for run-daily."),
    hour: int = typer.Option(7, "--hour", min=0, max=23),
    minute: int = typer.Option(0, "--minute", min=0, max=59),
) -> None:
    settings = AppSettings()
    rr = repo_root.resolve() if repo_root is not None else Path.cwd().resolve()
    wr = wrapper.resolve() if wrapper is not None else (rr / "scripts" / "macos" / "run-mail-assistant-daily.sh")
    digest = digest_out.resolve() if digest_out is not None else (rr / "data" / "digest.md")
    out_log, err_log = _default_launchd_log_paths(rr)
    spec = LaunchdPlistSpecDTO(
        label=settings.launchd_label,
        wrapper_script=wr,
        working_directory=rr,
        digest_out=digest,
        stdout_path=out_log,
        stderr_path=err_log,
        hour=hour,
        minute=minute,
        maildrop_root=settings.maildrop_root.resolve(),
    )
    typer.echo(render_launchd_plist_xml(spec))


@app.command("install-launchd")
def install_launchd_cmd(
    output: Path = typer.Option(..., "--output", help="Where to write the LaunchAgent plist."),
    repo_root: Optional[Path] = typer.Option(None, "--repo-root", help="Checkout root (absolute paths in plist)."),
    wrapper: Optional[Path] = typer.Option(None, "--wrapper", help="Wrapper script path."),
    digest_out: Optional[Path] = typer.Option(None, "--digest-out", help="Digest output path for run-daily."),
    hour: int = typer.Option(7, "--hour", min=0, max=23),
    minute: int = typer.Option(0, "--minute", min=0, max=59),
) -> None:
    settings = AppSettings()
    rr = repo_root.resolve() if repo_root is not None else Path.cwd().resolve()
    wr = wrapper.resolve() if wrapper is not None else (rr / "scripts" / "macos" / "run-mail-assistant-daily.sh")
    digest = digest_out.resolve() if digest_out is not None else (rr / "data" / "digest.md")
    out_log, err_log = _default_launchd_log_paths(rr)
    spec = LaunchdPlistSpecDTO(
        label=settings.launchd_label,
        wrapper_script=wr,
        working_directory=rr,
        digest_out=digest,
        stdout_path=out_log,
        stderr_path=err_log,
        hour=hour,
        minute=minute,
        maildrop_root=settings.maildrop_root.resolve(),
    )
    xml = render_launchd_plist_xml(spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(xml, encoding="utf-8")
    typer.echo(f"Wrote plist to {output.resolve()}")
    if sys.platform == "darwin":
        typer.echo("Next (user LaunchAgent):")
        typer.echo("  mkdir -p ~/Library/LaunchAgents")
        typer.echo(f"  cp {output.resolve()} ~/Library/LaunchAgents/{settings.launchd_label}.plist")
        typer.echo(
            f"  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/{settings.launchd_label}.plist 2>/dev/null || true"
        )
        typer.echo(f"  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{settings.launchd_label}.plist")
    else:
        typer.echo("Note: launchctl steps above apply to macOS only.")


@app.command("run-daily")
def run_daily_cmd(
    digest_out: Path | None = typer.Option(
        None,
        "--digest-out",
        help="Optional path to write digest markdown (full digest is not printed to stdout).",
    ),
) -> None:
    settings = AppSettings()
    result = run_daily(settings=settings, digest_output=digest_out)
    typer.echo(result.stdout_summary)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import httpx

from app.application.doctor_report import DoctorLineDTO
from app.application.dtos import YougileSmokeSyncResultDTO, YougileWorkspaceDiscoveryDTO
from app.application.kanban_resync_policy import plan_kanban_outbound
from app.application.kanban_mapping import KanbanMappingOptions, build_kanban_card_draft
from app.application.use_cases.kanban_sync import SyncApprovedTasksToKanbanUseCase, mapping_options_from_settings
from app.application.yougile_errors import format_yougile_provider_error, format_yougile_transport_error
from app.config import AppSettings
from app.domain.enums import KanbanProvider, TaskStatus
from app.domain.models import KanbanCardDraft
from app.application.ports import KanbanSyncRepositoryPort, LoggerPort, TaskRepositoryPort
from app.infrastructure.kanban.yougile_rest_client import YougileRestClient


def render_yougile_discovery_text(dto: YougileWorkspaceDiscoveryDTO, *, compact: bool, base_url_for_env: str) -> str:
    if not dto.ok:
        return f"YouGile discovery FAILED\n{dto.error or 'unknown error'}\n"
    lines: list[str] = ["YouGile workspace discovery", ""]
    if dto.warnings:
        lines.append("Warnings:")
        for w in dto.warnings:
            lines.append(f"  - {w}")
        lines.append("")
    lines.append(f"Boards ({len(dto.boards)}):")
    for b in dto.boards:
        if compact:
            lines.append(f"  {b.id}\t{b.title}")
        else:
            lines.append(f"  - {b.title}")
            lines.append(f"      id: {b.id}")
            if b.project_id:
                lines.append(f"      projectId: {b.project_id}")
    lines.append("")
    by_board: dict[str, list] = {}
    for c in dto.columns:
        by_board.setdefault(c.board_id, []).append(c)
    lines.append(f"Columns ({len(dto.columns)}); grouped by boardId:")
    for bid, cols in sorted(by_board.items(), key=lambda x: x[0]):
        lines.append(f"  board {bid}:")
        for c in cols:
            if compact:
                lines.append(f"    {c.id}\t{c.title}")
            else:
                lines.append(f"    - {c.title}")
                lines.append(f"        id: {c.id}")
    lines.append("")
    lines.append("Copy into .env (adjust names):")
    lines.append("KANBAN_PROVIDER=yougile")
    pub = (base_url_for_env or "https://ru.yougile.com").strip().rstrip("/")
    lines.append(f"YOUGILE_BASE_URL={pub}")
    if dto.boards:
        lines.append(f"YOUGILE_BOARD_ID={dto.boards[0].id}")
    if dto.columns:
        lines.append(f"YOUGILE_COLUMN_ID_TODO={dto.columns[0].id}")
    lines.append("YOUGILE_API_KEY=<paste your API key>")
    return "\n".join(lines) + "\n"


def build_yougile_env_fragment(settings: AppSettings, *, board_id: str | None, column_todo: str | None) -> str:
    """Printable .env fragment (may contain placeholders)."""
    b = (board_id or settings.yougile_board_id or "").strip() or "<YOUGILE_BOARD_ID from discover>"
    ct = (column_todo or settings.yougile_column_id_todo or "").strip() or "<YOUGILE_COLUMN_ID_TODO from discover>"
    cd = (settings.yougile_column_id_done or "").strip() or ""
    cb = (settings.yougile_column_id_blocked or "").strip() or ""
    lines = [
        "# --- Paste into .env ---",
        "KANBAN_PROVIDER=yougile",
        f"YOUGILE_BASE_URL={settings.yougile_base_url}",
        "YOUGILE_API_KEY=<your API key>",
        f"YOUGILE_BOARD_ID={b}",
        f"YOUGILE_COLUMN_ID_TODO={ct}",
    ]
    if cd:
        lines.append(f"YOUGILE_COLUMN_ID_DONE={cd}")
    else:
        lines.append("# YOUGILE_COLUMN_ID_DONE=")
    if cb:
        lines.append(f"YOUGILE_COLUMN_ID_BLOCKED={cb}")
    else:
        lines.append("# YOUGILE_COLUMN_ID_BLOCKED=")
    lines.append(f"YOUGILE_REQUESTS_PER_MINUTE={settings.yougile_requests_per_minute}")
    lines.append(f"YOUGILE_ENABLE_UPDATE_EXISTING={'true' if settings.yougile_enable_update_existing else 'false'}")
    return "\n".join(lines) + "\n"


def run_yougile_deep_doctor(settings: AppSettings, logger: LoggerPort, http: httpx.Client | None = None) -> tuple[DoctorLineDTO, ...]:
    lines: list[DoctorLineDTO] = []
    lines.append(DoctorLineDTO("OK", "YouGile deep checks (live API)"))
    if settings.kanban_provider != KanbanProvider.YOUGILE:
        lines.append(DoctorLineDTO("WARN", f"KANBAN_PROVIDER is {settings.kanban_provider.value} (expected yougile for full relevance)."))
    if not settings.yougile_api_key.strip():
        lines.append(DoctorLineDTO("FAIL", "YOUGILE_API_KEY missing — cannot call YouGile API."))
        return tuple(lines)
    if not settings.yougile_base_url.strip():
        lines.append(DoctorLineDTO("FAIL", "YOUGILE_BASE_URL empty."))
        return tuple(lines)
    client = YougileRestClient.from_settings(settings, logger, http)
    try:
        st, data, raw = client.request_json("GET", "/boards")
    except httpx.HTTPError as exc:
        lines.append(DoctorLineDTO("FAIL", format_yougile_transport_error(exc, context="GET /boards")))
        return tuple(lines)
    if st != 200:
        lines.append(
            DoctorLineDTO(
                "FAIL",
                format_yougile_provider_error(status_code=st, data=data, fallback_body=raw, context="GET /boards (auth)"),
            )
        )
        return tuple(lines)
    lines.append(DoctorLineDTO("OK", f"YouGile API reachable: GET /boards HTTP {st}"))

    bid = settings.yougile_board_id.strip()
    if bid:
        stb, datab, rawb = client.request_json("GET", f"/boards/{bid}")
        if stb == 200:
            title = ""
            if isinstance(datab, dict) and isinstance(datab.get("title"), str):
                title = datab["title"]
            lines.append(DoctorLineDTO("OK", f"Configured board exists: {title!r} ({bid})"))
        else:
            lines.append(
                DoctorLineDTO(
                    "FAIL",
                    format_yougile_provider_error(status_code=stb, data=datab, fallback_body=rawb, context=f"GET /boards/{bid}"),
                )
            )
    else:
        lines.append(DoctorLineDTO("WARN", "YOUGILE_BOARD_ID not set — cannot verify board row."))

    for label, cid in (
        ("TODO", settings.yougile_column_id_todo),
        ("DONE", settings.yougile_column_id_done),
        ("BLOCKED", settings.yougile_column_id_blocked),
    ):
        c = (cid or "").strip()
        if not c:
            if label == "TODO":
                lines.append(DoctorLineDTO("FAIL", "YOUGILE_COLUMN_ID_TODO missing — sync cannot create tasks."))
            else:
                lines.append(DoctorLineDTO("OK", f"YOUGILE_COLUMN_ID_{label} optional — not set."))
            continue
        stc, datac, rawc = client.request_json("GET", f"/columns/{c}")
        if stc == 200:
            t = ""
            if isinstance(datac, dict) and isinstance(datac.get("title"), str):
                t = datac["title"]
            lines.append(DoctorLineDTO("OK", f"Column {label} OK: {t!r} ({c})"))
        else:
            lines.append(
                DoctorLineDTO(
                    "FAIL",
                    format_yougile_provider_error(status_code=stc, data=datac, fallback_body=rawc, context=f"GET /columns/{c}"),
                )
            )

    lines.append(
        DoctorLineDTO(
            "OK",
            f"Rate limit budget: target {settings.yougile_requests_per_minute} req/min (YouGile max ~50/company).",
        )
    )
    if not settings.yougile_enable_update_existing:
        lines.append(
            DoctorLineDTO(
                "OK",
                "Update policy: YOUGILE_ENABLE_UPDATE_EXISTING=false — fingerprint changes skip remote writes (no silent duplicates).",
            )
        )
    return tuple(lines)


def make_smoke_draft_hook(task_id: int) -> Callable[[KanbanCardDraft], KanbanCardDraft]:
    def _hook(d: KanbanCardDraft) -> KanbanCardDraft:
        prefix = f"[mail-assistant-smoke:{task_id}] "
        title = (prefix + d.title).strip()[:1024]
        suffix = f"\n---\n[mail-assistant-smoke]\nSearch in YouGile for: mail-assistant-smoke:{task_id}\n"
        return replace(d, title=title or prefix.strip(), description=(d.description + suffix).strip())

    return _hook


@dataclass(frozen=True, slots=True)
class YougileSmokeSyncUseCase:
    """Single-task YouGile smoke: validates approval, tags draft, dry-run by default."""

    tasks: TaskRepositoryPort
    sync: KanbanSyncRepositoryPort
    sync_uc: SyncApprovedTasksToKanbanUseCase
    settings: AppSettings

    def execute(self, *, task_id: int, dry_run: bool, run_id: str) -> YougileSmokeSyncResultDTO:
        ctx = self.tasks.get_task_kanban_context(task_id)
        if ctx is None:
            return YougileSmokeSyncResultDTO(
                task_id=task_id,
                dry_run=dry_run,
                task_approved=False,
                plan=None,
                run_id=run_id,
                message="Task not found in database.",
            )
        if ctx.task.status != TaskStatus.APPROVED:
            return YougileSmokeSyncResultDTO(
                task_id=task_id,
                dry_run=dry_run,
                task_approved=False,
                plan=None,
                run_id=run_id,
                message=f"Task status is {ctx.task.status.value}; smoke sync requires approved.",
            )
        opts = mapping_options_from_settings(self.settings)
        draft = build_kanban_card_draft(ctx, opts)
        hook = make_smoke_draft_hook(task_id)
        draft = hook(draft)
        plan = plan_kanban_outbound(
            provider=KanbanProvider.YOUGILE,
            settings=self.settings,
            sync=self.sync,
            task_id=task_id,
            draft=draft,
            task_status=TaskStatus.APPROVED,
        )
        if dry_run:
            return YougileSmokeSyncResultDTO(
                task_id=task_id,
                dry_run=True,
                task_approved=True,
                plan=plan.value,
                run_id=run_id,
                message=f"Dry-run only: would apply plan={plan.value} for YouGile (smoke-tagged draft). Use --execute for one real write.",
            )
        batch = self.sync_uc.execute(
            run_id=run_id,
            provider=KanbanProvider.YOUGILE,
            dry_run=False,
            limit=1,
            only_task_id=task_id,
            draft_hook=hook,
        )
        msg = (
            f"Smoke sync finished: synced={batch.synced} updated={batch.updated} skipped={batch.skipped} failed={batch.failed}"
        )
        ext_id: str | None = None
        ext_url: str | None = None
        if batch.synced + batch.updated > 0:
            row = self.sync.get_sync_record_for_task(task_id, KanbanProvider.YOUGILE)
            if row is not None:
                ext_id = row.external_card_id
                ext_url = row.external_card_url
        return YougileSmokeSyncResultDTO(
            task_id=task_id,
            dry_run=False,
            task_approved=True,
            plan=plan.value,
            run_id=run_id,
            message=msg,
            external_task_id=ext_id,
            external_url=ext_url,
            synced=batch.synced,
            updated=batch.updated,
            skipped=batch.skipped,
            failed=batch.failed,
        )


def yougile_cleanup_note_text() -> str:
    return (
        "YouGile smoke-test cleanup (manual)\n"
        "-----------------------------------\n"
        "This CLI does not call destructive YouGile APIs (delete/archive) by default.\n"
        "\n"
        "Tasks created with `yougile-smoke-sync --execute` include:\n"
        "  - Title prefix: [mail-assistant-smoke:<task_id>]\n"
        "  - Description footer with the same token for search.\n"
        "\n"
        "In YouGile UI: use board search / filter for `mail-assistant-smoke` or your task id, then archive or delete the card.\n"
        "If you re-run smoke on the same internal task after deleting the remote card, clear or reset the "
        "`kanban_sync_records` row for that task+yougile in SQLite (advanced) or use a fresh test task id.\n"
    )


@dataclass(frozen=True, slots=True)
class YougileDiscoverWorkspaceUseCase:
    settings: AppSettings
    logger: LoggerPort

    def execute(self, *, http_client: httpx.Client | None = None) -> YougileWorkspaceDiscoveryDTO:
        if not self.settings.yougile_api_key.strip():
            return YougileWorkspaceDiscoveryDTO(ok=False, error="YOUGILE_API_KEY is not set", boards=(), columns=(), warnings=())
        client = YougileRestClient.from_settings(self.settings, self.logger, http_client)
        return client.discover_workspace()


def run_yougile_live_status_probe(settings: AppSettings, logger: LoggerPort, http: httpx.Client | None = None) -> tuple[str, ...]:
    """Short live lines for `kanban-status --probe` (YouGile only)."""
    if not settings.yougile_api_key.strip():
        return ("live_probe: skipped (YOUGILE_API_KEY empty)",)
    client = YougileRestClient.from_settings(settings, logger, http)
    out: list[str] = []
    try:
        st, data, raw = client.request_json("GET", "/boards")
    except httpx.HTTPError as exc:
        return (f"live_probe: FAIL {format_yougile_transport_error(exc, context='GET /boards')}",)
    if st != 200:
        return (f"live_probe: FAIL {format_yougile_provider_error(status_code=st, data=data, fallback_body=raw, context='GET /boards')}",)
    out.append(f"live_probe: GET /boards OK (HTTP {st})")
    bid = settings.yougile_board_id.strip()
    if bid:
        stb, datab, rawb = client.request_json("GET", f"/boards/{bid}")
        if stb == 200:
            out.append(f"live_probe: configured board OK ({bid})")
        else:
            out.append(
                f"live_probe: board FAIL — {format_yougile_provider_error(status_code=stb, data=datab, fallback_body=rawb, context='GET board')}"
            )
    ct = settings.yougile_column_id_todo.strip()
    if ct:
        stc, datac, rawc = client.request_json("GET", f"/columns/{ct}")
        if stc == 200:
            out.append(f"live_probe: TODO column OK ({ct})")
        else:
            out.append(
                f"live_probe: TODO column FAIL — {format_yougile_provider_error(status_code=stc, data=datac, fallback_body=rawc, context='GET column')}"
            )
    return tuple(out)

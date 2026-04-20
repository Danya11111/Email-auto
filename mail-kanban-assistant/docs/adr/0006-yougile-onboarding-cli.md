# ADR 0006: YouGile CLI onboarding (discovery, doctor, smoke)

## Status

Accepted

## Context

Syncing to YouGile requires non-secret host settings plus **UUIDs** for board and columns. Operators need a safe, repeatable path without editing code or guessing API shapes.

## Decision

- Add **read-only discovery** via `GET /api-v2/boards` and `GET /api-v2/columns`, implemented in `YougileRestClient`, with defensive JSON parsing and typed DTOs.
- Expose **thin CLI commands** (`yougile-discover`, `yougile-print-env`, `yougile-doctor`, `yougile-config-check`, `yougile-smoke-sync`, `yougile-cleanup-note`) that delegate to **application use cases** in `yougile_workspace.py` (no business rules in Typer handlers beyond argv wiring).
- **Smoke sync** reuses `SyncApprovedTasksToKanbanUseCase` with a **`draft_hook`** so the fingerprint/title reflect a deliberate smoke marker without forking the outbox pipeline.
- **No destructive remote cleanup** in v1: operators remove smoke cards in the YouGile UI; the CLI prints explicit instructions.

## Consequences

- Extra HTTP traffic is **opt-in** per command (`yougile-*`, `doctor --yougile-probe`, `kanban-status --probe`).
- Discovery remains best-effort if YouGile changes envelope JSON; `--json` mode helps operators attach raw output to bug reports.

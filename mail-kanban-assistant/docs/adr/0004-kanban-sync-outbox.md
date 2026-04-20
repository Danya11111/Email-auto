# ADR 0004: Kanban sync outbox (SQLite) for approved tasks

## Status

Accepted

## Context

The assistant extracts **candidate** tasks and routes uncertain items through a **review queue**. Only **human-approved** tasks should create or update external Kanban artifacts. Sync must survive restarts, avoid duplicate cards, and support retries after transient provider failures.

## Decision

- Persist one row per `(task_id, provider)` in **`kanban_sync_records`** with `sync_status`, `card_fingerprint`, payload snapshot, retry metadata, and optional external ids/urls.
- **Fingerprint** is a deterministic hash over the mapped card fields (title, normalized description body, due date, priority, status, labels, dedupe marker). If the fingerprint matches the last **synced** row, a new write is skipped even if the task is again `approved` (e.g. after manual reopen).
- **Approved-only** listing drives the sync use case; successful sync moves the task to **`synced`** in `extracted_tasks`.
- **Adapters** implement `KanbanPort.create_card` behind the application layer. Default provider is **`local_file`** (JSON under `KANBAN_ROOT_DIR/cards/`). **Trello** uses httpx against the REST API without an SDK. **`stub`** performs no external I/O.
- **`KANBAN_AUTO_SYNC`** defaults to **false** so digest / approve / `run-daily` do not push to external systems unless explicitly enabled.

## Consequences

- Idempotency is anchored on stable task ids + fingerprint + provider, not on “run once” memory.
- Changing mapping rules or visible card content bumps the fingerprint and triggers a controlled re-sync path (pending record + provider write).
- Digest and `doctor` can surface aggregate sync health without opening individual card files.

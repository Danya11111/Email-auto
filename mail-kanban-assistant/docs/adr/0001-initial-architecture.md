# ADR 0001: Initial architecture (MVP scaffold)

## Status

Accepted (2026-04-19)

## Context

We need a local-first system on macOS that can:

- ingest mail without storing mailbox credentials,
- triage and extract tasks using a local model,
- persist results durably and survive restarts,
- evolve toward Apple Mail automation and Kanban sync without locking the core to any vendor.

## Decision

### Platform + language

Choose **Python 3.12** for strong typing ergonomics (Pydantic), excellent stdlib support for mail parsing (`email`, `mailbox`, `sqlite3`), and straightforward CLI packaging.

### Persistence

Choose **SQLite** for MVP persistence:

- single-file operational simplicity,
- transactional guarantees for idempotent writes,
- easy local backup and inspection.

Domain/application do not depend on SQLite directly; access is isolated behind repository ports.

### LLM integration

Choose **LM Studio** via an **OpenAI-compatible HTTP API** (`httpx`), with:

- strict **Pydantic-validated JSON** outputs per task (triage / tasks / digest),
- timeouts + bounded retries,
- latency logging via `LoggerPort`.

This keeps LLM concerns inside infrastructure while preserving testability via fake ports.

### Mail ingestion strategy (MVP)

Primary ingestion is **export-based**:

- `.eml` directories
- `.mbox` files

We explicitly **reject** (for MVP) reading undocumented Apple Mail internal databases because it is fragile, hard to support, and often surprises users during upgrades.

Apple Mail support is planned as **system automation + export**, surfaced behind `MessageReaderPort`.

### Kanban integration strategy (MVP)

Introduce `KanbanPort` with a **stub adapter** that performs no external side effects.

This prevents premature coupling to a specific vendor API while still letting the application layer express intent (and policy gates).

### CLI placement

CLI lives in `interfaces/` and performs **wiring only** (composition), delegating behavior to use cases.

### Scheduling

Ship a **launchd plist example** as documentation; scheduling is not executed by Python in MVP.

## Consequences

### Positive

- Core logic is unit-testable without network or LM Studio.
- Adding a new mail source is mostly a new `MessageReaderPort` implementation.
- Adding Kanban is a new `KanbanPort` implementation + policy tuning.

### Negative / trade-offs

- Export-based ingestion requires a user workflow (acceptable for MVP privacy posture).
- SQLite concurrency is limited compared to client/server databases (acceptable for single-user laptop scope).

## Alternatives considered

- **Direct Apple Mail DB access**: rejected (undocumented, brittle, privacy-sensitive).
- **Cloud LLM**: rejected for MVP baseline (conflicts with local-first requirements).
- **Heavy workflow frameworks**: rejected (minimal dependencies; Typer + explicit use cases).

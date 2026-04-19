# Mail Kanban Assistant (MVP scaffold)

Local-first macOS-oriented assistant that ingests exported mail (`.eml` / `.mbox`), triages it with a **local LLM** served by **LM Studio** (OpenAI-compatible API), extracts candidate tasks, routes uncertain outputs through a **CLI review queue**, and generates a **deterministic daily digest** markdown artifact.

This repository is intentionally **not** a toy script: it is a **Clean Architecture** scaffold with explicit ports, SQLite persistence, structured LLM outputs for triage/tasks, idempotent ingestion, and pytest coverage using fake LLM ports.

## Goals

- **Privacy-first**: no mailbox passwords are collected or stored.
- **Local-first**: primary data stays on disk; LLM calls target a local endpoint by default.
- **Restart-safe**: stable dedupe keys, SQLite constraints, processing statuses, and review dedupe indexes enable catch-up after sleep/crash.

## Non-goals (MVP)

- No direct parsing of undocumented Apple Mail internal databases.
- No real Kanban / issue tracker integration (interfaces + stub only).
- No cloud mail sync.
- No GUI (CLI-only review workflow).

## Architecture (layers)

```text
interfaces (CLI) ──► application (use cases + ports + policies) ──► domain (pure models)
                         ▲
                         └── infrastructure (SQLite, httpx LLM gateway, mail readers, logging)
```

**Rule of thumb**: domain + application must not import LM Studio, SQLite, filesystem paths, Typer, or HTTP.

## Engineering decisions (normative)

- **Python**: 3.12+ (`requires-python` in `pyproject.toml`).
- **Default model**: `qwen3-8b` (smaller default than 9B-class models) to better fit **Apple M1 + 8GB RAM** machines used as a daily driver.
- **Low-memory workflow**:
  - sequential LLM calls only (no worker pools; `LLM_CONCURRENCY` is accepted but capped to **1** in settings validation),
  - capped prompt bodies via `LLM_MAX_INPUT_CHARS` + `MESSAGE_BODY_TRUNCATE_STRATEGY` (implemented in `app/application/llm_input.py`, applied in the LM Studio gateway),
  - capped output tokens via `LLM_MAX_OUTPUT_TOKENS`,
  - small batch sizes (`TRIAGE_BATCH_SIZE`, `TASK_EXTRACTION_BATCH_SIZE`) to avoid huge per-run spikes.
- **LM Studio**: OpenAI-compatible `POST /v1/chat/completions` with JSON validation via **Pydantic** (primary attempt uses `response_format=json_schema`; falls back to `json_object` + schema text for compatibility).
- **Persistence**: SQLite via `sqlite3` stdlib + repositories.
- **Mail ingestion**: read exported `.eml` directories and `.mbox` files; Apple Mail adapter is a stub pending automation-based export.
- **Digest**: primarily **deterministic Markdown** assembled in the application layer from SQLite snapshots (compact, copy/paste friendly). The digest LLM port remains for compatibility/experiments, but the default daily digest path does not depend on it.

Details:

- `docs/adr/0001-initial-architecture.md`
- `docs/adr/0002-review-queue-and-low-memory-workflow.md`

## LM Studio setup on a weak Mac (practical)

- Prefer **smaller** models (this repo defaults to `qwen3-8b`).
- Keep context as low as you can tolerate in LM Studio UI (the app also truncates bodies before calls).
- Use GPU offload when available; if VRAM/unified memory pressure causes swapping, reduce max context and/or model size.
- Keep server local (`LM_STUDIO_BASE_URL` defaults to `http://localhost:1234/v1`).

## Quickstart

### 1) Create a virtualenv (Python 3.12)

```bash
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2) Configure environment

```bash
cp .env.example .env
```

Tune low-memory defaults as needed (see `.env.example`).

### 3) Initialize SQLite schema

```bash
mail-assistant init-db
```

### 4) Ingest mail exports

```bash
mail-assistant ingest-eml --path ./data/inbox_eml
mail-assistant ingest-mbox --path ./data/archive.mbox
```

### 5) Run LLM stages (sequential)

```bash
mail-assistant triage
mail-assistant extract-tasks
```

### 6) Review queue (human-in-the-loop, CLI)

List pending items:

```bash
mail-assistant review-list
```

Approve/reject (id comes from `review-list`):

```bash
mail-assistant review-approve --review-id 12 --note "looks correct"
mail-assistant review-reject --review-id 13 --note "false positive"
```

Export pending reviews to JSON:

```bash
mail-assistant review-export --out ./data/pending_reviews.json
```

**Semantics (MVP):**

- **Triage review approve**: confirms triage and moves the message to `triaged` (eligible for extraction).
- **Triage review reject**: deletes triage and moves the message back to `ingested` for a future re-triage run.
- **Task review approve/reject**: updates extracted task status (`approved` / `rejected`).

### 7) Digest

Write digest to disk and print it:

```bash
mail-assistant build-digest --out ./data/digest.md
```

### 8) One-shot daily pipeline

`run-daily` optionally ingests from paths configured in `.env` (`MAIL_EML_DIR`, `MAIL_MBOX_PATH`), then runs triage → extract → digest.

It prints a **compact stdout summary** (counts + ids). Full digest is only printed by `build-digest` (and can be written via `--digest-out` on `run-daily`).

```bash
mail-assistant run-daily --digest-out ./data/digest.md
```

## Tests

```bash
pytest
```

Tests use **fake/stub LLM ports** (and CLI smoke tests monkeypatch the LM Studio factory), **temporary SQLite files**, and do not require LM Studio or network access.

## launchd (future scheduling)

Example plist: `app/scheduler/launchd/com.local.mailassistant.plist.example`

Typical approach on macOS:

- Install a LaunchAgent pointing at your venv `mail-assistant` entrypoint.
- Provide a stable `WorkingDirectory` and log file paths.

## Restart / catch-up behavior

- **Dedup**: `messages.dedupe_key` is unique; re-ingesting the same export increments duplicates instead of creating new rows.
- **Processing statuses**:
  - `ingested` → (`triaged` **or** `awaiting_review`) → `tasks_extracted`
  - Non-actionable mail is advanced to `tasks_extracted` during triage to avoid blocking the pipeline forever.
  - Low-confidence / high-impact uncertain triage creates a **pending triage review** and sets `awaiting_review` until approved.
- **Tasks**: `extracted_tasks.dedupe_key` is unique; repeated extraction is idempotent.
- **Reviews**: partial unique indexes prevent unbounded duplicates of pending triage/task reviews.
- **Runs**: `pipeline_runs` records start/end metadata for operational debugging.
- **DB upgrades**: opening a DB runs lightweight `upgrade_schema()` (adds missing columns/tables for older local files).

## Stubs / TODO

- `app/infrastructure/mail/apple_mail_adapter.py`: placeholder reader (export/automation-first approach).
- `app/infrastructure/kanban/stub_adapter.py`: Kanban sync is logged-only.
- Richer review UX (filters, bulk actions), richer digest inputs without bloating prompts.

## Next iteration (suggested)

- Apple Mail export automation (Script Editor / Shortcuts) producing `.eml` into `MAIL_EML_DIR`.
- Real Kanban adapter behind `KanbanPort` with explicit rate limits + idempotent external IDs.
- Optional short LLM “executive headline” separate from deterministic digest sections (still one prompt = one task).

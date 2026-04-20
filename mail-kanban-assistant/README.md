# Mail Kanban Assistant (MVP scaffold)

Local-first macOS-oriented assistant that ingests exported mail (`.eml` / `.mbox`) **and** an **Apple Mail JSON snapshot maildrop** (`MAILDROP_ROOT/incoming`), triages it with a **local LLM** served by **LM Studio** (OpenAI-compatible API), extracts candidate tasks, routes uncertain outputs through a **CLI review queue**, and generates a **deterministic daily digest** markdown artifact.

This repository is intentionally **not** a toy script: it is a **Clean Architecture** scaffold with explicit ports, SQLite persistence, structured LLM outputs for triage/tasks, idempotent ingestion, and pytest coverage using fake LLM ports.

## Goals

- **Privacy-first**: no mailbox passwords are collected or stored.
- **Local-first**: primary data stays on disk; LLM calls target a local endpoint by default.
- **Restart-safe**: stable dedupe keys, SQLite constraints, processing statuses, and review dedupe indexes enable catch-up after sleep/crash.

## Non-goals (MVP)

- No direct parsing of undocumented Apple Mail internal databases.
- No hosted multi-tenant SaaS; integrations are explicit, local-first adapters.
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
- **Mail ingestion**: read exported `.eml` directories, `.mbox` files, and **Apple Mail drop snapshots** (validated JSON files) under `MAILDROP_ROOT` (see ADR 0003).
- **Digest**: primarily **deterministic Markdown** assembled in the application layer from SQLite snapshots (compact, copy/paste friendly). The digest LLM port remains for compatibility/experiments, but the default daily digest path does not depend on it.

Details:

- `docs/adr/0001-initial-architecture.md`
- `docs/adr/0002-review-queue-and-low-memory-workflow.md`
- `docs/adr/0003-apple-mail-drop-snapshot-format.md`
- `docs/adr/0004-kanban-sync-outbox.md`
- `docs/adr/0005-yougile-kanban-provider.md`
- `docs/adr/0006-yougile-onboarding-cli.md`

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

### 4b) Apple Mail drop workflow (macOS, local-first)

This path is intentionally **not** “parse Mail’s internal DB” and **not** “store mailbox passwords”. Instead:

**Apple Mail / Shortcuts / JXA → JSON snapshot files → `incoming/` → SQLite → triage / tasks / digest**

1. Create the maildrop directories (idempotent):

```bash
mail-assistant prepare-maildrop --path ./data/maildrop
```

2. Configure macOS automation to write **one JSON file per message** into `./data/maildrop/incoming`.

   - Helper script (JXA): `scripts/apple_mail/save_message_snapshot.js`
   - Before running it, export `MAILDROP_INCOMING` to the **absolute** path of your `incoming/` folder.

3. Ingest snapshots (moves successes to `processed/`, failures to `failed/`):

```bash
mail-assistant ingest-apple-mail-drop --path ./data/maildrop
```

4. Sanity-check the machine + paths + LM Studio reachability (best-effort HTTP probe):

```bash
mail-assistant doctor --repo-root "$(pwd)"
```

**Snapshot JSON contract (strictly validated):** see `app/application/apple_mail_snapshot.py` (`AppleMailDropSnapshotFile`). Minimum practical fields:

- `snapshot_id` (string), `source` must be `"apple_mail_drop"`, `message_id` (string), `body_text` (string), `collected_at` (ISO datetime)
- Optional: `thread_id`, mailbox/account names, recipients arrays, flags, `attachments_summary` (metadata only), `raw_metadata`

**MVP limitations (honest):**

- Apple Mail scripting is **best effort** for some metadata (see comments in `scripts/apple_mail/save_message_snapshot.js`).
- Attachments are **not** ingested as binaries; only optional attachment metadata summaries are supported.

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

The digest includes a short **Kanban sync** section when the DB has sync state (counts by status, recent errors).

### 7b) Kanban sync (approved tasks only)

After tasks reach **`approved`** via the review queue, you can push them to a Kanban provider in an **idempotent** way:

- **`local_file`** (default): writes `task_<id>.json` under `KANBAN_ROOT_DIR/cards/` and can export `board.md`.
- **`yougile`**: creates tasks in a YouGile column via **YouGile REST API v2** (`https://…/api-v2/tasks`) using **`Authorization: Bearer <YOUGILE_API_KEY>`** only (no account password in this app). See `.env.example` and `docs/adr/0005-yougile-kanban-provider.md`.
- **`trello`**: creates cards via the REST API (set `TRELLO_*` in `.env`; see `doctor`).
- **`stub`**: no external side effects (useful for dry runs / tests).

**Why `local_file` stays the default:** it is zero-network, easy to inspect, and safe for experiments. YouGile is opt-in via `KANBAN_PROVIDER=yougile` once you have an API key and column UUIDs.

**YouGile onboarding (recommended)** — see **§7c** below for discovery, env templates, smoke sync on one task, and troubleshooting. Short path: API key → `yougile-discover` → paste ids into `.env` → `yougile-config-check` → `yougile-smoke-sync --task-id …` (dry-run) → `--execute` once → normal `kanban-sync`.

**Idempotency / fingerprint**

- SQLite `kanban_sync_records` stores `card_fingerprint` and `external_card_id` per `(task_id, provider)`.
- Same fingerprint as the last **synced** row → **skip** (no duplicate POST).
- **YouGile:** if the fingerprint **changes** after a successful sync, the safe default is **`YOUGILE_ENABLE_UPDATE_EXISTING=false`**: the run **skips** and marks the row skipped (no silent second task). Set `YOUGILE_ENABLE_UPDATE_EXISTING=true` to **PUT** title/description/deadline/column on the existing YouGile task.
- **local_file:** same path is overwritten — treated as **create** path in code (no duplicate files).

**Rate limits**

YouGile documents **≤ 50 HTTP requests per minute per company**. This integration spaces requests sequentially and defaults `YOUGILE_REQUESTS_PER_MINUTE=40`. There is **no aggressive POST retry** (retries could create duplicate tasks).

Commands:

```bash
mail-assistant kanban-preview
mail-assistant kanban-sync --dry-run
mail-assistant kanban-sync
mail-assistant kanban-status
mail-assistant kanban-status --probe   # YouGile: optional live GET checks
mail-assistant kanban-retry-failed
mail-assistant kanban-export-local

# YouGile onboarding (see §7c)
mail-assistant yougile-discover
mail-assistant yougile-discover --json
mail-assistant yougile-print-env
mail-assistant yougile-doctor
mail-assistant yougile-config-check
mail-assistant yougile-smoke-sync --task-id 42
mail-assistant yougile-smoke-sync --task-id 42 --execute
mail-assistant yougile-cleanup-note
```

`KANBAN_AUTO_SYNC=false` by default: automatic sync on approve / `run-daily` stays opt-in.

### 7c) YouGile workspace onboarding (operational)

Use these **application-layer** commands (they call YouGile read/write APIs only with `YOUGILE_API_KEY`; no password flow).

**Step-by-step**

1. Create an **API key** in YouGile (company settings). Store it in `YOUGILE_API_KEY` (never commit real keys).
2. Set `YOUGILE_BASE_URL` (default `https://ru.yougile.com` is fine for the hosted cloud).
3. Run **`mail-assistant yougile-discover`** (add `--json` for machine-readable output, `--compact` for dense rows). Lists **boards** (`GET /api-v2/boards`) and **columns** (`GET /api-v2/columns`), grouped by `boardId`, so you can copy UUIDs into `.env`.
4. Paste ids into `.env` (`YOUGILE_BOARD_ID`, `YOUGILE_COLUMN_ID_TODO`, optional DONE/BLOCKED). **`mail-assistant yougile-print-env`** prints a ready-to-paste fragment; **`mail-assistant yougile-config-check`** prints the fragment plus **live** API checks.
5. Set **`KANBAN_PROVIDER=yougile`** when you are ready to sync for real (until then you can keep `local_file` and pass `--force` on discover).
6. **`mail-assistant yougile-doctor`** — focused YouGile-only checks (config + `GET /boards`, optional board/column probes). Use **`mail-assistant doctor --yougile-probe`** to append the same live checks to the full environment doctor. Add **`--json`** on `doctor` for JSON lines.
7. **`mail-assistant kanban-status --probe`** — for YouGile, runs a tiny live probe (`/boards`, configured board, TODO column) after the usual SQLite counters.
8. **`mail-assistant yougile-smoke-sync --task-id <internal id>`** — **dry-run by default** (plans with a smoke tag in title/description). Add **`--execute`** for a **single** approved task write. Requires `KANBAN_PROVIDER=yougile`.
9. Normal **`mail-assistant kanban-sync`** for batch work after smoke looks good.

**Smoke cleanup**

There is **no automatic delete/archive** of YouGile tasks from this CLI (avoid destructive API assumptions). Smoke tasks include a searchable **`[mail-assistant-smoke:<task_id>]`** marker. Run **`mail-assistant yougile-cleanup-note`** for manual cleanup steps in the YouGile UI.

**Troubleshooting**

| Symptom | What to check |
|--------|----------------|
| `authentication failed` / HTTP 401 | Regenerate or paste `YOUGILE_API_KEY`; no extra quotes in `.env`. |
| HTTP 403 | Key lacks permission for board/project. |
| Board/column HTTP 404 | Wrong UUID; re-run `yougile-discover` and copy ids again. |
| HTTP 429 | Rate limit — lower `YOUGILE_REQUESTS_PER_MINUTE`, space out syncs. |
| Timeouts / connect errors | Network, firewall, or wrong `YOUGILE_BASE_URL`. |
| `yougile-smoke-sync` “not approved” | Task must be `approved` in SQLite (`review-approve` first). |
| No writes in dry-run | Expected — pass `--execute` only when ready. |

Operational ADR: `docs/adr/0006-yougile-onboarding-cli.md`.

### 8) One-shot daily pipeline

`run-daily` optionally ingests from paths configured in `.env` (`MAIL_EML_DIR`, `MAIL_MBOX_PATH`, **`MAILDROP_ROOT`**), then runs triage → extract → digest.

It prints a **compact stdout summary** (counts + ids). Full digest is only printed by `build-digest` (and can be written via `--digest-out` on `run-daily`).

```bash
mail-assistant run-daily --digest-out ./data/digest.md
```

## Tests

```bash
pytest
```

Tests use **fake/stub LLM ports** (and CLI smoke tests monkeypatch the LM Studio factory), **temporary SQLite files**, and do not require LM Studio or network access.

## launchd (macOS scheduling)

Use the generator commands so you do not hand-edit fragile paths:

```bash
mail-assistant print-launchd --repo-root "$(pwd)" --digest-out "$(pwd)/data/digest.md"
mail-assistant install-launchd --output ~/Library/LaunchAgents/com.local.mailassistant.plist --repo-root "$(pwd)"
```

The generated plist runs `scripts/macos/run-mail-assistant-daily.sh`, which executes the venv Python with an explicit working directory (no interactive shell assumptions).

**Wrapper environment variables:**

- `MAIL_KANBAN_REPO_ROOT` (required): repository root
- `MAIL_KANBAN_VENV_PYTHON` (optional): defaults to `$MAIL_KANBAN_REPO_ROOT/.venv/bin/python`
- `MAIL_KANBAN_DIGEST_OUT` (optional): digest output path for `run-daily`
- `MAILDROP_ROOT` (optional): passed through to ingestion defaults

**launchctl (user session agent):**

```bash
mkdir -p ~/Library/Logs/mail-assistant
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.local.mailassistant.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.mailassistant.plist
```

Unload/restart:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.local.mailassistant.plist
```

Historical reference template: `app/scheduler/launchd/com.local.mailassistant.plist.example` (prefer `print-launchd` for real paths).

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
- **Apple Mail drop artifacts**: `ingested_artifacts` tracks `content_hash` + `snapshot_id` + status (`pending` / `processed` / `failed`) so restarts and backlog runs do not double-ingest the same snapshot file.

## Recommended daily workflow (Mac)

- Keep LM Studio running locally (or accept `doctor` warnings).
- Let automation drop new JSON snapshots into `MAILDROP_ROOT/incoming` throughout the day.
- On a schedule (launchd) or manually, run `mail-assistant run-daily --digest-out ./data/digest.md`.
  - This ingests **EML/MBOX (if configured)** **and always attempts the maildrop** (empty `incoming/` is cheap).
  - If the machine slept: the next run processes the accumulated backlog idempotently.

## Stubs / TODO

- `app/infrastructure/mail/apple_mail_adapter.py`: legacy placeholder reader (kept for older “export to `.eml`” experiments). Prefer **`ingest-apple-mail-drop`** for the supported Apple Mail path.
- Richer review UX (filters, bulk actions), richer digest inputs without bloating prompts.

## Next iteration (suggested)

- Richer Mail automation templates (bulk selection helpers, safer HTML→text extraction) while keeping the JSON snapshot contract stable.
- More Kanban providers behind `KanbanPort` (rate limits, richer field mapping) without changing the sync outbox contract.
- Optional short LLM “executive headline” separate from deterministic digest sections (still one prompt = one task).

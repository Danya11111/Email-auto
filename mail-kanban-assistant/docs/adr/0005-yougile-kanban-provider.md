# ADR 0005: YouGile Kanban provider (REST v2, Bearer key)

## Status

Accepted

## Context

Users want **ru.yougile.com** (or compatible host) as an external board while keeping the existing **SQLite outbox** (`kanban_sync_records` + fingerprint) and **approved-only** sync pipeline.

Constraints from YouGile:

- Auth: `Authorization: Bearer <API key>` (key created in product UI; **no** password storage in this repository).
- Rate: about **50 requests/minute/company** — integrations must avoid burst POST retries.

## Decision

- Add `KanbanProvider.YOUGILE` and `YougileKanbanAdapter` using **httpx** against `{YOUGILE_BASE_URL}/api-v2/...` (append `/api-v2` when the base URL is only a host).
- **Create**: `POST /tasks` with `title`, `columnId`, `description`, optional `deadline` object.
- **Update** (optional): `PUT /tasks/{id}` with the same safe fields, only when `YOUGILE_ENABLE_UPDATE_EXISTING=true` and the outbox policy selects `UPDATE_EXISTING`.
- **Fingerprint resync policy** (`plan_kanban_outbound`):
  - **Default YouGile** after a successful sync: if fingerprint changes, **do not** POST a second task; mark **skipped** with a clear reason unless updates are explicitly enabled.
  - **Failed rows** with a stored `external_card_id` and the **same** fingerprint: prefer **PUT** resume (avoids duplicate tasks when retrying a failed update).
  - **local_file**: unchanged — JSON file is overwritten via the existing create path.
- **Throttling**: simple sequential spacing (`60 / YOUGILE_REQUESTS_PER_MINUTE` seconds, capped at 50 rpm) before each HTTP call. **No automatic POST retry** on errors.

## Consequences

- Priority is reflected in the **description** text; optional string-sticker mapping env vars are reserved for a future iteration once we pin a stable JSON shape from live API responses.
- Trello behaviour on fingerprint change remains “create again” (legacy); YouGile is stricter by default.

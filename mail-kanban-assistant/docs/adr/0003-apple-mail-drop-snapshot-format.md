# ADR 0003: Apple Mail integration via JSON snapshot drop folder

## Status

Accepted (MVP)

## Context

We need a **local-first**, **privacy-preserving** daily workflow on macOS that does **not**:

- store mailbox passwords in this system, or
- read undocumented Apple Mail internal databases, or
- depend on fragile “magic” automation that pretends to be a full IMAP client.

At the same time, users need a practical path from **Apple Mail → assistant** that survives restarts and supports backlog catch-up.

## Decision

Integrate Apple Mail through a **drop folder** containing **strictly validated JSON snapshot files** (one file per message).

The assistant ingests snapshots from `MAILDROP_ROOT/incoming`, persists normalized rows into SQLite using existing dedupe rules, and moves files to `processed/` or `failed/`.

A companion SQLite table `ingested_artifacts` tracks per-file **content hash**, optional `snapshot_id`, and lifecycle status for **restart safety** and **artifact-level dedupe**.

macOS automation (JXA / rules / Script Editor) is responsible for producing snapshots; the Python core remains portable and testable without Apple Mail installed.

## Consequences

### Positive

- Deterministic ingestion and idempotency at two layers: **message dedupe_key** + **artifact content_hash**.
- Clear operational boundaries: broken snapshots do not block the rest of the batch.
- Easy testing: drop JSON fixtures into a temp directory and run the use case / CLI.

### Negative / limits

- Not all Mail metadata is reliably exposed via scripting; some fields are **best effort** or synthetic (documented in README + script comments).
- Attachments are not extracted in MVP; only optional metadata summaries are supported.

## Alternatives considered

- **Direct `.eml` export from Mail**: still valid as a parallel path, but harder to automate reliably without heavier scripting; snapshots are easier to validate and move atomically.
- **Reading Mail’s internal DB**: rejected (fragile + privacy-sensitive + undocumented).

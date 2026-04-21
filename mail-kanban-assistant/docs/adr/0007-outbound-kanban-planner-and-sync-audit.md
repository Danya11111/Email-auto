# ADR 0007: Outbound Kanban planner and sync audit fields

## Status

Accepted

## Context

YouGile daily operations need a single, testable decision point for outbound writes (create vs update vs skip vs configuration failures) and lightweight audit visibility without turning SQLite into an event store.

## Decision

1. Introduce `app/application/outbound_kanban_planner.py` with `OutboundKanbanAction` + `plan_outbound_kanban_action` / `plan_resync_changed_action` as the only planners for outbound decisions.
2. Extend `kanban_sync_records` with nullable audit columns (`last_outbound_action`, `last_operation_note`, `previous_fingerprint`, `previous_external_card_url`, `record_updated_at`) via idempotent `PRAGMA`-driven migrations.
3. Keep local pipeline + sync rows as source of truth; fingerprint drift on SYNCED rows records `skip_manual_resync` via audit fields **without** flipping `sync_status` away from `synced`.

## Consequences

- Call sites must pass `task_status` into the compatibility wrapper `plan_kanban_outbound` when behaviour depends on approval.
- Digest/status queries can surface compact operational metrics derived from audit columns.

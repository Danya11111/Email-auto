# ADR 0007: Thread-aware Action Center as a separate decision layer

## Status

Accepted (2026-04)

## Context

Per-message triage and task extraction already existed, but daily operations need:

- fewer duplicate “noisy” items across long threads,
- explicit reply posture hints (waiting on us / them / overdue / ambiguous),
- a compact **Action Center** artifact for digest + CLI,
- explainability without dumping raw JSON.

Heavy multi-call LLM orchestration is incompatible with the **low-memory** constraint (8GB-class machines).

## Decision

Introduce a **pure application-layer** pipeline:

1. Load a bounded SQLite window (`ActionCenterRawBundleDTO`).
2. **Deterministic thread clustering** (`thread_grouping.py`): prefer `thread_hint`; else normalized subject + primary sender + time window merge. Each cluster gets a stable synthetic `thread_id`.
3. Aggregate triage fields per thread (max importance, max reply requirement, any actionable).
4. Compute **`ReplyState`** and **`ThreadActionState`** via small rule modules (`reply_state_rules.py`, `thread_action_state_rules.py`).
5. Build **`ActionCenterSnapshotDTO`** with **one primary thread row** per cluster (no per-message duplicate action items for the same conversation).
6. Attach optional global rows (orphan reviews, failed sync pins, approved-ready / manual-resync aggregates).
7. Feed snapshot into **digest markdown** (executive lines + grouped sections) and **CLI** (`action-center`, `explain-*`).

**No new SQLite tables** for snapshots in MVP: the snapshot is computed on demand from existing SOT tables.

Optional `ACTION_CENTER_USE_LLM_EXECUTIVE_SUMMARY` remains **off** by default; deterministic bullets stay the contract.

## Consequences

- **Pros**: testable heuristics, idempotent SQL reads, low RAM, clear explain text, minimal coupling to Kanban providers.
- **Cons / limits**: without RFC `thread_id`, clustering is **best-effort**; same normalized subject from different real threads may still collide if participants and timing align — mitigate via `thread_hint` from Apple Mail drop and conservative windows.

## Related

- `app/application/action_center_engine.py`
- `app/application/thread_grouping.py`
- `app/application/reply_state_rules.py`

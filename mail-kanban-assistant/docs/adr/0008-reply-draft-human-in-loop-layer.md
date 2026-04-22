# ADR 0008: Reply draft workflow as a separate human-in-the-loop artifact layer

## Status

Accepted (2026-04-22)

## Context

The system already exposes deterministic **reply posture** (`ReplyState`) and Action Center guidance, but operators still need a **local, reviewable artifact** that turns “you should reply” into **draftable text** without inventing facts and without any mail send path.

## Decision

Introduce a **Reply Draft Center** as an explicit SQLite-backed artifact (`reply_drafts`) with:

- **Domain enums + errors** for lifecycle (`ReplyDraftStatus`, `ReplyTone`, `ReplyDraftGenerationMode`) and typed `ReplyDraft`.
- **Application ports** (`ReplyDraftRepositoryPort`, `ReplyDraftLLMPort`, `ReplyContextBuilderPort`, `ReplyDraftExporterPort`, `ReplyDraftActionCenterEnricherPort`) so CLI stays thin.
- **Deterministic fingerprinting** over a bounded context DTO (ids + triage pins + structured bullets), separate from raw bodies, to detect **stale** drafts when threads evolve.
- **On-demand structured LLM generation** only via `reply-draft-*` CLI (same LM Studio gateway, new JSON schema `reply_draft`).
- **Digest / Action Center integration** via `SqliteReplyDraftActionCenterEnricher` wired in `build_wiring`, without automatic draft generation for all mail.

## Consequences

- **Positive**: auditable drafts, export path for Apple Mail handoff, idempotent reuse when fingerprint unchanged, conservative defaults (`REPLY_DRAFT_REQUIRE_APPROVAL_BEFORE_EXPORT=true`).
- **Trade-off**: thread ids remain **heuristic**; draft context is **bounded** and may omit nuance—operators must treat drafts as starting points.
- **Compatibility**: `populate_by_name=True` on `AppSettings` so tests and library callers can override settings fields by Python name while env vars continue to use `REPLY_*` aliases.

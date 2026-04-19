# ADR 0002: Review queue + low-memory local workflow

## Status

Accepted (2026-04-19)

## Context

The first iteration proved the architecture, but daily usage on a constrained Mac (8GB unified memory) needs:

- smaller default models and safer prompt sizing,
- explicit human review for uncertain triage/tasks without a GUI,
- digest output that is reliably readable and copy/paste friendly even when the LLM is flaky.

## Decision

### Default model + inference ergonomics

Default LM Studio model is set to **`qwen3-8b`** to reduce memory pressure versus larger defaults.

The application enforces **sequential** LLM usage in MVP (no background pools) and applies **deterministic input shrinking** via `LlmTextPolicy` + `prepare_body_for_llm()` before triage/task extraction calls.

HTTP calls include `max_tokens` to cap output size.

### Human review queue (CLI-first)

Introduce `review_items` in SQLite with:

- partial unique indexes to prevent duplicate **pending** triage/task reviews,
- explicit approve/reject transitions implemented as application use cases,
- triage reject behavior that deletes triage and returns the message to `ingested` for a safe retry path.

### Digest composition

The daily digest is primarily assembled as **deterministic Markdown** from SQLite snapshots (messages + triage join + candidate tasks + pending reviews + pipeline stats), instead of relying on a large LLM “write me a digest” prompt.

This improves reliability on weak hardware and reduces token burn.

## Consequences

### Positive

- Predictable daily artifact for humans.
- Review workflow is testable without LM Studio.
- Idempotent review enqueue reduces queue spam on reruns.

### Trade-offs

- Digest prose is less “creative” than a fully generative digest (by design).
- Triage reject deletes model output (intentional reset for re-triage).

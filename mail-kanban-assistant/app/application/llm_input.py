from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import MessageBodyTruncateStrategy


@dataclass(frozen=True, slots=True)
class LlmTextPolicy:
    """Centralized limits for shrinking text before local LLM calls."""

    max_input_chars: int
    truncate_strategy: MessageBodyTruncateStrategy
    head_tail_tail_chars: int = 800


def prepare_body_for_llm(text: str, policy: LlmTextPolicy) -> str:
    """Deterministic, memory-safe body shrinking for LLM prompts."""

    cleaned = text.replace("\r\n", "\n").strip()
    max_chars = max(256, int(policy.max_input_chars))
    if len(cleaned) <= max_chars:
        return cleaned

    strategy = policy.truncate_strategy
    if strategy == MessageBodyTruncateStrategy.HEAD:
        return cleaned[:max_chars]

    if strategy == MessageBodyTruncateStrategy.HEAD_TAIL:
        tail = max(64, min(policy.head_tail_tail_chars, max_chars // 3))
        head = max_chars - tail - 8
        head = max(256, head)
        return f"{cleaned[:head]}\n\n[...snip...]\n\n{cleaned[-tail:]}"

    # middle_snip: keep start + end chunks for threading cues without huge middle
    head = max_chars // 2
    tail = max_chars - head - 16
    tail = max(256, tail)
    return f"{cleaned[:head]}\n\n[...snip middle...]\n\n{cleaned[-tail:]}"

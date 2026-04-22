from __future__ import annotations

import re

_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:re|fw|fwd|ответ|пересл|переслано)\s*:\s*)+",
    flags=re.IGNORECASE,
)


def normalize_subject(subject: str | None) -> str:
    """Strip common reply/forward prefixes; collapse whitespace (deterministic)."""
    if subject is None:
        return ""
    s = str(subject).strip()
    prev = None
    while prev != s:
        prev = s
        s = _SUBJECT_PREFIX_RE.sub("", s).strip()
    return " ".join(s.split())

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Iterable

from app.application.dtos import ActionCenterMessageRowDTO
from app.application.thread_subject import normalize_subject


def _primary_party(sender: str | None) -> str:
    if not sender:
        return "unknown"
    s = sender.strip().lower()
    if "<" in s and ">" in s:
        start = s.find("<") + 1
        end = s.find(">", start)
        if end > start:
            s = s[start:end].strip()
    return s or "unknown"


def grouping_key(msg: ActionCenterMessageRowDTO) -> str:
    hint = (msg.thread_hint or "").strip()
    if hint:
        return f"hint:{hint}"
    subj = normalize_subject(msg.subject)
    party = _primary_party(msg.sender)
    return f"heur:{subj}|from:{party}"


def _stable_thread_id(*, key: str, anchor_message_id: int) -> str:
    """Stable id per cluster; hint-based clusters that do not merge still need distinct ids."""
    if key.startswith("hint:"):
        digest = hashlib.sha256(f"{key}|{anchor_message_id}".encode("utf-8")).hexdigest()[:14]
        return f"t-hint-{digest}"
    digest = hashlib.sha256(f"{key}|{anchor_message_id}".encode("utf-8")).hexdigest()[:14]
    return f"t-heur-{digest}"


def cluster_messages_into_threads(
    messages: Iterable[ActionCenterMessageRowDTO],
    *,
    time_window: timedelta,
) -> dict[str, tuple[int, ...]]:
    """
    Deterministic thread clustering.
    Messages sharing the same grouping_key merge only if consecutive in time order
    with gaps <= time_window (sorted ascending by received_at).
    """
    msgs = sorted(
        messages,
        key=lambda m: (m.received_at or datetime.min.replace(tzinfo=UTC), m.message_id),
    )
    clusters: list[list[ActionCenterMessageRowDTO]] = []
    for m in msgs:
        k = grouping_key(m)
        placed = False
        for cluster in reversed(clusters):
            anchor = cluster[0]
            if grouping_key(anchor) != k:
                continue
            last = cluster[-1]
            t_new = m.received_at or datetime.min.replace(tzinfo=UTC)
            t_last = last.received_at or datetime.min.replace(tzinfo=UTC)
            if abs((t_new - t_last).total_seconds()) <= time_window.total_seconds():
                cluster.append(m)
                placed = True
                break
        if not placed:
            clusters.append([m])

    out: dict[str, tuple[int, ...]] = {}
    for cluster in clusters:
        anchor_id = cluster[0].message_id
        tid = _stable_thread_id(key=grouping_key(cluster[0]), anchor_message_id=anchor_id)
        out[tid] = tuple(x.message_id for x in sorted(cluster, key=lambda z: z.message_id))
    return out

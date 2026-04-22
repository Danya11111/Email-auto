from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DigestComposeOptions:
    """Rendering controls for morning digest markdown (deterministic layout)."""

    compact: bool = False
    include_informational: bool = False

from __future__ import annotations

from urllib.parse import urljoin


def lm_studio_models_probe_url(lm_studio_base_url: str) -> str:
    """HTTP URL used for a lightweight LM Studio reachability check (OpenAI-compatible /v1/models)."""

    base = lm_studio_base_url.strip().rstrip("/") + "/"
    return urljoin(base, "models")

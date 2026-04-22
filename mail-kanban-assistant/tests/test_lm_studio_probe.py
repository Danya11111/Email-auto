from __future__ import annotations

from app.application.lm_studio_probe import lm_studio_models_probe_url


def test_lm_studio_models_probe_url_default_shape() -> None:
    assert lm_studio_models_probe_url("http://localhost:1234/v1") == "http://localhost:1234/v1/models"


def test_lm_studio_models_probe_url_trims_whitespace_and_trailing_slash() -> None:
    assert lm_studio_models_probe_url("  http://127.0.0.1:9999/v1/  ") == "http://127.0.0.1:9999/v1/models"


def test_lm_studio_models_probe_url_non_v1_base() -> None:
    assert lm_studio_models_probe_url("http://host/openai") == "http://host/openai/models"

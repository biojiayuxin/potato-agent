from __future__ import annotations

from pathlib import Path

from interface.hermes_service import build_config_data
from interface.mapping import HermesTarget


def _target() -> HermesTarget:
    return HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=Path("/home/hmx_alice"),
        hermes_home=Path("/home/hmx_alice/.hermes"),
        workdir=Path("/home/hmx_alice/work"),
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )


def test_build_config_data_emits_single_fallback_model_at_top_level() -> None:
    fallback = {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4",
        "api_key": "sk-fallback",
    }

    data = build_config_data({"hermes": {"fallback_model": fallback}}, _target())

    assert data["fallback_model"] == fallback
    assert "fallback_providers" not in data


def test_build_config_data_passes_standard_fallback_providers() -> None:
    fallbacks = [
        {
            "provider": "custom",
            "model": "gpt-5.4",
            "base_url": "https://backup.example/v1",
            "api_key": "sk-fallback",
        }
    ]

    data = build_config_data({"hermes": {"fallback_providers": fallbacks}}, _target())

    assert data["fallback_providers"] == fallbacks
    assert "fallback_model" not in data


def test_build_config_data_does_not_normalize_legacy_fallback_model_list() -> None:
    fallbacks = [
        {
            "provider": "custom",
            "model": "gpt-5.4",
            "base_url": "https://backup.example/v1",
        }
    ]

    data = build_config_data({"hermes": {"fallback_model": fallbacks}}, _target())

    assert data["fallback_model"] == fallbacks
    assert "fallback_providers" not in data


def test_build_config_data_passes_both_standard_fallback_fields() -> None:
    fallback_model = {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4",
    }
    fallback_providers = [
        {
            "provider": "custom",
            "model": "gpt-5.4",
            "base_url": "https://backup.example/v1",
        }
    ]

    data = build_config_data(
        {
            "hermes": {
                "fallback_model": fallback_model,
                "fallback_providers": fallback_providers,
            }
        },
        _target(),
    )

    assert data["fallback_providers"] == fallback_providers
    assert data["fallback_model"] == fallback_model

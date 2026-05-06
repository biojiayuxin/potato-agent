from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from interface.hermes_service import build_config_data, install_user_runtime_files
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


def test_install_user_runtime_files_writes_only_user_runtime_paths(
    monkeypatch, tmp_path
) -> None:
    user = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=tmp_path / "home",
        hermes_home=tmp_path / "home" / ".hermes",
        workdir=tmp_path / "home" / "work",
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-runtime-only-test.service",
        extra_env={"OPENAI_API_KEY": "sk-user"},
        config_overrides={},
    )
    touched_paths: list[Path] = []

    monkeypatch.setattr(
        "interface.hermes_service.pwd.getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        "interface.hermes_service._set_owner_and_mode",
        lambda path, uid, gid, mode: touched_paths.append(path),
    )
    monkeypatch.setattr(
        "interface.hermes_service._run_command",
        lambda command: (_ for _ in ()).throw(AssertionError(command)),
    )

    install_user_runtime_files(
        {
            "hermes": {
                "model": {
                    "provider": "custom",
                    "default": "gpt-5.5",
                    "base_url": "https://primary.example/v1",
                    "api_key": "sk-user",
                }
            }
        },
        user,
    )

    assert (user.hermes_home / ".env").read_text(encoding="utf-8")
    assert (user.hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert touched_paths
    assert all(tmp_path in path.parents or path == tmp_path for path in touched_paths)

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from interface.mapping import HermesTarget
from interface.model_options import (
    ModelOptionsError,
    normalize_model_options,
    patch_user_active_model,
)


def _target(tmp_path: Path) -> HermesTarget:
    return HermesTarget(
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
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )


def test_normalize_legacy_single_model_config() -> None:
    options = normalize_model_options(
        {
            "hermes": {
                "model": {
                    "default": "gpt-5.4",
                    "provider": "custom",
                    "base_url": "https://primary.example/v1",
                    "api_key": "sk-primary",
                }
            }
        }
    )

    assert options.primary_id == "gpt-5.4"
    assert [option.id for option in options.options] == ["gpt-5.4"]
    assert options.primary.api_mode == "codex_responses"
    assert options.primary.reasoning_effort == "xhigh"


def test_normalize_new_model_options() -> None:
    options = normalize_model_options(
        {
            "hermes": {
                "model_options": {
                    "primary": "primary",
                    "options": [
                        {
                            "id": "primary",
                            "name": "Main",
                            "provider": "custom",
                            "model": "gpt-5.4",
                            "base_url": "https://primary.example/v1",
                            "api_key": "sk-primary",
                        },
                        {
                            "id": "fast",
                            "model": "gpt-5.4-mini",
                            "base_url": "https://fast.example/v1",
                            "api_key": "sk-fast",
                            "context_length": "500,000",
                        },
                    ],
                }
            }
        }
    )

    assert options.primary.model == "gpt-5.4"
    assert options.get("fast").context_length == 500000
    assert options.get("fast").api_mode == "codex_responses"
    assert options.get("fast").reasoning_effort == "xhigh"


def test_normalize_rejects_invalid_model_options() -> None:
    base = {
        "hermes": {
            "model_options": {
                "primary": "primary",
                "options": [
                    {
                        "id": "primary",
                        "model": "gpt-5.4",
                        "base_url": "https://primary.example/v1",
                        "api_key": "sk-primary",
                    }
                ],
            }
        }
    }

    too_many = yaml.safe_load(yaml.safe_dump(base))
    too_many["hermes"]["model_options"]["options"].extend(
        [
            {
                "id": "one",
                "model": "one",
                "base_url": "https://one.example/v1",
                "api_key": "sk-one",
            },
            {
                "id": "two",
                "model": "two",
                "base_url": "https://two.example/v1",
                "api_key": "sk-two",
            },
            {
                "id": "three",
                "model": "three",
                "base_url": "https://three.example/v1",
                "api_key": "sk-three",
            },
        ]
    )
    with pytest.raises(ModelOptionsError, match="at most 3"):
        normalize_model_options(too_many)

    missing = yaml.safe_load(yaml.safe_dump(base))
    missing["hermes"]["model_options"]["options"][0].pop("api_key")
    with pytest.raises(ModelOptionsError, match="api_key"):
        normalize_model_options(missing)

    duplicate = yaml.safe_load(yaml.safe_dump(base))
    duplicate["hermes"]["model_options"]["options"].append(
        {
            "id": "primary",
            "model": "other",
            "base_url": "https://other.example/v1",
            "api_key": "sk-other",
        }
    )
    with pytest.raises(ModelOptionsError, match="Duplicate"):
        normalize_model_options(duplicate)


def test_patch_user_active_model_updates_model_and_env(monkeypatch, tmp_path) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    config_path = target.hermes_home / "config.yaml"
    config_path.write_text(
        """
model:
  default: old-model
  provider: custom
  base_url: https://old.example/v1
  api_key: sk-old
terminal:
  backend: local
agent:
  reasoning_effort: xhigh
display:
  compact: true
fallback_providers:
  - provider: custom
    model: keep-fallback
    base_url: https://fallback.example/v1
    api_key: sk-fallback
""".lstrip(),
        encoding="utf-8",
    )
    (target.hermes_home / ".env").write_text(
        "FOO=bar\nOPENAI_API_KEY=sk-old\n", encoding="utf-8"
    )
    options = normalize_model_options(
        {
            "hermes": {
                "model_options": {
                    "primary": "primary",
                    "options": [
                        {
                            "id": "primary",
                            "model": "gpt-5.4",
                            "base_url": "https://primary.example/v1",
                            "api_key": "sk-primary",
                        },
                        {
                            "id": "fast",
                            "name": "Fast",
                            "provider": "custom",
                            "model": "gpt-5.4-mini",
                            "base_url": "https://fast.example/v1",
                            "api_key": "sk-fast",
                            "context_length": 500000,
                            "api_mode": "chat_completions",
                        },
                    ],
                }
            }
        }
    )

    monkeypatch.setattr(
        "interface.model_options.pwd.getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        "interface.model_options._set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )
    monkeypatch.setattr("interface.model_options.atomic_yaml_write", None)

    patch_user_active_model(target, options.get("fast"))

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["model"] == {
        "default": "gpt-5.4-mini",
        "provider": "custom",
        "base_url": "https://fast.example/v1",
        "api_key": "sk-fast",
        "api_mode": "chat_completions",
        "context_length": 500000,
    }
    assert data["auxiliary"]["compression"]["context_length"] == 500000
    assert data["terminal"] == {"backend": "local"}
    assert data["agent"] == {"reasoning_effort": "xhigh"}
    assert data["display"] == {"compact": True}
    assert data["fallback_providers"][0]["model"] == "keep-fallback"
    assert (target.hermes_home / ".env").read_text(encoding="utf-8") == (
        "FOO=bar\nOPENAI_API_KEY=sk-fast\n"
    )


def test_patch_user_active_model_clears_stale_context_length(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    config_path = target.hermes_home / "config.yaml"
    config_path.write_text(
        """
model:
  default: old-model
  provider: custom
  base_url: https://old.example/v1
  api_key: sk-old
  context_length: 800000
auxiliary:
  compression:
    context_length: 800000
  summarizer:
    model: summary
""".lstrip(),
        encoding="utf-8",
    )
    options = normalize_model_options(
        {
            "hermes": {
                "model_options": {
                    "primary": "primary",
                    "options": [
                        {
                            "id": "primary",
                            "model": "gpt-5.4",
                            "base_url": "https://primary.example/v1",
                            "api_key": "sk-primary",
                        }
                    ],
                }
            }
        }
    )

    monkeypatch.setattr(
        "interface.model_options.pwd.getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        "interface.model_options._set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )
    monkeypatch.setattr("interface.model_options.atomic_yaml_write", None)

    patch_user_active_model(target, options.primary)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["model"]["api_mode"] == "codex_responses"
    assert "context_length" not in data["model"]
    assert "context_length" not in data["auxiliary"]["compression"]
    assert data["auxiliary"]["summarizer"] == {"model": "summary"}
    assert data["agent"] == {"reasoning_effort": "xhigh"}

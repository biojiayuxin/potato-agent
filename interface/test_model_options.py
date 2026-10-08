from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from interface.mapping import HermesTarget
from interface.model_catalog import public_catalog
from interface.test_model_support import make_catalog, install_catalog
from interface.model_options import (
    ModelOptionsError,
    normalize_model_options,
    model_options_from_catalog,
    repair_user_model_config,
)


@pytest.mark.parametrize("hermes", [{}, {"model": {}}, {"model_options": {}}, {"model_catalog": True, "model": {}}])
def test_legacy_model_mapping_is_rejected(hermes):
    with pytest.raises(ModelOptionsError, match="schema-v2 model catalog"):
        normalize_model_options({"hermes": hermes})


def test_catalog_controls_defaults_and_exposes_no_private_fields(monkeypatch):
    catalog = make_catalog()
    catalog["default_option_id"] = "deep"
    install_catalog(monkeypatch, catalog)
    options = normalize_model_options({"hermes": {"model_catalog": True}})
    assert options.new_user_default.id == "deep"
    assert options.get("old-fast") is None
    assert not hasattr(options.primary, "api_key")
    assert not hasattr(options.primary, "base_url")
    assert "SECRET" not in str(options)
    assert "example/v1" not in str(options)


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
        model_proxy_token="pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz",
    )


def test_repair_user_model_config_updates_model_to_proxy_and_scrubs_env(monkeypatch, tmp_path) -> None:
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
    catalog = make_catalog()
    catalog["options"]["fast"].update(context_length=500000, reasoning_effort="xhigh")
    options = model_options_from_catalog(public_catalog(catalog))

    monkeypatch.setattr(
        "interface.model_options.pwd.getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )

    repair_user_model_config(target, options.get("fast"))

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["model"] == {
        "default": "fast",
        "provider": "custom",
        "base_url": "http://127.0.0.1:8765/v1",
        "api_key": "pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz",
        "api_mode": "chat_completions",
        "context_length": 500000,
    }
    assert data["auxiliary"]["compression"]["context_length"] == 500000
    assert data["terminal"] == {"backend": "local"}
    assert data["agent"]["reasoning_effort"] == "xhigh"
    assert "clarify" in data["agent"]["disabled_toolsets"]
    assert data["platform_toolsets"]["cli"][-1] == "no_mcp"
    assert data["display"] == {"compact": True}
    assert "fallback_providers" not in data
    assert (target.hermes_home / ".env").read_text(encoding="utf-8") == "FOO=bar\n"

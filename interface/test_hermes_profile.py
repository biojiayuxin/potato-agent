from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from interface.hermes_profile import (
    DEFAULT_ACTIVATION_RUNTIME_PROFILE_PATH,
    DEFAULT_AGENT_BROWSER_BIN_DIR,
    DEFAULT_AGENT_BROWSER_EXECUTABLE,
    DEFAULT_BUNDLED_SKILLS_PATH,
    DEFAULT_OPTIONAL_SKILLS_PATH,
    DEFAULT_RUNTIME_PROFILE_PATH,
    HermesRuntimeProfileError,
    apply_runtime_profile,
    bundled_plugin_disable_keys,
    load_runtime_profile,
    local_browser_cdp_url,
    parse_runtime_profile,
    runtime_profile_environment,
)


def _raw_profile() -> dict:
    return yaml.safe_load(DEFAULT_RUNTIME_PROFILE_PATH.read_text(encoding="utf-8"))


def test_runtime_profile_loads_the_versioned_potato_contract() -> None:
    profile = load_runtime_profile()

    assert profile.schema_version == 1
    assert profile.name == "potato"
    assert len(profile.expected_tools) == 27
    assert profile.model_providers == ("custom",)
    assert profile.mcp_enabled is False
    assert profile.allow_lazy_installs is False


def test_runtime_profile_rejects_unknown_fields_and_unsafe_policy() -> None:
    unknown = _raw_profile()
    unknown["unexpected"] = True
    with pytest.raises(HermesRuntimeProfileError, match="unknown unexpected"):
        parse_runtime_profile(unknown)

    unsafe = _raw_profile()
    unsafe["runtime"]["allow_lazy_installs"] = True
    with pytest.raises(HermesRuntimeProfileError, match="allow_lazy_installs"):
        parse_runtime_profile(unsafe)

    expanded_toolsets = _raw_profile()
    expanded_toolsets["toolsets"]["enabled"].append("web")
    with pytest.raises(HermesRuntimeProfileError, match="toolsets.enabled"):
        parse_runtime_profile(expanded_toolsets)

    expanded_tools = _raw_profile()
    expanded_tools["expected_tools"][-1] = "web_search"
    with pytest.raises(HermesRuntimeProfileError, match="expected_tools"):
        parse_runtime_profile(expanded_tools)


def test_bundled_plugin_disable_keys_match_plugin_manager_key_semantics() -> None:
    keys = set(bundled_plugin_disable_keys(load_runtime_profile()))

    assert keys == set()


def test_apply_runtime_profile_is_idempotent_and_user_can_only_close_more() -> None:
    existing = {
        "model": {"provider": "openrouter", "api_mode": "anthropic_messages"},
        "fallback_model": {"provider": "openrouter", "model": "fallback"},
        "fallback_providers": [{"provider": "nous", "model": "fallback"}],
        "auxiliary": {
            "compression": {
                "provider": "openrouter",
                "model": "summary-model",
                "base_url": "https://other.example/v1",
                "api_key": "secret",
                "api_mode": "anthropic_messages",
                "fallback_chain": [{"provider": "nous"}],
                "timeout": 45,
                "extra_body": {"reasoning": {"effort": "low"}},
            }
        },
        "platform_toolsets": {"cli": ["hermes-cli"], "cron": ["todo"]},
        "agent": {"disabled_toolsets": ["memory"]},
        "security": {"allow_lazy_installs": True, "other": "preserved"},
        "lsp": {"enabled": False, "install_strategy": "auto"},
        "terminal": {"backend": "docker", "timeout": 123},
        "browser": {
            "cloud_provider": "browser-use",
            "cdp_url": "http://remote.example:9222",
            "engine": "lightpanda",
        },
        "memory": {"provider": "honcho", "memory_enabled": True},
        "context": {"engine": "lcm"},
        "gateway": {
            "platforms": {"telegram": {"enabled": True}},
            "bind": "127.0.0.1",
        },
        "web": {"backend": "firecrawl"},
        "mcp_servers": {"external": {"command": "server"}},
        "plugins": {"enabled": ["example"], "disabled": ["extra-disabled"]},
        "unmanaged": {"value": 7},
    }

    applied = apply_runtime_profile(existing)
    reapplied = apply_runtime_profile(applied)

    profile = load_runtime_profile()
    assert applied == reapplied
    assert applied["platform_toolsets"]["cli"] == [
        *profile.enabled_toolsets,
        "no_mcp",
    ]
    assert applied["platform_toolsets"]["cron"] == ["todo"]
    assert applied["agent"]["disabled_toolsets"][0] == "memory"
    assert set(profile.disabled_toolsets).issubset(
        applied["agent"]["disabled_toolsets"]
    )
    assert applied["security"] == {
        "allow_lazy_installs": False,
        "other": "preserved",
    }
    assert applied["lsp"] == {"enabled": False, "install_strategy": "manual"}
    assert applied["terminal"] == {
        "backend": "local",
        "timeout": 123,
    }
    assert applied["browser"] == {
        "cloud_provider": "local",
        "cdp_url": "",
        "engine": "chrome",
    }
    assert applied["memory"] == {"provider": "", "memory_enabled": True}
    assert applied["context"]["engine"] == "compressor"
    assert applied["gateway"] == {
        "platforms": {},
        "bind": "127.0.0.1",
    }
    assert applied["web"] == {
        "backend": "",
        "search_backend": "",
        "extract_backend": "",
    }
    assert applied["model"] == {
        "provider": "custom",
        "api_mode": "codex_responses",
    }
    assert "fallback_model" not in applied
    assert "fallback_providers" not in applied
    assert applied["auxiliary"]["compression"] == {
        "provider": "custom",
        "model": "summary-model",
        "base_url": "",
        "api_key": "",
        "api_mode": "codex_responses",
        "fallback_chain": [],
        "timeout": 45,
        "extra_body": {"reasoning": {"effort": "low"}},
    }
    assert applied["mcp_servers"] == {}
    assert applied["plugins"]["enabled"] == []
    assert applied["plugins"]["disabled"] == ["extra-disabled"]
    assert applied["unmanaged"] == {"value": 7}
    assert existing["terminal"]["backend"] == "docker"


def test_apply_runtime_profile_rejects_malformed_managed_config() -> None:
    with pytest.raises(HermesRuntimeProfileError, match="agent.disabled_toolsets"):
        apply_runtime_profile({"agent": {"disabled_toolsets": "web"}})

    with pytest.raises(HermesRuntimeProfileError, match="auxiliary.vision"):
        apply_runtime_profile({"auxiliary": {"vision": "auto"}})


def test_auxiliary_api_mode_follows_allowed_main_model_transport() -> None:
    applied = apply_runtime_profile(
        {
            "model": {
                "provider": "custom",
                "api_mode": "chat_completions",
            },
            "auxiliary": {
                "vision": {"model": "vision-model"},
                "compression": {"api_mode": "anthropic_messages"},
            },
        }
    )

    assert applied["model"]["api_mode"] == "chat_completions"
    assert applied["auxiliary"]["vision"]["api_mode"] == "chat_completions"
    assert applied["auxiliary"]["compression"]["api_mode"] == "chat_completions"


def test_runtime_profile_environment_accepts_deployment_override() -> None:
    env = runtime_profile_environment(
        profile_path="/opt/potato/runtime-profile.yaml"
    )

    assert env["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    assert env["HERMES_SKIP_NODE_BOOTSTRAP"] == "1"
    assert env["HERMES_DISABLE_GATEWAY_PLATFORMS"] == "1"
    assert env["HERMES_DISABLE_MCP"] == "1"
    assert env["HERMES_DISABLE_CRON"] == "1"
    assert env["HERMES_DISABLE_KANBAN"] == "1"
    assert env["TERMINAL_ENV"] == "local"
    assert env["AGENT_BROWSER_ENGINE"] == "chrome"
    assert env["BROWSER_CDP_URL"] == ""
    assert env["CAMOFOX_URL"] == ""
    assert Path(env["HERMES_BUNDLED_SKILLS"]) == DEFAULT_BUNDLED_SKILLS_PATH
    assert Path(env["HERMES_OPTIONAL_SKILLS"]) == DEFAULT_OPTIONAL_SKILLS_PATH
    assert Path(env["HERMES_AGENT_BROWSER_BIN_DIR"]) == DEFAULT_AGENT_BROWSER_BIN_DIR
    assert Path(env["AGENT_BROWSER_EXECUTABLE_PATH"]) == (
        DEFAULT_AGENT_BROWSER_EXECUTABLE
    )
    assert Path(env["HERMES_RUNTIME_PROFILE_PATH"]) == Path(
        "/opt/potato/runtime-profile.yaml"
    )

    with pytest.raises(HermesRuntimeProfileError, match="under /opt"):
        runtime_profile_environment(
            profile_path="/tmp/user-controlled-profile.yaml"
        )


def test_runtime_profile_environment_requires_default_activation_path(
    monkeypatch,
) -> None:
    monkeypatch.delenv("HERMES_RUNTIME_PROFILE_PATH", raising=False)

    env = runtime_profile_environment()

    assert env == {
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
        "HERMES_SKIP_NODE_BOOTSTRAP": "1",
        "HERMES_DISABLE_GATEWAY_PLATFORMS": "1",
        "HERMES_DISABLE_MCP": "1",
        "HERMES_DISABLE_CRON": "1",
        "HERMES_DISABLE_KANBAN": "1",
        "TERMINAL_ENV": "local",
        "AGENT_BROWSER_ENGINE": "chrome",
        "BROWSER_CDP_URL": "",
        "CAMOFOX_URL": "",
        "HERMES_BUNDLED_SKILLS": str(DEFAULT_BUNDLED_SKILLS_PATH),
        "HERMES_OPTIONAL_SKILLS": str(DEFAULT_OPTIONAL_SKILLS_PATH),
        "HERMES_AGENT_BROWSER_BIN_DIR": str(DEFAULT_AGENT_BROWSER_BIN_DIR),
        "AGENT_BROWSER_EXECUTABLE_PATH": str(DEFAULT_AGENT_BROWSER_EXECUTABLE),
        "HERMES_RUNTIME_PROFILE_PATH": str(
            DEFAULT_ACTIVATION_RUNTIME_PROFILE_PATH
        ),
    }


def test_local_browser_cdp_url_accepts_only_resolved_loopback_websockets() -> None:
    endpoint = "ws://127.0.0.1:9222/devtools/browser/local"
    assert local_browser_cdp_url(endpoint) == endpoint
    ipv6_endpoint = "wss://[::1]:9223/devtools/page/local"
    assert local_browser_cdp_url(ipv6_endpoint) == ipv6_endpoint
    assert local_browser_cdp_url("") == ""

    for value in (
        "http://127.0.0.1:9222/json/version",
        "ws://localhost:9222/devtools/browser/unresolved",
        "ws://example.com:9222/devtools/browser/remote",
        "ws://127.0.0.1:9222",
        "ws://user@127.0.0.1:9222/devtools/browser/local",
        "ws://127.0.0.1:bad/devtools/browser/local",
        "ws://127.0.0.1:9222/other/local",
        "ws://127.0.0.1:9222/devtools/browser/local?token=test",
    ):
        with pytest.raises(HermesRuntimeProfileError, match="browser_cdp_url"):
            local_browser_cdp_url(value)

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from interface.hermes_service import (
    DEFAULT_APPROVAL_MODE,
    build_config_data,
    build_systemd_unit,
    install_public_data_link,
    install_user_runtime_files,
)
from interface.mapping import HermesTarget


def _write_bioinformatics_skills_template(tmp_path: Path) -> Path:
    source = tmp_path / "skill-source" / "potato-knowledge-bioinformatics"
    nested = source / "potato-gene-search" / "scripts"
    nested.mkdir(parents=True)
    (source / "DESCRIPTION.md").write_text("Bioinformatics skills\n", encoding="utf-8")
    (source / "potato-gene-search" / "SKILL.md").write_text(
        "Gene search\n", encoding="utf-8"
    )
    script = nested / "query_potato_gene.py"
    script.write_text("#!/usr/bin/env python3\nprint('gene')\n", encoding="utf-8")
    script.chmod(0o755)
    return source


def _write_plan_mode_skills_template(tmp_path: Path) -> Path:
    source = tmp_path / "skill-source" / "plan-mode"
    plan_skill = source / "plan"
    plan_skill.mkdir(parents=True)
    (plan_skill / "SKILL.md").write_text(
        "---\nname: plan\ndescription: Test plan mode\n---\n\nPlan mode\n",
        encoding="utf-8",
    )
    return source


def _write_public_data_template(tmp_path: Path) -> Path:
    public_data = tmp_path / "public_data_source"
    public_data.mkdir()
    (public_data / "README.txt").write_text("public\n", encoding="utf-8")
    return public_data


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


def legacy_test_build_config_data_emits_single_fallback_model_at_top_level() -> None:
    fallback = {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4",
        "api_key": "sk-fallback",
    }

    data = build_config_data({"hermes": {"fallback_model": fallback}}, _target())

    assert data["fallback_model"] == fallback
    assert "fallback_providers" not in data


def legacy_test_build_config_data_passes_standard_fallback_providers() -> None:
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


def legacy_test_build_config_data_does_not_normalize_legacy_fallback_model_list() -> None:
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


def legacy_test_build_config_data_passes_both_standard_fallback_fields() -> None:
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


def test_build_config_data_defaults_approvals_to_smart() -> None:
    data = build_config_data({"hermes": {}}, _target())

    assert data["approvals"]["mode"] == DEFAULT_APPROVAL_MODE


def test_build_config_data_applies_runtime_profile_after_user_overrides() -> None:
    target = replace(
        _target(),
        config_overrides={
            "platform_toolsets": {"cli": ["hermes-cli"]},
            "security": {"allow_lazy_installs": True},
            "terminal": {"backend": "docker"},
            "browser": {
                "cloud_provider": "browser-use",
                "cdp_url": "http://remote.example:9222",
                "engine": "lightpanda",
            },
            "memory": {"provider": "honcho"},
            "context": {"engine": "lcm"},
            "gateway": {
                "platforms": {"telegram": {"enabled": True}},
                "bind": "127.0.0.1",
            },
            "mcp_servers": {"example": {"command": "server"}},
            "plugins": {"enabled": ["example"]},
            "agent": {"disabled_toolsets": ["memory"]},
        },
    )

    data = build_config_data({"hermes": {}}, target)

    assert data["platform_toolsets"]["cli"][-1] == "no_mcp"
    assert data["security"]["allow_lazy_installs"] is False
    assert data["terminal"]["backend"] == "local"
    assert data["browser"]["cloud_provider"] == "local"
    assert data["browser"]["cdp_url"] == ""
    assert data["browser"]["engine"] == "chrome"
    assert data["memory"]["provider"] == ""
    assert data["context"]["engine"] == "compressor"
    assert data["gateway"] == {"platforms": {}, "bind": "127.0.0.1"}
    assert data["mcp_servers"] == {}
    assert data["plugins"]["enabled"] == []
    assert "memory" in data["agent"]["disabled_toolsets"]


def test_build_config_data_allows_global_approval_mode_override() -> None:
    data = build_config_data(
        {
            "hermes": {
                "config_overrides": {
                    "approvals": {
                        "mode": "manual",
                    },
                },
            },
        },
        _target(),
    )

    assert data["approvals"]["mode"] == "manual"


def test_build_config_data_allows_user_approval_mode_override() -> None:
    target = replace(_target(), config_overrides={"approvals": {"mode": "manual"}})

    data = build_config_data(
        {
            "hermes": {
                "config_overrides": {
                    "approvals": {
                        "mode": "off",
                    },
                },
            },
        },
        target,
    )

    assert data["approvals"]["mode"] == "manual"


def test_build_config_data_disables_fallbacks_and_uses_proxy_credentials() -> None:
    data = build_config_data(
        {
            "hermes": {
                "model": {
                    "default": "gpt-5.4",
                    "provider": "custom",
                    "base_url": "https://primary.example/v1",
                    "api_key": "sk-primary",
                },
                "fallback_providers": [
                    {
                        "provider": "custom",
                        "model": "gpt-5.4-mini",
                        "base_url": "https://fallback.example/v1",
                        "api_key": "sk-fallback",
                    }
                ],
            }
        },
        _target(),
    )

    assert data["model"] == {
        "default": "gpt-5.4",
        "provider": "custom",
        "base_url": "http://127.0.0.1:8765/v1",
        "api_key": "alice-local-token",
        "api_mode": "codex_responses",
    }
    assert "fallback_providers" not in data
    assert "fallback_model" not in data

def test_build_systemd_unit_hides_interface_paths() -> None:
    unit = build_systemd_unit({"hermes": {}}, _target())

    assert "PrivateTmp=yes" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "InaccessiblePaths=-/srv/potato_agent" in unit
    assert "InaccessiblePaths=-/var/lib/potato-agent" in unit
    assert "InaccessiblePaths=-/opt/interface-env" in unit


@pytest.mark.parametrize(
    ("hermes_config", "error"),
    [
        (
            {"service": {"restart": "no\nUser=root\nExecStartPost=/usr/bin/true"}},
            "control characters",
        ),
        ({"service": {"restart": "sometimes"}}, "must be one of"),
        (
            {"service": {"description_template": "Hermes\nUser=root"}},
            "control characters",
        ),
        (
            {"service": {"description_template": "Hermes\u2028User=root"}},
            "control characters",
        ),
        ({"executable": "/usr/local/bin/hermes\nExecStartPost=/bin/true"}, "control"),
        (
            {"service": {"inaccessible_paths": ["/srv/data\nReadWritePaths=/"]}},
            "control",
        ),
    ],
)
def test_build_systemd_unit_rejects_directive_injection(
    hermes_config, error
) -> None:
    with pytest.raises(RuntimeError, match=error):
        build_systemd_unit({"hermes": hermes_config}, _target())


def test_build_systemd_unit_injects_runtime_profile_guards(monkeypatch) -> None:
    unit = build_systemd_unit(
        {"hermes": {"runtime_profile_path": "/opt/potato/profile.yaml"}},
        _target(),
    )

    assert "Environment=HERMES_DISABLE_LAZY_INSTALLS=1" in unit
    assert "Environment=HERMES_SKIP_NODE_BOOTSTRAP=1" in unit
    assert "Environment=HERMES_DISABLE_GATEWAY_PLATFORMS=1" in unit
    assert "Environment=HERMES_DISABLE_MCP=1" in unit
    assert "Environment=HERMES_DISABLE_CRON=1" in unit
    assert "Environment=HERMES_DISABLE_KANBAN=1" in unit
    assert "Environment=TERMINAL_ENV=local" in unit
    assert "Environment=AGENT_BROWSER_ENGINE=chrome" in unit
    assert "Environment=BROWSER_CDP_URL=" in unit
    assert "Environment=CAMOFOX_URL=" in unit
    assert (
        "Environment=HERMES_BUNDLED_SKILLS="
        "/opt/potato-hermes-lite/current/share/hermes/skills"
    ) in unit
    assert (
        "Environment=HERMES_OPTIONAL_SKILLS="
        "/opt/potato-hermes-lite/current/share/hermes/optional-skills"
    ) in unit
    assert (
        "Environment=HERMES_AGENT_BROWSER_BIN_DIR="
        "/opt/potato-hermes-lite/current/browser/bin"
    ) in unit
    assert (
        "Environment=AGENT_BROWSER_EXECUTABLE_PATH="
        "/opt/potato-hermes-lite/current/browser/chrome/chrome-linux64/chrome"
    ) in unit
    assert "Environment=HERMES_RUNTIME_PROFILE_PATH=/opt/potato/profile.yaml" in unit


def test_build_systemd_unit_accepts_configured_runtime_profile_path(
    monkeypatch,
) -> None:
    monkeypatch.delenv("HERMES_RUNTIME_PROFILE_PATH", raising=False)

    unit = build_systemd_unit(
        {"hermes": {"runtime_profile_path": "/opt/potato/release/profile.yaml"}},
        _target(),
    )

    assert (
        "Environment=HERMES_RUNTIME_PROFILE_PATH=/opt/potato/release/profile.yaml"
        in unit
    )


def test_build_systemd_unit_accepts_resolved_loopback_cdp() -> None:
    unit = build_systemd_unit(
        {
            "hermes": {
                "browser_cdp_url": (
                    "ws://127.0.0.1:9222/devtools/browser/local"
                )
            }
        },
        _target(),
    )

    assert (
        "Environment=BROWSER_CDP_URL="
        "ws://127.0.0.1:9222/devtools/browser/local"
    ) in unit


def test_build_systemd_unit_uses_required_default_profile_path(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_RUNTIME_PROFILE_PATH", raising=False)

    unit = build_systemd_unit({"hermes": {}}, _target())

    assert "Environment=HERMES_DISABLE_LAZY_INSTALLS=1" in unit
    assert "Environment=HERMES_SKIP_NODE_BOOTSTRAP=1" in unit
    assert "Environment=HERMES_DISABLE_GATEWAY_PLATFORMS=1" in unit
    assert "Environment=HERMES_DISABLE_MCP=1" in unit
    assert "Environment=HERMES_DISABLE_CRON=1" in unit
    assert "Environment=HERMES_DISABLE_KANBAN=1" in unit
    assert "Environment=TERMINAL_ENV=local" in unit
    assert "Environment=AGENT_BROWSER_ENGINE=chrome" in unit
    assert "Environment=BROWSER_CDP_URL=" in unit
    assert "Environment=CAMOFOX_URL=" in unit
    assert "Environment=HERMES_BUNDLED_SKILLS=" in unit
    assert "Environment=HERMES_OPTIONAL_SKILLS=" in unit
    assert "Environment=HERMES_AGENT_BROWSER_BIN_DIR=" in unit
    assert "Environment=AGENT_BROWSER_EXECUTABLE_PATH=" in unit
    assert (
        "Environment=HERMES_RUNTIME_PROFILE_PATH="
        "/opt/potato-hermes-lite/current/config/runtime-profile.yaml"
    ) in unit


def test_build_systemd_unit_prioritizes_agent_runtime() -> None:
    unit = build_systemd_unit({"hermes": {}}, _target())

    assert "CPUWeight=1000" in unit
    assert "IOWeight=10000" in unit
    assert "Nice=-5" in unit
    assert "IOSchedulingClass=best-effort" in unit
    assert "IOSchedulingPriority=0" in unit


def test_build_systemd_unit_allows_gateway_drain_before_stop_timeout() -> None:
    unit = build_systemd_unit({"hermes": {}}, _target())

    assert "TimeoutStopSec=210" in unit


def test_build_systemd_unit_uses_configured_gateway_drain_timeout() -> None:
    target = _target()
    unit = build_systemd_unit(
        {
            "hermes": {
                "config_overrides": {
                    "agent": {
                        "restart_drain_timeout": 45,
                    },
                },
            },
        },
        target,
    )

    assert "TimeoutStopSec=75" in unit


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
    soul_template = tmp_path / "SOUL.template.md"
    soul_template.write_text("Template soul\n", encoding="utf-8")
    skills_template = _write_bioinformatics_skills_template(tmp_path)
    plan_mode_template = _write_plan_mode_skills_template(tmp_path)
    public_data = _write_public_data_template(tmp_path)

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
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_SOUL_TEMPLATE_PATH",
        soul_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_BIOINFORMATICS_SKILLS_PATH",
        skills_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PLAN_MODE_SKILLS_PATH",
        plan_mode_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PUBLIC_DATA_PATH",
        public_data,
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

    env_body = (user.hermes_home / ".env").read_text(encoding="utf-8")
    config_body = (user.hermes_home / "config.yaml").read_text(encoding="utf-8")
    config_data = yaml.safe_load(config_body)
    assert config_data["model"]["default"] == "gpt-5.5"
    assert "OPENAI_API_KEY" not in env_body
    assert "sk-user" not in env_body
    assert config_data["model"]["base_url"] == "http://127.0.0.1:8765/v1"
    assert config_data["model"]["api_key"] == "alice-local-token"
    assert "sk-user" not in config_body
    assert (
        user.hermes_home / "SOUL.md"
    ).read_text(encoding="utf-8") == "Template soul\n"
    assert (
        user.hermes_home
        / "skills"
        / "potato-knowledge-bioinformatics"
        / "potato-gene-search"
        / "SKILL.md"
    ).read_text(encoding="utf-8") == "Gene search\n"
    assert (
        user.hermes_home / "skills" / "plan-mode" / "plan" / "SKILL.md"
    ).read_text(encoding="utf-8").startswith("---\nname: plan\n")
    public_data_link = user.home_dir / "public_data"
    assert public_data_link.is_symlink()
    assert public_data_link.resolve() == public_data
    assert touched_paths
    assert all(tmp_path in path.parents or path == tmp_path for path in touched_paths)


def test_install_user_runtime_files_overwrites_soul_file_with_template(
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
        extra_env={},
        config_overrides={},
    )
    soul_template = tmp_path / "SOUL.template.md"
    soul_template.write_text("Current template\n", encoding="utf-8")
    skills_template = _write_bioinformatics_skills_template(tmp_path)
    plan_mode_template = _write_plan_mode_skills_template(tmp_path)
    public_data = _write_public_data_template(tmp_path)
    user.hermes_home.mkdir(parents=True)
    (user.hermes_home / "SOUL.md").write_text("Old user soul\n", encoding="utf-8")
    touched_modes: dict[Path, int] = {}

    monkeypatch.setattr(
        "interface.hermes_service.pwd.getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        "interface.hermes_service._set_owner_and_mode",
        lambda path, uid, gid, mode: touched_modes.__setitem__(path, mode),
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_SOUL_TEMPLATE_PATH",
        soul_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_BIOINFORMATICS_SKILLS_PATH",
        skills_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PLAN_MODE_SKILLS_PATH",
        plan_mode_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PUBLIC_DATA_PATH",
        public_data,
    )

    install_user_runtime_files({"hermes": {}}, user)

    soul_path = user.hermes_home / "SOUL.md"
    assert soul_path.read_text(encoding="utf-8") == "Current template\n"
    assert touched_modes[soul_path] == 0o600


def test_install_user_runtime_files_replaces_managed_bioinformatics_skills(
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
        extra_env={},
        config_overrides={},
    )
    soul_template = tmp_path / "SOUL.template.md"
    soul_template.write_text("Template soul\n", encoding="utf-8")
    skills_template = _write_bioinformatics_skills_template(tmp_path)
    plan_mode_template = _write_plan_mode_skills_template(tmp_path)
    public_data = _write_public_data_template(tmp_path)
    old_skill_dir = user.hermes_home / "skills" / "potato-knowledge-bioinformatics"
    old_skill_dir.mkdir(parents=True)
    (old_skill_dir / "old.txt").write_text("stale\n", encoding="utf-8")
    old_plan_mode_dir = user.hermes_home / "skills" / "plan-mode"
    old_plan_mode_dir.mkdir(parents=True)
    (old_plan_mode_dir / "old.txt").write_text("stale\n", encoding="utf-8")
    touched_modes: dict[Path, int] = {}

    monkeypatch.setattr(
        "interface.hermes_service.pwd.getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        "interface.hermes_service._set_owner_and_mode",
        lambda path, uid, gid, mode: touched_modes.__setitem__(path, mode),
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_SOUL_TEMPLATE_PATH",
        soul_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_BIOINFORMATICS_SKILLS_PATH",
        skills_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PLAN_MODE_SKILLS_PATH",
        plan_mode_template,
    )
    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PUBLIC_DATA_PATH",
        public_data,
    )

    install_user_runtime_files({"hermes": {}}, user)

    target_dir = user.hermes_home / "skills" / "potato-knowledge-bioinformatics"
    target_script = (
        target_dir / "potato-gene-search" / "scripts" / "query_potato_gene.py"
    )
    assert not (target_dir / "old.txt").exists()
    assert (
        target_dir / "DESCRIPTION.md"
    ).read_text(encoding="utf-8") == "Bioinformatics skills\n"
    assert target_script.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")
    plan_mode_target = user.hermes_home / "skills" / "plan-mode"
    assert not (plan_mode_target / "old.txt").exists()
    assert (
        plan_mode_target / "plan" / "SKILL.md"
    ).read_text(encoding="utf-8").startswith("---\nname: plan\n")
    assert touched_modes[user.hermes_home / "skills"] == 0o700
    assert touched_modes[target_script] == 0o755


def test_install_public_data_link_creates_home_symlink(monkeypatch, tmp_path) -> None:
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
        extra_env={},
        config_overrides={},
    )
    public_data = _write_public_data_template(tmp_path)
    user.home_dir.mkdir(parents=True)

    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PUBLIC_DATA_PATH",
        public_data,
    )
    monkeypatch.setattr("interface.hermes_service.os.lchown", lambda path, uid, gid: None)

    install_public_data_link(user, uid=123, gid=456)

    link_path = user.home_dir / "public_data"
    assert link_path.is_symlink()
    assert link_path.resolve() == public_data


def test_install_public_data_link_does_not_overwrite_existing_path(
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
        extra_env={},
        config_overrides={},
    )
    public_data = _write_public_data_template(tmp_path)
    user.home_dir.mkdir(parents=True)
    (user.home_dir / "public_data").write_text("user file\n", encoding="utf-8")

    monkeypatch.setattr(
        "interface.hermes_service.DEFAULT_PUBLIC_DATA_PATH",
        public_data,
    )

    with pytest.raises(RuntimeError, match="path already exists"):
        install_public_data_link(user, uid=123, gid=456)

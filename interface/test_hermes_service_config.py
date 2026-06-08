from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from interface.hermes_service import (
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


def test_build_systemd_unit_prioritizes_agent_runtime() -> None:
    unit = build_systemd_unit({"hermes": {}}, _target())

    assert "CPUWeight=1000" in unit
    assert "IOWeight=10000" in unit
    assert "Nice=-5" in unit
    assert "IOSchedulingClass=best-effort" in unit
    assert "IOSchedulingPriority=0" in unit


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
    public_data = _write_public_data_template(tmp_path)
    old_skill_dir = user.hermes_home / "skills" / "potato-knowledge-bioinformatics"
    old_skill_dir.mkdir(parents=True)
    (old_skill_dir / "old.txt").write_text("stale\n", encoding="utf-8")
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

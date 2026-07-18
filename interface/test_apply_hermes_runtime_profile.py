from __future__ import annotations

import os
import pwd
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import apply_hermes_runtime_profile as apply_script
from interface.hermes_profile import load_runtime_profile
from interface.mapping import HermesTarget


def _target(tmp_path: Path) -> HermesTarget:
    home = tmp_path / "home"
    linux_user = pwd.getpwuid(os.getuid()).pw_name
    return HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user=linux_user,
        home_dir=home,
        hermes_home=home / ".hermes",
        workdir=home / "work",
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )


def _write_config(target: HermesTarget) -> Path:
    target.hermes_home.mkdir(parents=True)
    path = target.hermes_home / "config.yaml"
    path.write_text(
        "model:\n  default: gpt-test\n  provider: openrouter\n"
        "terminal:\n  backend: docker\n",
        encoding="utf-8",
    )
    path.chmod(0o640)
    return path


def _write_mapping(tmp_path: Path, target: HermesTarget) -> Path:
    mapping = tmp_path / "users_mapping.yaml"
    mapping.write_text(
        yaml.safe_dump(
            {
                "users": [
                    {
                        "username": target.username,
                        "email": target.email,
                        "display_name": target.display_name,
                        "linux_user": target.linux_user,
                        "home_dir": str(target.home_dir),
                        "hermes_home": str(target.hermes_home),
                        "workdir": str(target.workdir),
                        "api_port": target.api_port,
                        "api_key": target.api_key,
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return mapping


def test_process_target_check_is_read_only(tmp_path: Path) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    original = config_path.read_bytes()

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=False,
    )

    assert result.changed is True
    assert result.applied is False
    assert result.error == ""
    assert config_path.read_bytes() == original
    assert list(target.hermes_home.glob("config.yaml.bak.runtime-profile.*")) == []


def test_process_target_apply_backs_up_and_atomically_patches_only_config(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    original = config_path.read_bytes()
    original_stat = config_path.stat()
    skills_file = target.hermes_home / "skills" / "example" / "SKILL.md"
    skills_file.parent.mkdir(parents=True)
    skills_file.write_text("unchanged skill\n", encoding="utf-8")
    state_db = target.hermes_home / "state.db"
    state_db.write_bytes(b"unchanged db")

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )

    assert result.applied is True
    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == original
    assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o640
    assert result.backup_path.stat().st_uid == original_stat.st_uid
    assert result.backup_path.stat().st_gid == original_stat.st_gid
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640
    assert config_path.stat().st_uid == original_stat.st_uid
    assert config_path.stat().st_gid == original_stat.st_gid
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["model"]["provider"] == "custom"
    assert data["terminal"]["backend"] == "local"
    assert data["platform_toolsets"]["cli"][-1] == "no_mcp"
    assert skills_file.read_text(encoding="utf-8") == "unchanged skill\n"
    assert state_db.read_bytes() == b"unchanged db"

    second = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )
    assert second.changed is False
    assert second.applied is False
    assert len(list(target.hermes_home.glob("config.yaml.bak.runtime-profile.*"))) == 1


def test_atomic_backup_uses_collision_suffix(tmp_path: Path) -> None:
    target = _target(tmp_path)
    path = _write_config(target)
    original = path.read_bytes()

    with apply_script._open_config_snapshot(target) as snapshot:
        first = apply_script.atomic_backup_and_write(
            snapshot,
            b"model:\n  default: first\n",
            timestamp="20260715T120000Z",
        )
    first_version = path.read_bytes()
    with apply_script._open_config_snapshot(target) as snapshot:
        second = apply_script.atomic_backup_and_write(
            snapshot,
            b"model:\n  default: second\n",
            timestamp="20260715T120000Z",
        )

    assert first.name == "config.yaml.bak.runtime-profile.20260715T120000Z"
    assert second.name == "config.yaml.bak.runtime-profile.20260715T120000Z.1"
    assert first.read_bytes() == original
    assert second.read_bytes() == first_version
    assert path.read_bytes() == b"model:\n  default: second\n"


def test_process_target_rejects_config_outside_home(tmp_path: Path) -> None:
    target = _target(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    config_path = outside / "config.yaml"
    config_path.write_text("terminal:\n  backend: docker\n", encoding="utf-8")
    original = config_path.read_bytes()
    outside_target = replace(target, hermes_home=outside)

    result = apply_script.process_target(
        outside_target,
        load_runtime_profile(),
        apply=True,
    )

    assert "inside target.home_dir" in result.error
    assert config_path.read_bytes() == original
    assert list(outside.glob("*.bak.runtime-profile.*")) == []


def test_process_target_rejects_symlinked_config(tmp_path: Path) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    outside = tmp_path / "outside.yaml"
    outside.write_text("terminal:\n  backend: docker\n", encoding="utf-8")
    original = outside.read_bytes()
    (target.hermes_home / "config.yaml").symlink_to(outside)

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )

    assert "symlinked or unreadable config" in result.error
    assert outside.read_bytes() == original


def test_process_target_rejects_symlinked_config_ancestor(tmp_path: Path) -> None:
    target = _target(tmp_path)
    target.home_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_config = outside / "config.yaml"
    outside_config.write_text("terminal:\n  backend: docker\n", encoding="utf-8")
    original = outside_config.read_bytes()
    target.hermes_home.symlink_to(outside, target_is_directory=True)

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )

    assert "symlinked ancestor" in result.error
    assert outside_config.read_bytes() == original
    assert list(outside.glob("*.bak.runtime-profile.*")) == []


def test_process_target_rejects_non_regular_config(tmp_path: Path) -> None:
    target = _target(tmp_path)
    target.hermes_home.mkdir(parents=True)
    os.mkfifo(target.hermes_home / "config.yaml")

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=False,
    )

    assert "regular file" in result.error


def test_process_target_rejects_wrong_config_owner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    original = config_path.read_bytes()
    monkeypatch.setattr(
        apply_script.pwd,
        "getpwnam",
        lambda _username: SimpleNamespace(
            pw_uid=os.getuid() + 1,
            pw_gid=os.getgid() + 1,
        ),
    )

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )

    assert "config owner must be" in result.error
    assert config_path.read_bytes() == original


def test_process_target_rejects_hardlinked_config(tmp_path: Path) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    hardlink = target.hermes_home / "config-copy.yaml"
    os.link(config_path, hardlink)
    original = config_path.read_bytes()

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )

    assert "exactly one hard link" in result.error
    assert config_path.read_bytes() == original
    assert hardlink.read_bytes() == original


def test_process_target_aborts_if_config_changes_before_rename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    concurrent = b"terminal:\n  backend: local\nconcurrent: true\n"
    original_assert = apply_script._assert_snapshot_current
    calls = 0

    def mutate_before_second_check(snapshot) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            config_path.write_bytes(concurrent)
        original_assert(snapshot)

    monkeypatch.setattr(
        apply_script,
        "_assert_snapshot_current",
        mutate_before_second_check,
    )

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )

    assert "config changed before commit" in result.error
    assert config_path.read_bytes() == concurrent
    assert list(target.hermes_home.glob("config.yaml.bak.runtime-profile.*")) == []
    assert list(target.hermes_home.glob(".config.yaml.runtime-profile.*")) == []


def test_process_target_aborts_if_config_directory_is_replaced(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    original = config_path.read_bytes()
    moved = target.home_dir / ".hermes-moved"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_config = outside / "config.yaml"
    outside_config.write_text("outside: unchanged\n", encoding="utf-8")
    outside_original = outside_config.read_bytes()
    original_assert = apply_script._assert_directory_still_bound
    calls = 0

    def replace_directory_before_second_check(snapshot) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            target.hermes_home.rename(moved)
            target.hermes_home.symlink_to(outside, target_is_directory=True)
        original_assert(snapshot)

    monkeypatch.setattr(
        apply_script,
        "_assert_directory_still_bound",
        replace_directory_before_second_check,
    )

    result = apply_script.process_target(
        target,
        load_runtime_profile(),
        apply=True,
    )

    assert "config directory path changed before commit" in result.error
    assert outside_config.read_bytes() == outside_original
    assert (moved / "config.yaml").read_bytes() == original
    assert list(moved.glob("config.yaml.bak.runtime-profile.*")) == []
    assert list(moved.glob(".config.yaml.runtime-profile.*")) == []


def test_main_defaults_to_check_and_does_not_write(tmp_path: Path, capsys) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    mapping_path = _write_mapping(tmp_path, target)
    original = config_path.read_bytes()

    exit_code = apply_script.main(["--mapping", str(mapping_path)])

    assert exit_code == 1
    assert capsys.readouterr().out.startswith("DRIFT alice:")
    assert config_path.read_bytes() == original


def test_main_apply_requires_explicit_scope(tmp_path: Path) -> None:
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text("users: []\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        apply_script.main(["--apply", "--mapping", str(mapping_path)])

    assert exc_info.value.code == 2


def test_main_apply_requires_root(monkeypatch, tmp_path: Path) -> None:
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text("users: []\n", encoding="utf-8")
    monkeypatch.setattr(apply_script.os, "geteuid", lambda: 1000)

    with pytest.raises(RuntimeError, match="must run as root"):
        apply_script.main(["--apply", "--all", "--mapping", str(mapping_path)])


def test_select_targets_rejects_users_silently_filtered_by_mapping_store(
    tmp_path: Path,
) -> None:
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text(
        "users:\n"
        "  - username: incomplete\n"
        "    email: incomplete@example.com\n"
        "    linux_user: hmx_incomplete\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="could not be loaded.*incomplete"):
        apply_script._select_targets(mapping_path, None)


def test_main_apply_user_patches_without_restarting_services(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    target = _target(tmp_path)
    config_path = _write_config(target)
    mapping_path = _write_mapping(tmp_path, target)
    monkeypatch.setattr(apply_script.os, "geteuid", lambda: 0)

    exit_code = apply_script.main(
        ["--apply", "--user", "alice", "--mapping", str(mapping_path)]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.startswith("APPLIED alice:")
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["terminal"][
        "backend"
    ] == "local"

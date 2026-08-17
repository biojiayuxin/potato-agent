from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import refresh_managed_skills as refresh


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    skill = source / "general-web-search"
    script_dir = skill / "scripts"
    script_dir.mkdir(parents=True)
    (source / "DESCRIPTION.md").write_text("managed description\n", encoding="utf-8")
    (skill / "SKILL.md").write_text("managed skill\n", encoding="utf-8")
    script = script_dir / "query.py"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    script.chmod(0o755)
    return source


def _mapping(tmp_path: Path, usernames=("alice", "bob")):
    mapping_path = tmp_path / "mapping.yaml"
    password_entries = {}
    lines = [
        "start_port: 8643",
        "hermes:",
        "  model:",
        "    default: model",
        "    provider: custom",
        "    base_url: http://127.0.0.1:8765/v1",
        "    api_key: pmp_default_0123456789abcdefghijklmnopqrstuvwxyz",
        "users:",
    ]
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    for index, username in enumerate(usernames):
        home = tmp_path / f"home-{username}"
        hermes_home = home / ".hermes"
        workdir = home / "work"
        hermes_home.mkdir(parents=True)
        workdir.mkdir()
        home.chmod(0o700)
        hermes_home.chmod(0o700)
        workdir.chmod(0o700)
        password_entries[f"linux-{username}"] = SimpleNamespace(
            pw_uid=os.geteuid(), pw_gid=os.getegid(), pw_dir=str(home)
        )
        lines.extend(
            [
                f"  - username: {username}",
                f"    email: {username}@example.com",
                f"    display_name: {username.title()}",
                f"    linux_user: linux-{username}",
                f"    home_dir: {home}",
                f"    hermes_home: {hermes_home}",
                f"    workdir: {workdir}",
                f"    api_port: {8700 + index}",
                f"    api_key: sk-user-{username}",
                f"    model_proxy_token: pmp_{username}_0123456789abcdefghijklmnopqrstuvwxyz",
                f"    systemd_service: hermes-{username}.service",
            ]
        )
        (unit_dir / f"hermes-{username}.service").write_text(
            "[Service]\nExecStart=/bin/true\n", encoding="utf-8"
        )
    mapping_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mapping_path, password_entries, unit_dir


def _password_lookup(entries):
    return lambda linux_user: entries[linux_user]


def _managed_path(tmp_path: Path, username: str) -> Path:
    return (
        tmp_path
        / f"home-{username}"
        / ".hermes"
        / "skills"
        / "potato-knowledge-bioinformatics"
    )


def test_refresh_defaults_to_dry_run_without_writes(tmp_path) -> None:
    source = _source(tmp_path)
    mapping, entries, unit_dir = _mapping(tmp_path)
    managed = _managed_path(tmp_path, "alice")
    managed.mkdir(parents=True)
    marker = managed / "user-change.txt"
    marker.write_text("keep until apply\n", encoding="utf-8")

    result = refresh.refresh_managed_skills(
        mapping_path=mapping,
        source_path=source,
        username="alice",
        all_users=False,
        expect_count=None,
        apply=False,
        unit_dir=unit_dir,
        password_lookup=_password_lookup(entries),
        service_active=lambda _service: True,
        require_deployed_source=False,
    )

    assert result.applied is False
    assert result.selected == ("alice",)
    assert result.active == ("alice",)
    assert marker.read_text(encoding="utf-8") == "keep until apply\n"


def test_all_apply_requires_matching_expect_count_before_writes(tmp_path) -> None:
    source = _source(tmp_path)
    mapping, entries, unit_dir = _mapping(tmp_path)
    marker = _managed_path(tmp_path, "alice") / "old.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("old\n", encoding="utf-8")

    with pytest.raises(refresh.ManagedSkillRefreshError, match="requires"):
        refresh.refresh_managed_skills(
            mapping_path=mapping,
            source_path=source,
            username=None,
            all_users=True,
            expect_count=None,
            apply=True,
            unit_dir=unit_dir,
            password_lookup=_password_lookup(entries),
            service_active=lambda _service: False,
            require_deployed_source=False,
        )
    assert marker.exists()

    with pytest.raises(refresh.ManagedSkillRefreshError, match="does not match"):
        refresh.refresh_managed_skills(
            mapping_path=mapping,
            source_path=source,
            username=None,
            all_users=True,
            expect_count=3,
            apply=True,
            unit_dir=unit_dir,
            password_lookup=_password_lookup(entries),
            service_active=lambda _service: False,
            require_deployed_source=False,
        )
    assert marker.exists()


def test_apply_replaces_only_managed_category_and_restarts_only_active(
    tmp_path,
) -> None:
    source = _source(tmp_path)
    mapping, entries, unit_dir = _mapping(tmp_path)
    restarts: list[str] = []
    before: dict[str, tuple[str, str, str]] = {}
    for username in ("alice", "bob"):
        hermes_home = tmp_path / f"home-{username}" / ".hermes"
        (hermes_home / "config.yaml").write_text("config\n", encoding="utf-8")
        (hermes_home / "SOUL.md").write_text("soul\n", encoding="utf-8")
        custom = hermes_home / "skills" / "custom-skill"
        plan = hermes_home / "skills" / "plan-mode"
        custom.mkdir(parents=True)
        plan.mkdir()
        (custom / "SKILL.md").write_text("custom\n", encoding="utf-8")
        (plan / "SKILL.md").write_text("plan\n", encoding="utf-8")
        managed = _managed_path(tmp_path, username)
        managed.mkdir()
        (managed / "obsolete.txt").write_text("obsolete\n", encoding="utf-8")
        before[username] = (
            (hermes_home / "config.yaml").read_text(encoding="utf-8"),
            (hermes_home / "SOUL.md").read_text(encoding="utf-8"),
            (custom / "SKILL.md").read_text(encoding="utf-8"),
        )

    result = refresh.refresh_managed_skills(
        mapping_path=mapping,
        source_path=source,
        username=None,
        all_users=True,
        expect_count=2,
        apply=True,
        unit_dir=unit_dir,
        password_lookup=_password_lookup(entries),
        service_active=lambda service: service == "hermes-alice.service",
        service_restart=restarts.append,
        require_deployed_source=False,
    )

    assert result.completed == ("alice", "bob")
    assert restarts == ["hermes-alice.service"]
    for username in ("alice", "bob"):
        hermes_home = tmp_path / f"home-{username}" / ".hermes"
        managed = _managed_path(tmp_path, username)
        assert not (managed / "obsolete.txt").exists()
        assert (managed / "DESCRIPTION.md").read_text(encoding="utf-8") == (
            "managed description\n"
        )
        assert (managed / "general-web-search" / "SKILL.md").exists()
        assert before[username] == (
            (hermes_home / "config.yaml").read_text(encoding="utf-8"),
            (hermes_home / "SOUL.md").read_text(encoding="utf-8"),
            (
                hermes_home / "skills" / "custom-skill" / "SKILL.md"
            ).read_text(encoding="utf-8"),
        )
        assert (hermes_home / "skills" / "plan-mode" / "SKILL.md").exists()
        assert stat.S_IMODE(managed.stat().st_mode) == 0o700
        assert stat.S_IMODE(
            (managed / "general-web-search" / "SKILL.md").stat().st_mode
        ) == 0o600
        assert stat.S_IMODE(
            (managed / "general-web-search" / "scripts" / "query.py").stat().st_mode
        ) == 0o700


def test_any_preflight_failure_leaves_every_user_unchanged(tmp_path) -> None:
    source = _source(tmp_path)
    mapping, entries, unit_dir = _mapping(tmp_path)
    (unit_dir / "hermes-bob.service").unlink()
    markers = []
    for username in ("alice", "bob"):
        marker = _managed_path(tmp_path, username) / "old.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text(f"old-{username}\n", encoding="utf-8")
        markers.append(marker)

    with pytest.raises(refresh.ManagedSkillRefreshError, match="bob"):
        refresh.refresh_managed_skills(
            mapping_path=mapping,
            source_path=source,
            username=None,
            all_users=True,
            expect_count=2,
            apply=True,
            unit_dir=unit_dir,
            password_lookup=_password_lookup(entries),
            service_active=lambda _service: False,
            require_deployed_source=False,
        )

    assert [path.read_text(encoding="utf-8") for path in markers] == [
        "old-alice\n",
        "old-bob\n",
    ]


def test_apply_failure_reports_completed_and_incomplete_users(tmp_path) -> None:
    source = _source(tmp_path)
    mapping, entries, unit_dir = _mapping(tmp_path)

    def restart(service: str) -> None:
        if service == "hermes-bob.service":
            raise RuntimeError("restart failed")

    with pytest.raises(refresh.ManagedSkillRefreshError) as failure:
        refresh.refresh_managed_skills(
            mapping_path=mapping,
            source_path=source,
            username=None,
            all_users=True,
            expect_count=2,
            apply=True,
            unit_dir=unit_dir,
            password_lookup=_password_lookup(entries),
            service_active=lambda _service: True,
            service_restart=restart,
            require_deployed_source=False,
        )

    message = str(failure.value)
    assert "completed: alice" in message
    assert "incomplete: bob" in message
    assert str(tmp_path) not in message


def test_apply_rejects_non_deployed_source_before_target_changes(
    tmp_path, monkeypatch
) -> None:
    source = _source(tmp_path)
    mapping, entries, unit_dir = _mapping(tmp_path, usernames=("alice",))
    monkeypatch.setattr(refresh.os, "geteuid", lambda: 0)
    with pytest.raises(refresh.ManagedSkillRefreshError, match="deployed"):
        refresh.refresh_managed_skills(
            mapping_path=mapping,
            source_path=source,
            username="alice",
            all_users=False,
            expect_count=1,
            apply=True,
            unit_dir=unit_dir,
            password_lookup=_password_lookup(entries),
            service_active=lambda _service: False,
            require_deployed_source=True,
        )


def test_parser_requires_scope_and_defaults_to_dry_run() -> None:
    parser = refresh.build_parser()
    args = parser.parse_args(["--all"])
    assert args.apply is False
    assert args.all is True
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--all", "--user", "alice"])

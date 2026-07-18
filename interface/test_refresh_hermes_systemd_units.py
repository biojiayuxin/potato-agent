from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import refresh_hermes_systemd_units as refresh
from interface.mapping import HermesTarget


def _target(tmp_path: Path, username: str) -> HermesTarget:
    home = tmp_path / f"home-{username}"
    home.mkdir(exist_ok=True)
    return HermesTarget(
        username=username,
        email=f"{username}@example.test",
        display_name=username,
        linux_user=username,
        home_dir=home,
        hermes_home=home / ".hermes",
        workdir=home,
        api_server_host="127.0.0.1",
        api_port=8600,
        api_key="",
        api_server_model_name="Hermes",
        systemd_service=f"hermes-{username}.service",
        extra_env={},
        config_overrides={},
    )


def _write_unit(path: Path, content: bytes = b"old unit\n") -> None:
    path.write_bytes(content)
    path.chmod(0o644)


def _prepare(tmp_path: Path, targets: list[HermesTarget]):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir(exist_ok=True)
    for target in targets:
        _write_unit(unit_dir / target.systemd_service)
    candidates = refresh.prepare_candidates(
        {"hermes": {}},
        targets,
        unit_dir=unit_dir,
        expected_uid=os.geteuid(),
    )
    return unit_dir, candidates


def test_parser_defaults_to_read_only_check():
    args = refresh.build_parser().parse_args(["--all"])

    assert not args.apply
    assert args.all


def test_refresh_lock_is_exclusive_and_nonblocking(tmp_path):
    lock_path = tmp_path / "run" / "unit-refresh" / "hermes.lock"

    with refresh._refresh_lock(
        lock_path,
        expected_uid=os.geteuid(),
        expected_gid=os.getegid(),
        validate_ancestors=False,
    ):
        with pytest.raises(refresh.UnitRefreshError, match="already running"):
            with refresh._refresh_lock(
                lock_path,
                expected_uid=os.geteuid(),
                expected_gid=os.getegid(),
                validate_ancestors=False,
            ):
                pytest.fail("second lock unexpectedly acquired")


def test_incomplete_transaction_blocks_future_apply(tmp_path):
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o700)
    transaction_dir = backup_root / "transaction"
    transaction_dir.mkdir(mode=0o700)
    manifest = transaction_dir / "transaction.json"
    manifest.write_text('{"status":"prepared"}\n', encoding="utf-8")
    manifest.chmod(0o600)

    with pytest.raises(refresh.UnitRefreshError, match="incomplete"):
        refresh._assert_no_incomplete_transactions(
            backup_root,
            expected_uid=os.geteuid(),
            validate_ancestors=False,
        )

    manifest.write_text('{"status":"complete"}\n', encoding="utf-8")
    manifest.chmod(0o600)
    refresh._assert_no_incomplete_transactions(
        backup_root,
        expected_uid=os.geteuid(),
        validate_ancestors=False,
    )


def test_validate_target_set_rejects_duplicate_and_unsafe_names(tmp_path):
    first = _target(tmp_path, "one")
    duplicate = replace(_target(tmp_path, "two"), systemd_service=first.systemd_service)

    with pytest.raises(refresh.UnitRefreshError, match="duplicate"):
        refresh.validate_target_set(
            [first, duplicate],
            unit_dir=tmp_path,
            expected_count=None,
            require_existing_set=False,
        )

    unsafe = replace(first, systemd_service="../hermes-one.service")
    with pytest.raises(refresh.UnitRefreshError, match="unsafe"):
        refresh.validate_target_set(
            [unsafe],
            unit_dir=tmp_path,
            expected_count=None,
            require_existing_set=False,
        )


def test_validate_target_set_requires_exact_existing_inventory(tmp_path):
    target = _target(tmp_path, "one")
    (tmp_path / target.systemd_service).write_text("unit", encoding="utf-8")
    (tmp_path / "hermes-orphan.service").write_text("unit", encoding="utf-8")

    with pytest.raises(refresh.UnitRefreshError, match="unmapped"):
        refresh.validate_target_set(
            [target],
            unit_dir=tmp_path,
            expected_count=1,
            require_existing_set=True,
        )


def test_unit_directory_rejects_symlink_and_writable_directory(tmp_path):
    real = tmp_path / "real-units"
    real.mkdir()
    linked = tmp_path / "linked-units"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(refresh.UnitRefreshError, match="non-symlink"):
        refresh._validate_unit_directory(linked, expected_uid=os.geteuid())

    real.chmod(0o777)
    with pytest.raises(refresh.UnitRefreshError, match="writable"):
        refresh._validate_unit_directory(real, expected_uid=os.geteuid())


def test_runtime_profile_accepts_managed_current_release_symlink(tmp_path):
    releases = tmp_path / "releases"
    release = releases / "release-1"
    profile = release / "config" / "runtime-profile.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("schema_version: 1\n", encoding="utf-8")
    releases.chmod(0o755)
    profile.chmod(0o644)
    current = tmp_path / "current"
    current.symlink_to(release, target_is_directory=True)

    resolved = refresh._validate_trusted_runtime_profile(
        current / "config" / "runtime-profile.yaml",
        current_path=current,
        releases_dir=releases,
        expected_uid=os.geteuid(),
        validate_ancestors=False,
    )

    assert resolved == profile


@pytest.mark.parametrize("target_kind", ["outside", "nested"])
def test_runtime_profile_rejects_current_outside_direct_release(
    tmp_path, target_kind
):
    releases = tmp_path / "releases"
    releases.mkdir()
    releases.chmod(0o755)
    if target_kind == "outside":
        release = tmp_path / "outside"
    else:
        release = releases / "nested" / "release-1"
    profile = release / "config" / "runtime-profile.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text("schema_version: 1\n", encoding="utf-8")
    profile.chmod(0o644)
    current = tmp_path / "current"
    current.symlink_to(release, target_is_directory=True)

    with pytest.raises(refresh.UnitRefreshError, match="direct child"):
        refresh._validate_trusted_runtime_profile(
            current / "config" / "runtime-profile.yaml",
            current_path=current,
            releases_dir=releases,
            expected_uid=os.geteuid(),
            validate_ancestors=False,
        )


def test_runtime_profile_rejects_unmanaged_symlink(tmp_path):
    profile = tmp_path / "runtime-profile.yaml"
    profile.write_text("schema_version: 1\n", encoding="utf-8")
    linked = tmp_path / "linked-profile.yaml"
    linked.symlink_to(profile)

    with pytest.raises(refresh.UnitRefreshError, match="non-symlink"):
        refresh._validate_trusted_runtime_profile(
            linked,
            current_path=tmp_path / "current",
            releases_dir=tmp_path / "releases",
            expected_uid=os.geteuid(),
            validate_ancestors=False,
        )


def test_prepare_rejects_symlink_hardlink_and_wrong_owner(tmp_path):
    target = _target(tmp_path, "one")
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    real = unit_dir / "real.service"
    _write_unit(real)
    unit_path = unit_dir / target.systemd_service
    unit_path.symlink_to(real)

    with pytest.raises(refresh.UnitRefreshError, match="non-symlink"):
        refresh.prepare_candidates(
            {"hermes": {}},
            [target],
            unit_dir=unit_dir,
            expected_uid=os.geteuid(),
        )

    unit_path.unlink()
    os.link(real, unit_path)
    with pytest.raises(refresh.UnitRefreshError, match="hard link"):
        refresh.prepare_candidates(
            {"hermes": {}},
            [target],
            unit_dir=unit_dir,
            expected_uid=os.geteuid(),
        )

    unit_path.unlink()
    real.unlink()
    _write_unit(unit_path)
    with pytest.raises(refresh.UnitRefreshError, match="owner"):
        refresh.prepare_candidates(
            {"hermes": {}},
            [target],
            unit_dir=unit_dir,
            expected_uid=os.geteuid() + 1,
        )


def test_read_only_diff_does_not_modify_unit(tmp_path):
    target = _target(tmp_path, "one")
    unit_dir, candidates = _prepare(tmp_path, [target])
    before = (unit_dir / target.systemd_service).read_bytes()

    diff = refresh.render_diff(candidates[0])

    assert "HERMES_RUNTIME_PROFILE_PATH" in diff
    assert (unit_dir / target.systemd_service).read_bytes() == before


def test_apply_updates_units_once_and_preserves_home(tmp_path):
    targets = [_target(tmp_path, "one"), _target(tmp_path, "two")]
    unit_dir, candidates = _prepare(tmp_path, targets)
    sentinel = targets[0].home_dir / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    reload_calls = []

    backup_dir = refresh.apply_candidates(
        candidates,
        backup_root=tmp_path / "backups",
        expected_uid=os.geteuid(),
        expected_gid=os.getegid(),
        expected_mode=0o644,
        owner_uid=os.geteuid(),
        owner_gid=os.getegid(),
        reload_fn=lambda: reload_calls.append("reload"),
    )

    assert backup_dir is not None
    assert reload_calls == ["reload"]
    manifest = json.loads((backup_dir / "transaction.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    for candidate in candidates:
        assert candidate.path.read_bytes() == candidate.desired
        assert (candidate.path.stat().st_mode & 0o777) == 0o644
        assert (backup_dir / candidate.path.name).read_bytes() == b"old unit\n"


def test_apply_rejects_concurrent_change_before_writing(tmp_path):
    target = _target(tmp_path, "one")
    _, candidates = _prepare(tmp_path, [target])
    candidates[0].path.write_text("changed concurrently\n", encoding="utf-8")

    with pytest.raises(refresh.UnitRefreshError, match="changed before commit"):
        refresh.apply_candidates(
            candidates,
            backup_root=tmp_path / "backups",
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o644,
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
        )

    assert candidates[0].path.read_text(encoding="utf-8") == "changed concurrently\n"


def test_apply_rejects_insecure_existing_backup_root(tmp_path):
    target = _target(tmp_path, "one")
    _, candidates = _prepare(tmp_path, [target])
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o755)

    with pytest.raises(refresh.UnitRefreshError, match="mode 0700"):
        refresh.apply_candidates(
            candidates,
            backup_root=backup_root,
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o644,
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
        )


def test_apply_rolls_back_first_unit_when_second_exchange_fails(tmp_path, monkeypatch):
    targets = [_target(tmp_path, "one"), _target(tmp_path, "two")]
    _, candidates = _prepare(tmp_path, targets)
    real_exchange = refresh._rename_exchange
    failed = False

    def fail_second_once(source, destination):
        nonlocal failed
        if Path(destination) == candidates[1].path and not failed:
            failed = True
            raise OSError("injected replace failure")
        return real_exchange(source, destination)

    monkeypatch.setattr(refresh, "_rename_exchange", fail_second_once)

    with pytest.raises(refresh.UnitRefreshError, match="rolled back"):
        refresh.apply_candidates(
            candidates,
            backup_root=tmp_path / "backups",
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o644,
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
        )

    assert failed
    for candidate in candidates:
        assert candidate.path.read_bytes() == b"old unit\n"
    assert not list(candidates[0].path.parent.glob(".*.runtime-profile.*"))
    transaction_dir = next((tmp_path / "backups").iterdir())
    manifest = json.loads(
        (transaction_dir / "transaction.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "rolled_back"


def test_atomic_exchange_preserves_update_between_check_and_swap(tmp_path, monkeypatch):
    target = _target(tmp_path, "one")
    _, candidates = _prepare(tmp_path, [target])
    real_exchange = refresh._rename_exchange
    injected = False

    def inject_update_before_exchange(source, destination):
        nonlocal injected
        if not injected:
            injected = True
            Path(destination).write_text("administrator update\n", encoding="utf-8")
            Path(destination).chmod(0o600)
        return real_exchange(source, destination)

    monkeypatch.setattr(refresh, "_rename_exchange", inject_update_before_exchange)

    with pytest.raises(refresh.UnitRefreshError, match="concurrent change"):
        refresh.apply_candidates(
            candidates,
            backup_root=tmp_path / "backups",
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o644,
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
        )

    assert candidates[0].path.read_text(encoding="utf-8") == "administrator update\n"
    assert (candidates[0].path.stat().st_mode & 0o777) == 0o600


def test_atomic_exchange_preserves_second_update_during_restore(tmp_path, monkeypatch):
    target = _target(tmp_path, "one")
    _, candidates = _prepare(tmp_path, [target])
    real_exchange = refresh._rename_exchange
    exchange_count = 0

    def inject_two_updates(source, destination):
        nonlocal exchange_count
        exchange_count += 1
        if exchange_count == 1:
            Path(destination).write_text("first administrator update\n", encoding="utf-8")
            Path(destination).chmod(0o600)
        elif exchange_count == 2:
            Path(destination).write_text("second administrator update\n", encoding="utf-8")
            Path(destination).chmod(0o640)
        return real_exchange(source, destination)

    monkeypatch.setattr(refresh, "_rename_exchange", inject_two_updates)

    with pytest.raises(refresh.UnitRefreshError, match="preserved exchange entries"):
        refresh.apply_candidates(
            candidates,
            backup_root=tmp_path / "backups",
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o644,
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
        )

    assert exchange_count == 3
    assert candidates[0].path.read_text(encoding="utf-8") == (
        "second administrator update\n"
    )
    assert (candidates[0].path.stat().st_mode & 0o777) == 0o640
    preserved = list(candidates[0].path.parent.glob(".*.runtime-profile.*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "first administrator update\n"


def test_rollback_preserves_update_after_first_swap(tmp_path, monkeypatch):
    targets = [_target(tmp_path, "one"), _target(tmp_path, "two")]
    _, candidates = _prepare(tmp_path, targets)
    real_exchange = refresh._rename_exchange
    injected = False

    def fail_second_after_admin_update(source, destination):
        nonlocal injected
        if Path(destination) == candidates[1].path and not injected:
            injected = True
            candidates[0].path.write_text("administrator update\n", encoding="utf-8")
            candidates[0].path.chmod(0o600)
            raise OSError("injected second exchange failure")
        return real_exchange(source, destination)

    monkeypatch.setattr(refresh, "_rename_exchange", fail_second_after_admin_update)

    with pytest.raises(refresh.UnitRefreshError, match="preserved exchange entries"):
        refresh.apply_candidates(
            candidates,
            backup_root=tmp_path / "backups",
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o644,
            owner_uid=os.geteuid(),
            owner_gid=os.getegid(),
        )

    assert candidates[0].path.read_text(encoding="utf-8") == "administrator update\n"
    assert (candidates[0].path.stat().st_mode & 0o777) == 0o600
    assert candidates[1].path.read_bytes() == b"old unit\n"
    transaction_dir = next((tmp_path / "backups").iterdir())
    manifest = json.loads(
        (transaction_dir / "transaction.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "rollback_conflict"


def test_apply_requires_root_before_reading_mapping(monkeypatch):
    monkeypatch.setattr(refresh.os, "geteuid", lambda: 1000)

    with pytest.raises(refresh.UnitRefreshError, match="must run as root"):
        refresh.main(["--apply", "--all", "--expect-count", "1", "--require-existing-set"])

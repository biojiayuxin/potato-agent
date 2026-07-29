from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from interface import user_private_files
from interface.mapping import HermesTarget


def _target(tmp_path: Path) -> HermesTarget:
    home = tmp_path / "home"
    return HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=home,
        hermes_home=home / ".hermes",
        workdir=home / "work",
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="user-api-key",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )


def _password_entry(target: HermesTarget) -> SimpleNamespace:
    return SimpleNamespace(
        pw_uid=os.geteuid(),
        pw_gid=os.getegid(),
        pw_dir=str(target.home_dir),
    )


def test_runtime_paths_reject_hermes_home_outside_account_home(tmp_path) -> None:
    target = _target(tmp_path)
    escaped = HermesTarget(
        **{
            **target.__dict__,
            "hermes_home": tmp_path / "outside",
        }
    )

    with pytest.raises(
        user_private_files.UserPrivateFileError,
        match="Hermes home must remain inside",
    ):
        user_private_files.validate_user_runtime_paths(
            escaped, _password_entry(target)
        )


def test_runtime_paths_reject_mismatched_passwd_home(tmp_path) -> None:
    target = _target(tmp_path)
    password_entry = _password_entry(target)
    password_entry.pw_dir = str(tmp_path / "another-home")

    with pytest.raises(
        user_private_files.UserPrivateFileError,
        match="does not match the Linux account home",
    ):
        user_private_files.validate_user_runtime_paths(target, password_entry)


def test_runtime_paths_reject_workdir_outside_account_home(tmp_path) -> None:
    target = _target(tmp_path)
    escaped = HermesTarget(
        **{
            **target.__dict__,
            "workdir": tmp_path / "outside-workdir",
        }
    )

    with pytest.raises(
        user_private_files.UserPrivateFileError,
        match="Working directory must remain inside",
    ):
        user_private_files.validate_user_runtime_paths(
            escaped, _password_entry(target)
        )


@pytest.mark.skipif(os.name == "nt", reason="requires O_NOFOLLOW")
def test_private_read_rejects_final_symlink(tmp_path) -> None:
    target = _target(tmp_path)
    user_private_files.prepare_user_runtime_directories(
        target, _password_entry(target)
    )
    outside = tmp_path / "outside.yaml"
    outside.write_text("secret: outside\n", encoding="utf-8")
    link = target.hermes_home / "config.yaml"
    link.symlink_to(outside)

    with pytest.raises(user_private_files.UserPrivateFileError):
        user_private_files.read_user_private_text(target, link)


def test_prepare_runtime_directories_creates_private_temp_root(tmp_path) -> None:
    target = _target(tmp_path)

    user_private_files.prepare_user_runtime_directories(
        target, _password_entry(target)
    )

    temp_root = target.hermes_home / "tmp"
    assert temp_root.is_dir()
    assert temp_root.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
def test_direct_private_read_rejects_fifo_without_blocking(tmp_path) -> None:
    fifo = tmp_path / "config.yaml"
    os.mkfifo(fifo)

    with pytest.raises(
        user_private_files.UserPrivateFileError,
        match="Unsafe private user file",
    ):
        user_private_files._direct_read(fifo)


def test_private_write_rejects_existing_symlink_without_touching_target(
    tmp_path,
) -> None:
    target = _target(tmp_path)
    user_private_files.prepare_user_runtime_directories(
        target, _password_entry(target)
    )
    outside = tmp_path / "outside.env"
    outside.write_text("DO_NOT_CHANGE=1\n", encoding="utf-8")
    link = target.hermes_home / ".env"
    link.symlink_to(outside)

    with pytest.raises(
        user_private_files.UserPrivateFileError,
        match="must remain inside the Hermes home",
    ):
        user_private_files.write_user_private_text(target, link, "SAFE=1\n")

    assert link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "DO_NOT_CHANGE=1\n"


def test_private_write_replaces_symlink_created_after_path_validation(
    monkeypatch, tmp_path
) -> None:
    target = _target(tmp_path)
    user_private_files.prepare_user_runtime_directories(
        target, _password_entry(target)
    )
    outside = tmp_path / "outside.env"
    outside.write_text("DO_NOT_CHANGE=1\n", encoding="utf-8")
    link = target.hermes_home / ".env"
    link.symlink_to(outside)
    monkeypatch.setattr(
        user_private_files,
        "validate_user_private_path",
        lambda _target, _path: None,
    )

    user_private_files.write_user_private_text(target, link, "SAFE=1\n")

    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8") == "SAFE=1\n"
    assert outside.read_text(encoding="utf-8") == "DO_NOT_CHANGE=1\n"
    assert link.stat().st_mode & 0o777 == 0o600


def test_root_worker_receives_private_body_only_on_stdin(monkeypatch, tmp_path) -> None:
    target = _target(tmp_path)
    sentinel = "private-config-body-7e4d2a"
    captured: dict[str, object] = {}

    monkeypatch.setattr(user_private_files.os, "geteuid", lambda: 0)

    def fake_worker(linux_user, action, *arguments, input_bytes=b""):
        captured["command"] = user_private_files._worker_command(
            linux_user, action, *arguments
        )
        captured["input"] = input_bytes
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(user_private_files, "_run_worker", fake_worker)

    user_private_files.write_user_private_text(
        target,
        target.hermes_home / "config.yaml",
        sentinel,
    )

    command = "\0".join(str(item) for item in captured["command"])
    assert sentinel not in command
    assert captured["input"] == sentinel.encode("utf-8")


def test_root_worker_timeout_fails_closed(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(user_private_files.subprocess, "run", timeout)

    with pytest.raises(
        user_private_files.UserPrivateFileError,
        match="operation timed out",
    ):
        user_private_files._run_worker("hmx_alice", "read", "/unused")


def test_permission_repair_rejects_symlink(tmp_path) -> None:
    target = _target(tmp_path)
    password_entry = _password_entry(target)
    user_private_files.prepare_user_runtime_directories(target, password_entry)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"unchanged")
    link = target.hermes_home / "state.db"
    link.symlink_to(outside)

    with pytest.raises(user_private_files.UserPrivateFileError):
        user_private_files.repair_user_private_file(target, password_entry, link)

    assert outside.read_bytes() == b"unchanged"


def test_permission_repair_rejects_hard_link(tmp_path) -> None:
    target = _target(tmp_path)
    password_entry = _password_entry(target)
    user_private_files.prepare_user_runtime_directories(target, password_entry)
    original = target.hermes_home / "original.db"
    original.write_bytes(b"unchanged")
    linked = target.hermes_home / "state.db"
    os.link(original, linked)

    with pytest.raises(
        user_private_files.UserPrivateFileError,
        match="Unsafe private user file",
    ):
        user_private_files.repair_user_private_file(
            target, password_entry, linked
        )

    assert original.read_bytes() == b"unchanged"

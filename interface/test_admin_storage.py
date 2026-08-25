from __future__ import annotations

import json
import os
import pwd
import sqlite3
import subprocess
from pathlib import Path

import pytest

from interface import admin_store, auth_db, privileged_helper, storage_snapshot_job
from interface.home_usage import (
    HomeUsageError,
    build_home_usage_command,
    measure_home_allocated_bytes,
)
from interface.mapping import HermesTarget
from interface.privileged_client import PrivilegedClientError


REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = REPO_ROOT / "packaging" / "systemd"


def _target(home: Path) -> HermesTarget:
    username = pwd.getpwuid(os.getuid()).pw_name
    return HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user=username,
        home_dir=home,
        hermes_home=home / ".hermes",
        workdir=home,
        api_server_host="127.0.0.1",
        api_port=8000,
        api_key="key",
        api_server_model_name="model",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )


def test_home_usage_command_is_uid_scoped_low_priority_and_same_filesystem(
    tmp_path,
) -> None:
    command = build_home_usage_command(_target(tmp_path))
    assert command[:3] == ["runuser", "-u", pwd.getpwuid(os.getuid()).pw_name]
    assert ["ionice", "-c", "3", "du", "-s", "-x", "-B1"] == command[8:15]
    assert command[-2:] == ["--", str(tmp_path)]


def test_home_usage_rejects_symlink_and_parses_only_byte_count(
    tmp_path, monkeypatch
) -> None:
    real_home = tmp_path / "real"
    real_home.mkdir()
    linked_home = tmp_path / "linked"
    linked_home.symlink_to(real_home, target_is_directory=True)
    with pytest.raises(HomeUsageError):
        build_home_usage_command(_target(linked_home))

    monkeypatch.setattr(
        "interface.home_usage.run_process_group",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=f"4096\t{real_home}\n", stderr=""
        ),
    )
    assert measure_home_allocated_bytes(_target(real_home)) == 4096


def _user(db_path, username: str, *, temporary: bool = False):
    if temporary:
        return auth_db.create_temporary_user(
            username=username,
            email=f"{username}@temporary.example",
            password="Password1!",
            mapping_username=username,
            db_path=db_path,
        )
    return auth_db.upsert_user(
        username=username,
        email=f"{username}@example.com",
        password="Password1!",
        mapping_username=username,
        db_path=db_path,
    )


def test_daily_snapshot_includes_active_temp_and_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    _user(db_path, "alice")
    _user(db_path, "temp_1234567890_0123abcd", temporary=True)
    monkeypatch.setattr(
        storage_snapshot_job.privileged_client,
        "home_usage",
        lambda username: 100 if username == "alice" else 200,
    )
    first = storage_snapshot_job.run_storage_snapshot(now=100, db_path=db_path)
    second = storage_snapshot_job.run_storage_snapshot(now=100, db_path=db_path)
    assert first == {"candidates": 2, "saved": 2, "errors": 0, "skipped_race": 0}
    assert second == first
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "select allocated_bytes from user_storage_snapshots order by allocated_bytes"
        ).fetchall()
    assert rows == [(100,), (200,)]


def test_snapshot_write_recheck_handles_cleanup_race(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = _user(db_path, "temp_1234567890_0123abcd", temporary=True)

    def cleanup_during_measurement(_username: str) -> int:
        auth_db.delete_user_by_id(user.id, db_path=db_path)
        return 123

    monkeypatch.setattr(
        storage_snapshot_job.privileged_client,
        "home_usage",
        cleanup_during_measurement,
    )
    result = storage_snapshot_job.run_storage_snapshot(now=100, db_path=db_path)
    assert result["saved"] == 0
    assert result["skipped_race"] == 1


def test_snapshot_records_non_sensitive_timeout_code(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = _user(db_path, "alice")
    monkeypatch.setattr(
        storage_snapshot_job.privileged_client,
        "home_usage",
        lambda _username: (_ for _ in ()).throw(
            PrivilegedClientError("privileged helper timed out")
        ),
    )
    result = storage_snapshot_job.run_storage_snapshot(now=100, db_path=db_path)
    assert result["errors"] == 1
    entities = admin_store.list_overview_entities(db_path=db_path)
    snapshot = next(item["storage"] for item in entities if item["user_id"] == user.id)
    assert snapshot["status"] == "error"
    assert snapshot["error_code"] == "timeout"


def test_helper_home_usage_error_output_is_redacted(
    tmp_path, monkeypatch, capsys
) -> None:
    sensitive_path = tmp_path / "secret-home"
    monkeypatch.setattr("sys.argv", ["privileged-helper", "home-usage", "--username", "alice"])
    monkeypatch.setattr(privileged_helper, "require_root", lambda: None)
    monkeypatch.setattr(privileged_helper, "require_binary", lambda _name: None)
    monkeypatch.setattr(
        privileged_helper,
        "_load_target",
        lambda _username: _target(tmp_path),
    )
    monkeypatch.setattr(
        privileged_helper,
        "measure_home_allocated_bytes",
        lambda _target: (_ for _ in ()).throw(HomeUsageError(str(sensitive_path))),
    )
    assert privileged_helper.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": False,
        "error": "home usage unavailable",
        "error_code": "unavailable",
    }
    assert str(sensitive_path) not in json.dumps(payload)


def test_storage_timer_and_admin_usage_credentials_are_hardened_templates() -> None:
    service = (SYSTEMD_ROOT / "potato-storage-snapshot.service").read_text()
    timer = (SYSTEMD_ROOT / "potato-storage-snapshot.timer").read_text()
    interface_unit = (SYSTEMD_ROOT / "potato-interface.service").read_text()
    proxy_unit = (SYSTEMD_ROOT / "potato-model-proxy.service").read_text()
    credential = (
        "LoadCredential=admin-usage-token:"
        "/etc/potato-agent/credentials/admin-usage-token"
    )
    assert "User=potato-interface" in service
    assert "-m interface.storage_snapshot_job run" in service
    assert "ProtectProc=invisible" in service
    assert "ProcSubset=pid" in service
    assert "OnCalendar=*-*-* 04:00:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
    assert credential in interface_unit
    assert credential in proxy_unit
    assert "POTATO_ADMIN_USAGE_TOKEN=" not in interface_unit
    assert "POTATO_ADMIN_USAGE_TOKEN=" not in proxy_unit

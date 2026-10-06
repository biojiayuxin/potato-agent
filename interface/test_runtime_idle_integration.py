"""Exercise the idle scheduler, fresh helper process, auth polling and admin state.

Only OS service/account operations are stubbed; all state is in disposable DBs.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from interface.test_temporary_users import _load_app


def test_idle_sweep_updates_auth_and_admin_for_all_account_types(tmp_path, monkeypatch):
    client, app, auth, db_path = _load_app(tmp_path, monkeypatch)
    runtime = importlib.import_module("interface.runtime_state")
    users = {}
    for name in ("overdue", "recent", "leased", "background", "temp_overdue", "temp_retired"):
        create = auth.create_temporary_user if name.startswith("temp_") else auth.upsert_user
        users[name] = create(
            username=name, email=f"{name}@example.test", password="test-password",
            mapping_username=name, db_path=db_path,
        )
        app.privileged_client.provision_user(name)
        runtime.mark_runtime_started(users[name].id)

    tokens = {name: app._create_session_token(user.id) for name, user in users.items()}
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "update runtime_state set runtime_started_at=?, last_user_message_at=?",
            (now - 3600, now - 3600),
        )
        conn.execute("update temporary_users set created_at=?", (now - 3600,))
        conn.execute(
            "update runtime_state set last_user_message_at=? where user_id=?",
            (now - 60, users["recent"].id),
        )
    runtime.create_runtime_lease(
        users["leased"].id, lease_type="foreground_chat", ttl_seconds=90,
    )
    auth.mark_temporary_user_cleanup_attempt(
        users["temp_retired"].id, status="failed", now=now - 120,
    )
    auth.retire_temporary_user_identity(users["temp_retired"].id)

    # Auth polling, as used by idle browser tabs, must not renew the baseline.
    for name in ("overdue", "temp_overdue"):
        before = runtime.get_runtime_state(users[name].id)
        client.cookies.set(app.SESSION_COOKIE_NAME, tokens[name])
        assert client.get("/api/auth/session").json()["authenticated"] is True
        assert runtime.get_runtime_state(users[name].id) == before

    helper_source = """
import contextlib
from types import SimpleNamespace
from interface import privileged_helper as helper
helper.require_root = lambda: None
helper.require_binary = lambda name: None
helper._load_target = lambda name: SimpleNamespace(username=name, systemd_service=name)
helper.service_operation_lock = lambda name: contextlib.nullcontext()
helper.has_active_background_processes = lambda target: target.username == 'background'
helper.is_service_active = lambda name: True
helper.stop_service = lambda name: None
raise SystemExit(helper.main())
"""
    repo = Path(__file__).resolve().parents[1]
    helper_calls = []

    def stop_idle_runtime(username, user_id, idle_timeout_seconds):
        assert idle_timeout_seconds == 1800
        helper_calls.append(username)
        # A fresh process exercises DB initialization with a retired temporary
        # identity still present, just as production's sudo helper does.
        result = subprocess.run(
            [sys.executable, "-B", "-c", helper_source, "stop-idle-runtime",
             "--username", username, "--user-id", user_id,
             "--idle-timeout-seconds", str(idle_timeout_seconds)],
            cwd=repo,
            env={**os.environ, "INTERFACE_AUTH_DB": str(db_path),
                 "POTATO_AGENT_REPO_ROOT": str(repo), "PYTHONPATH": str(repo)},
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    real_exists = app.os.path.exists
    monkeypatch.setattr(
        app.os.path, "exists",
        lambda path: True if str(path).startswith("/etc/systemd/system/hermes-")
        else real_exists(path),
    )
    monkeypatch.setattr(app, "RUNTIME_IDLE_TIMEOUT_SECONDS", 1800)
    monkeypatch.setattr(app.privileged_client, "force_helper", True)
    monkeypatch.setattr(app.privileged_client, "stop_idle_runtime", stop_idle_runtime)
    monkeypatch.setattr(app, "_has_active_background_processes_for_target", lambda target: False)
    removed = []
    monkeypatch.setattr(
        app.privileged_client, "deprovision_user",
        lambda name, *, delete_home: removed.append(name),
    )
    monkeypatch.setattr(app.privileged_client, "remove_mapping", lambda name: None)

    assert asyncio.run(app._run_runtime_idle_check_once()) == 3
    assert set(helper_calls) == {"overdue", "background"}
    assert set(removed) == {"temp_overdue", "temp_retired"}
    assert runtime.list_active_runtime_user_ids() == {
        users[name].id for name in ("recent", "leased", "background")
    }
    assert runtime.get_runtime_state(users["overdue"].id)["last_sleep_reason"] == "idle_timeout"
    assert runtime.get_runtime_state(users["background"].id)["last_background_activity_at"] >= now
    for name in users:
        client.cookies.set(app.SESSION_COOKIE_NAME, tokens[name])
        assert client.get("/api/auth/session").json()["authenticated"] is (
            name in {"recent", "leased", "background"}
        )


@pytest.mark.parametrize("temporary", [False, True])
@pytest.mark.parametrize("idle_seconds,expected", [(1799, False), (1800, True), (1801, True)])
def test_thirty_minute_boundary_for_both_account_types(
    tmp_path, monkeypatch, temporary, idle_seconds, expected,
):
    _, _, auth, db_path = _load_app(tmp_path, monkeypatch)
    runtime = importlib.import_module("interface.runtime_state")
    now = int(time.time())
    monkeypatch.setattr(runtime, "_now", lambda: now)
    create = auth.create_temporary_user if temporary else auth.upsert_user
    user = create(
        username="boundary", email="boundary@example.test", password="test-password",
        mapping_username="boundary", db_path=db_path,
    )
    runtime.mark_runtime_started(user.id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "update runtime_state set runtime_started_at=?, last_user_message_at=?",
            (now - idle_seconds, now - idle_seconds),
        )
        conn.execute("update temporary_users set created_at=?", (now - idle_seconds,))
    candidates = (
        runtime.list_idle_temporary_user_candidates(
            idle_timeout_seconds=1800, cleanup_retry_seconds=60,
        ) if temporary else runtime.list_idle_runtime_candidates(idle_timeout_seconds=1800)
    )
    assert bool(candidates) is expected

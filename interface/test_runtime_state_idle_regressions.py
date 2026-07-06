from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import interface.auth_db as auth_db
import interface.display_store as display_store
from interface.runtime_state import (
    create_runtime_lease,
    delete_runtime_state,
    ensure_runtime_state_store,
    get_runtime_state,
    list_idle_temporary_user_candidates,
    list_idle_runtime_candidates,
    mark_foreground_activity,
    mark_runtime_started,
    mark_user_message_activity,
    release_runtime_lease,
)


def _temp_db_path() -> Path:
    return Path(tempfile.mkdtemp(prefix="potato-runtime-state-test-")) / "interface.db"


def test_foreground_activity_resets_idle_baseline_after_long_turn() -> None:
    db_path = _temp_db_path()
    user_id = "user-1"
    ensure_runtime_state_store(db_path)

    mark_runtime_started(user_id, db_path=db_path)
    mark_user_message_activity(user_id, db_path=db_path)
    lease_id = create_runtime_lease(
        user_id,
        lease_type="foreground_chat",
        ttl_seconds=90,
        resource_id="live-1",
        db_path=db_path,
    )

    old_activity_at = int(time.time()) - 3600
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user_id),
        )
        conn.commit()

    assert not list_idle_runtime_candidates(
        idle_timeout_seconds=30 * 60,
        db_path=db_path,
    )

    mark_foreground_activity(user_id, db_path=db_path)
    release_runtime_lease(lease_id, db_path=db_path)

    assert not list_idle_runtime_candidates(
        idle_timeout_seconds=30 * 60,
        db_path=db_path,
    )


def test_idle_check_stops_runtime_and_revokes_session(monkeypatch) -> None:
    db_path = _temp_db_path()
    user_id = "user-1"
    ensure_runtime_state_store(db_path)
    mark_runtime_started(user_id, db_path=db_path)
    old_activity_at = int(time.time()) - 3600
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user_id),
        )
        conn.commit()

    import interface.app as app_mod

    monkeypatch.setattr(app_mod, "RUNTIME_IDLE_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(app_mod, "cleanup_expired_runtime_leases", lambda: 0)
    monkeypatch.setattr(
        app_mod,
        "list_idle_runtime_candidates",
        lambda *, idle_timeout_seconds: list_idle_runtime_candidates(
            idle_timeout_seconds=idle_timeout_seconds,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "list_idle_temporary_user_candidates",
        lambda *, idle_timeout_seconds, cleanup_retry_seconds: [],
    )
    monkeypatch.setattr(app_mod, "is_temporary_user", lambda checked_user_id: False)
    monkeypatch.setattr(
        app_mod,
        "has_active_runtime_leases",
        lambda checked_user_id: False,
    )
    monkeypatch.setattr(
        app_mod,
        "revoke_runtime_session",
        lambda checked_user_id, *, reason: __import__(
            "interface.runtime_state", fromlist=["revoke_runtime_session"]
        ).revoke_runtime_session(checked_user_id, reason=reason, db_path=db_path),
    )
    monkeypatch.setattr(
        app_mod,
        "list_users",
        lambda: [
            SimpleNamespace(
                id=user_id,
                username="alice",
                email="alice@example.com",
                mapping_username="alice",
            )
        ],
    )
    monkeypatch.setattr(
        app_mod.mapping_store,
        "resolve_target",
        lambda **kwargs: SimpleNamespace(
            username="alice",
            systemd_service="hermes-alice.service",
        ),
    )
    monkeypatch.setattr(app_mod.os.path, "exists", lambda path: True)
    monkeypatch.setattr(app_mod.os, "geteuid", lambda: 1000)

    stopped: list[str] = []

    def fake_stop_idle_runtime(username: str, checked_user_id: str) -> dict[str, bool]:
        stopped.append(username)
        __import__(
            "interface.runtime_state", fromlist=["revoke_runtime_session"]
        ).revoke_runtime_session(
            checked_user_id,
            reason="idle_timeout",
            db_path=db_path,
        )
        return {"stopped": True}

    monkeypatch.setattr(
        app_mod.privileged_client,
        "stop_idle_runtime",
        fake_stop_idle_runtime,
    )

    import asyncio

    assert asyncio.run(app_mod._run_runtime_idle_check_once()) == 1
    assert stopped == ["alice"]
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT session_revoked_after, last_sleep_reason FROM runtime_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    assert row[0] > 0
    assert row[1] == "idle_timeout"


def test_idle_check_revokes_session_when_service_already_inactive(monkeypatch) -> None:
    db_path = _temp_db_path()
    user_id = "user-1"
    ensure_runtime_state_store(db_path)
    mark_runtime_started(user_id, db_path=db_path)
    old_activity_at = int(time.time()) - 3600
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user_id),
        )
        conn.commit()

    import interface.app as app_mod

    monkeypatch.setattr(app_mod, "RUNTIME_IDLE_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(app_mod, "cleanup_expired_runtime_leases", lambda: 0)
    monkeypatch.setattr(
        app_mod,
        "list_idle_runtime_candidates",
        lambda *, idle_timeout_seconds: list_idle_runtime_candidates(
            idle_timeout_seconds=idle_timeout_seconds,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "list_idle_temporary_user_candidates",
        lambda *, idle_timeout_seconds, cleanup_retry_seconds: [],
    )
    monkeypatch.setattr(app_mod, "is_temporary_user", lambda checked_user_id: False)
    monkeypatch.setattr(
        app_mod,
        "revoke_runtime_session",
        lambda checked_user_id, *, reason: __import__(
            "interface.runtime_state", fromlist=["revoke_runtime_session"]
        ).revoke_runtime_session(checked_user_id, reason=reason, db_path=db_path),
    )
    monkeypatch.setattr(
        app_mod,
        "list_users",
        lambda: [
            SimpleNamespace(
                id=user_id,
                username="alice",
                email="alice@example.com",
                mapping_username="alice",
            )
        ],
    )
    monkeypatch.setattr(
        app_mod.mapping_store,
        "resolve_target",
        lambda **kwargs: SimpleNamespace(
            username="alice",
            systemd_service="hermes-alice.service",
        ),
    )
    monkeypatch.setattr(app_mod.os.path, "exists", lambda path: True)
    monkeypatch.setattr(app_mod.os, "geteuid", lambda: 1000)
    def fake_stop_inactive_service(username: str, checked_user_id: str) -> dict[str, str | bool]:
        __import__(
            "interface.runtime_state", fromlist=["revoke_runtime_session"]
        ).revoke_runtime_session(
            checked_user_id,
            reason="idle_timeout",
            db_path=db_path,
        )
        return {"stopped": True, "reason": "service_inactive"}

    monkeypatch.setattr(
        app_mod.privileged_client,
        "stop_idle_runtime",
        fake_stop_inactive_service,
    )

    import asyncio

    assert asyncio.run(app_mod._run_runtime_idle_check_once()) == 1
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT runtime_started_at, session_revoked_after, last_sleep_reason FROM runtime_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    assert row[0] == 0
    assert row[1] > 0
    assert row[2] == "idle_timeout"


def test_idle_check_cleans_temporary_user(monkeypatch) -> None:
    db_path = _temp_db_path()
    auth_db.ensure_auth_db(db_path)
    display_store.ensure_display_store(db_path)
    ensure_runtime_state_store(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    mark_runtime_started(user.id, db_path=db_path)
    display_store.save_display_messages(
        user.id,
        "sess-1",
        [{"role": "user", "content": "hello"}],
        db_path=db_path,
    )

    old_activity_at = int(time.time()) - 3600
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user.id),
        )
        conn.execute(
            "UPDATE temporary_users SET created_at = ? WHERE user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()

    import interface.app as app_mod

    monkeypatch.setattr(app_mod, "RUNTIME_IDLE_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(app_mod, "TEMPORARY_USER_CLEANUP_RETRY_SECONDS", 60)
    monkeypatch.setattr(
        app_mod,
        "cleanup_expired_runtime_leases",
        lambda: __import__(
            "interface.runtime_state", fromlist=["cleanup_expired_runtime_leases"]
        ).cleanup_expired_runtime_leases(db_path=db_path),
    )
    monkeypatch.setattr(
        app_mod,
        "list_idle_runtime_candidates",
        lambda *, idle_timeout_seconds: list_idle_runtime_candidates(
            idle_timeout_seconds=idle_timeout_seconds,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "list_idle_temporary_user_candidates",
        lambda *, idle_timeout_seconds, cleanup_retry_seconds: list_idle_temporary_user_candidates(
            idle_timeout_seconds=idle_timeout_seconds,
            cleanup_retry_seconds=cleanup_retry_seconds,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "list_users",
        lambda: [auth_db.get_user_by_id(user.id, db_path=db_path)],
    )
    monkeypatch.setattr(
        app_mod,
        "is_temporary_user",
        lambda checked_user_id: auth_db.is_temporary_user(
            checked_user_id,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "mark_temporary_user_cleanup_attempt",
        lambda checked_user_id, *, status, error_message="", now=None: auth_db.mark_temporary_user_cleanup_attempt(
            checked_user_id,
            status=status,
            error_message=error_message,
            now=now,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "revoke_runtime_session",
        lambda checked_user_id, *, reason: __import__(
            "interface.runtime_state", fromlist=["revoke_runtime_session"]
        ).revoke_runtime_session(
            checked_user_id,
            reason=reason,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "delete_display_user_data",
        lambda checked_user_id: display_store.delete_display_user_data(
            checked_user_id,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "delete_runtime_state",
        lambda checked_user_id: delete_runtime_state(checked_user_id, db_path=db_path),
    )
    monkeypatch.setattr(
        app_mod,
        "delete_user_by_mapping_username",
        lambda checked_mapping_username: auth_db.delete_user_by_mapping_username(
            checked_mapping_username,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "delete_temporary_user_record",
        lambda checked_user_id: auth_db.delete_temporary_user_record(
            checked_user_id,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod.mapping_store,
        "resolve_target",
        lambda **kwargs: SimpleNamespace(
            username="temp_1",
            systemd_service="hermes-temp-1.service",
        ),
    )

    deprovisioned: list[tuple[str, bool]] = []
    removed_mappings: list[str] = []

    def fake_deprovision_user(username: str, *, delete_home: bool = False) -> None:
        deprovisioned.append((username, delete_home))

    monkeypatch.setattr(
        app_mod.privileged_client,
        "deprovision_user",
        fake_deprovision_user,
    )
    monkeypatch.setattr(
        app_mod.privileged_client,
        "remove_mapping",
        lambda username: removed_mappings.append(username),
    )

    closed_for_cleanup: list[str] = []

    class DummyBridgeRegistry:
        async def close_for_cleanup(self, checked_user_id: str) -> None:
            assert checked_user_id == user.id
            closed_for_cleanup.append(checked_user_id)

        async def close_for_reconfigure(self, checked_user_id: str) -> bool:
            raise AssertionError("temporary cleanup should force-close the bridge")

    app_mod.app.state.tui_gateway_bridges = DummyBridgeRegistry()

    import asyncio

    assert asyncio.run(app_mod._run_runtime_idle_check_once()) == 1
    assert closed_for_cleanup == [user.id]
    assert deprovisioned == [("temp_1", True)]
    assert removed_mappings == ["temp_1"]
    assert auth_db.get_user_by_id(user.id, db_path=db_path) is None
    assert auth_db.get_temporary_user(user.id, db_path=db_path) is None
    assert get_runtime_state(user.id, db_path=db_path) is None
    assert display_store.get_display_messages(user.id, "sess-1", db_path=db_path) is None


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


def run() -> None:
    test_foreground_activity_resets_idle_baseline_after_long_turn()


if __name__ == "__main__":
    run()

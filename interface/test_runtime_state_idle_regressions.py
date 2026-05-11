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

from interface.runtime_state import (
    create_runtime_lease,
    ensure_runtime_state_store,
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


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


def run() -> None:
    test_foreground_activity_resets_idle_baseline_after_long_turn()


if __name__ == "__main__":
    run()

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import interface.auth_db as auth_db
import interface.display_store as display_store
from interface.runtime_state import (
    claim_runtime_sleep,
    create_runtime_lease,
    delete_runtime_state,
    ensure_runtime_state_store,
    finish_runtime_lease,
    get_runtime_idle_eligibility,
    get_runtime_state,
    heartbeat_runtime_lease,
    list_idle_temporary_user_candidates,
    list_idle_runtime_candidates,
    mark_foreground_activity,
    mark_background_activity,
    mark_runtime_started,
    mark_user_message_activity,
    release_runtime_lease,
    release_runtime_sleep_claim,
    revoke_runtime_session,
    runtime_sleep_claim_is_valid,
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

    assert finish_runtime_lease(
        lease_id,
        user_id=user_id,
        db_path=db_path,
    ) is True

    assert not list_idle_runtime_candidates(
        idle_timeout_seconds=30 * 60,
        db_path=db_path,
    )


def test_runtime_idle_eligibility_reads_activity_and_lease_together() -> None:
    db_path = _temp_db_path()
    user_id = "user-1"
    checked_at = int(time.time())
    old_activity_at = checked_at - 600
    ensure_runtime_state_store(db_path)
    mark_runtime_started(user_id, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                last_background_activity_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (
                old_activity_at,
                old_activity_at,
                old_activity_at,
                old_activity_at,
                user_id,
            ),
        )
        conn.commit()

    eligible = get_runtime_idle_eligibility(
        user_id,
        idle_timeout_seconds=300,
        now=checked_at,
        db_path=db_path,
    )

    assert eligible is not None
    assert eligible["latest_activity_at"] == old_activity_at
    assert eligible["eligible"] is True
    assert eligible["reason"] == ""

    lease_id = create_runtime_lease(
        user_id,
        lease_type="foreground_chat",
        ttl_seconds=3600,
        db_path=db_path,
    )
    leased = get_runtime_idle_eligibility(
        user_id,
        idle_timeout_seconds=300,
        now=checked_at,
        db_path=db_path,
    )

    assert leased is not None
    assert leased["has_active_runtime_lease"] is True
    assert leased["eligible"] is False
    assert leased["reason"] == "active_lease"

    release_runtime_lease(lease_id, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE runtime_state SET last_user_message_at = ? WHERE user_id = ?",
            (checked_at - 60, user_id),
        )
        conn.commit()
    recent = get_runtime_idle_eligibility(
        user_id,
        idle_timeout_seconds=300,
        now=checked_at,
        db_path=db_path,
    )

    assert recent is not None
    assert recent["eligible"] is False
    assert recent["reason"] == "recent_activity"


def test_runtime_sleep_claim_fences_activity_and_new_leases() -> None:
    db_path = _temp_db_path()
    user_id = "user-claim"
    checked_at = int(time.time())
    old_activity_at = checked_at - 600
    ensure_runtime_state_store(db_path)
    mark_runtime_started(user_id, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                last_background_activity_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (
                old_activity_at,
                old_activity_at,
                old_activity_at,
                old_activity_at,
                user_id,
            ),
        )
        conn.commit()

    claim_id = claim_runtime_sleep(
        user_id,
        idle_timeout_seconds=300,
        now=checked_at,
        db_path=db_path,
    )

    assert claim_id
    assert mark_foreground_activity(user_id, db_path=db_path) is False
    assert mark_background_activity(user_id, db_path=db_path) is False
    with pytest.raises(RuntimeError, match="cleanup or sleep"):
        create_runtime_lease(
            user_id,
            lease_type="foreground_chat",
            ttl_seconds=90,
            db_path=db_path,
        )
    assert runtime_sleep_claim_is_valid(
        user_id,
        claim_id=claim_id,
        idle_timeout_seconds=300,
        now=checked_at + 1,
        db_path=db_path,
    ) is True
    assert release_runtime_sleep_claim(
        user_id,
        claim_id="another-worker",
        db_path=db_path,
    ) is False
    assert release_runtime_sleep_claim(
        user_id,
        claim_id=claim_id,
        db_path=db_path,
    ) is True
    assert mark_foreground_activity(user_id, db_path=db_path) is True


def test_runtime_sleep_claim_blocks_late_lease_heartbeat_and_finish() -> None:
    db_path = _temp_db_path()
    user_id = "user-late-lease"
    checked_at = int(time.time())
    old_activity_at = checked_at - 600
    ensure_runtime_state_store(db_path)
    mark_runtime_started(user_id, db_path=db_path)
    lease_id = create_runtime_lease(
        user_id,
        lease_type="foreground_chat",
        ttl_seconds=90,
        db_path=db_path,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                last_background_activity_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (
                old_activity_at,
                old_activity_at,
                old_activity_at,
                old_activity_at,
                user_id,
            ),
        )
        conn.execute(
            "UPDATE runtime_leases SET expires_at = ? WHERE lease_id = ?",
            (checked_at - 1, lease_id),
        )
        conn.commit()

    claim_id = claim_runtime_sleep(
        user_id,
        idle_timeout_seconds=300,
        now=checked_at,
        db_path=db_path,
    )

    assert claim_id
    assert heartbeat_runtime_lease(
        lease_id,
        ttl_seconds=90,
        db_path=db_path,
    ) is False
    assert finish_runtime_lease(
        lease_id,
        user_id=user_id,
        db_path=db_path,
    ) is False
    assert release_runtime_sleep_claim(
        user_id,
        claim_id=claim_id,
        db_path=db_path,
    ) is True
    state = get_runtime_state(user_id, db_path=db_path)
    assert state is not None
    assert state["last_user_message_at"] == old_activity_at


def test_late_finish_does_not_restore_activity_after_revocation() -> None:
    db_path = _temp_db_path()
    user_id = "user-revoked"
    ensure_runtime_state_store(db_path)
    mark_runtime_started(user_id, db_path=db_path)
    lease_id = create_runtime_lease(
        user_id,
        lease_type="foreground_chat",
        ttl_seconds=90,
        db_path=db_path,
    )

    revoke_runtime_session(
        user_id,
        reason="idle_timeout",
        db_path=db_path,
    )

    assert finish_runtime_lease(
        lease_id,
        user_id=user_id,
        db_path=db_path,
    ) is False
    state = get_runtime_state(user_id, db_path=db_path)
    assert state is not None
    assert state["last_user_message_at"] == 0
    assert state["last_sleep_reason"] == "idle_timeout"


def test_service_stop_deadline_stays_below_sleep_claim_ttl(monkeypatch) -> None:
    import interface.hermes_service as hermes_service

    calls: list[tuple[str, float | None]] = []
    monkeypatch.setattr(
        hermes_service,
        "_run_command",
        lambda command, *, timeout_seconds=None: calls.append(
            (command[1], timeout_seconds)
        ),
    )
    monkeypatch.setattr(
        hermes_service,
        "_run_command_result",
        lambda command, *, timeout_seconds=None: calls.append(
            (command[1], timeout_seconds)
        ),
    )

    hermes_service.stop_service("hermes-alice.service")

    assert calls == [
        ("stop", hermes_service.SYSTEMCTL_STOP_TIMEOUT_SECONDS),
        ("reset-failed", hermes_service.SYSTEMCTL_QUERY_TIMEOUT_SECONDS),
    ]
    assert sum(timeout or 0 for _, timeout in calls) < 5 * 60


@pytest.mark.parametrize(
    ("is_temporary", "expected_text"),
    [
        (False, "Workspace slept"),
        (True, "Temporary workspace expired"),
    ],
)
def test_activity_middleware_reports_the_matching_sleep_reason(
    monkeypatch, is_temporary: bool, expected_text: str
) -> None:
    import asyncio
    import json
    import interface.app as app_mod

    request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        app_mod,
        "_should_refresh_activity_for_request",
        lambda checked_request: True,
    )
    monkeypatch.setattr(
        app_mod,
        "_resolve_current_user",
        lambda checked_request: (
            SimpleNamespace(id="user-1", is_temporary=is_temporary),
            None,
        ),
    )
    monkeypatch.setattr(app_mod, "mark_foreground_activity", lambda user_id: False)

    async def unexpected_call_next(checked_request):
        raise AssertionError("blocked activity must not reach the route")

    response = asyncio.run(
        app_mod.refresh_authenticated_activity(request, unexpected_call_next)
    )

    assert response.status_code == 401
    assert expected_text in json.loads(response.body)["detail"]


def test_current_user_dependency_reuses_middleware_auth_resolution(monkeypatch) -> None:
    import asyncio
    import interface.app as app_mod
    from fastapi.responses import JSONResponse

    request = SimpleNamespace(state=SimpleNamespace())
    user = SimpleNamespace(id="user-1", is_temporary=False)
    resolve_calls = 0

    def resolve_once(checked_request):
        nonlocal resolve_calls
        resolve_calls += 1
        return user, None

    monkeypatch.setattr(
        app_mod,
        "_should_refresh_activity_for_request",
        lambda checked_request: True,
    )
    monkeypatch.setattr(app_mod, "_resolve_current_user", resolve_once)
    monkeypatch.setattr(app_mod, "mark_foreground_activity", lambda user_id: True)

    async def call_dependency(checked_request):
        assert await app_mod.get_current_user(checked_request) is user
        return JSONResponse({"ok": True})

    response = asyncio.run(
        app_mod.refresh_authenticated_activity(request, call_dependency)
    )

    assert response.status_code == 200
    assert resolve_calls == 1


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
    monkeypatch.setattr(app_mod, "cleanup_turn_submission_receipts", lambda: {})
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

    stopped: list[str] = []

    def fake_stop_idle_runtime(
        username: str,
        checked_user_id: str,
        idle_timeout_seconds: int,
    ) -> dict[str, bool]:
        assert idle_timeout_seconds == 300
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
    monkeypatch.setattr(app_mod, "cleanup_turn_submission_receipts", lambda: {})
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
    def fake_stop_inactive_service(
        username: str,
        checked_user_id: str,
        idle_timeout_seconds: int,
    ) -> dict[str, str | bool]:
        assert idle_timeout_seconds == 300
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


def test_root_idle_stop_claim_rechecks_after_service_probe(monkeypatch) -> None:
    import interface.app as app_mod

    eligibility_calls = 0

    def fake_eligibility(user_id: str, *, idle_timeout_seconds: int):
        nonlocal eligibility_calls
        eligibility_calls += 1
        assert user_id == "user-1"
        assert idle_timeout_seconds == 300
        return {"eligible": True, "reason": ""}

    monkeypatch.setattr(app_mod, "RUNTIME_IDLE_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(app_mod.os.path, "exists", lambda path: True)
    monkeypatch.setattr(app_mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(app_mod, "service_operation_lock", lambda service: _null_context())
    monkeypatch.setattr(app_mod, "get_runtime_idle_eligibility", fake_eligibility)
    monkeypatch.setattr(app_mod, "claim_runtime_sleep", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        app_mod, "_has_active_background_processes_for_target", lambda target: False
    )
    monkeypatch.setattr(app_mod, "is_service_active", lambda service: True)
    monkeypatch.setattr(
        app_mod,
        "stop_service",
        lambda service: (_ for _ in ()).throw(
            AssertionError("recent activity must prevent service stop")
        ),
    )

    stopped = app_mod._stop_idle_runtime_candidate(
        SimpleNamespace(id="user-1"),
        SimpleNamespace(username="alice", systemd_service="hermes-alice.service"),
    )

    assert stopped is False
    assert eligibility_calls == 1


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
    display_store.create_turn_submission_receipt(
        user.id,
        "request-1",
        requested_session_id="sess-1",
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
        "cleanup_turn_submission_receipts",
        lambda: display_store.cleanup_turn_submission_receipts(db_path=db_path),
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
        "claim_temporary_user_cleanup",
        lambda checked_user_id, *, idle_timeout_seconds, cleanup_retry_seconds: __import__(
            "interface.runtime_state", fromlist=["claim_temporary_user_cleanup"]
        ).claim_temporary_user_cleanup(
            checked_user_id,
            idle_timeout_seconds=idle_timeout_seconds,
            cleanup_retry_seconds=cleanup_retry_seconds,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "temporary_cleanup_claim_is_valid",
        lambda checked_user_id, *, claimed_at, idle_timeout_seconds: __import__(
            "interface.runtime_state", fromlist=["temporary_cleanup_claim_is_valid"]
        ).temporary_cleanup_claim_is_valid(
            checked_user_id,
            claimed_at=claimed_at,
            idle_timeout_seconds=idle_timeout_seconds,
            db_path=db_path,
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "release_temporary_cleanup_claim",
        lambda checked_user_id, *, claimed_at: __import__(
            "interface.runtime_state", fromlist=["release_temporary_cleanup_claim"]
        ).release_temporary_cleanup_claim(
            checked_user_id,
            claimed_at=claimed_at,
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
    monkeypatch.setattr(
        app_mod,
        "_has_active_background_processes_for_target",
        lambda target: False,
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
    assert (
        display_store.get_turn_submission_receipt(
            user.id, "request-1", db_path=db_path
        )
        is None
    )


def test_temporary_cleanup_keeps_user_with_background_job(monkeypatch) -> None:
    import asyncio
    import interface.app as app_mod

    released_claims: list[tuple[str, int]] = []
    background_activity: list[str] = []

    monkeypatch.setattr(app_mod, "RUNTIME_IDLE_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(app_mod, "TEMPORARY_USER_CLEANUP_RETRY_SECONDS", 60)
    monkeypatch.setattr(
        app_mod,
        "claim_temporary_user_cleanup",
        lambda user_id, **kwargs: {"last_cleanup_attempt_at": 123},
    )
    monkeypatch.setattr(
        app_mod,
        "temporary_cleanup_claim_is_valid",
        lambda user_id, **kwargs: True,
    )
    monkeypatch.setattr(
        app_mod,
        "release_temporary_cleanup_claim",
        lambda user_id, *, claimed_at: released_claims.append((user_id, claimed_at)),
    )
    monkeypatch.setattr(
        app_mod,
        "mark_background_activity",
        lambda user_id: background_activity.append(user_id),
    )
    monkeypatch.setattr(
        app_mod,
        "_has_active_background_processes_for_target",
        lambda target: True,
    )

    async def close_bridge(user_id: str) -> bool:
        return True

    monkeypatch.setattr(app_mod, "_close_temporary_user_bridge", close_bridge)
    monkeypatch.setattr(
        app_mod.privileged_client,
        "deprovision_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("background job must prevent temporary user deletion")
        ),
    )

    cleaned = asyncio.run(
        app_mod._cleanup_temporary_user_candidate(
            SimpleNamespace(id="temp-user", mapping_username="temp_1"),
            SimpleNamespace(username="temp_1"),
        )
    )

    assert cleaned is False
    assert background_activity == ["temp-user"]
    assert released_claims == [("temp-user", 123)]


def test_temporary_cleanup_fails_open_when_background_check_fails(monkeypatch) -> None:
    import asyncio
    import interface.app as app_mod

    released_claims: list[tuple[str, int]] = []
    monkeypatch.setattr(app_mod, "RUNTIME_IDLE_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(app_mod, "TEMPORARY_USER_CLEANUP_RETRY_SECONDS", 60)
    monkeypatch.setattr(
        app_mod,
        "claim_temporary_user_cleanup",
        lambda user_id, **kwargs: {"last_cleanup_attempt_at": 456},
    )
    monkeypatch.setattr(
        app_mod,
        "temporary_cleanup_claim_is_valid",
        lambda user_id, **kwargs: True,
    )
    monkeypatch.setattr(
        app_mod,
        "release_temporary_cleanup_claim",
        lambda user_id, *, claimed_at: released_claims.append((user_id, claimed_at)),
    )
    monkeypatch.setattr(
        app_mod,
        "_has_active_background_processes_for_target",
        lambda target: (_ for _ in ()).throw(RuntimeError("process scan failed")),
    )

    async def close_bridge(user_id: str) -> bool:
        return True

    monkeypatch.setattr(app_mod, "_close_temporary_user_bridge", close_bridge)
    monkeypatch.setattr(
        app_mod.privileged_client,
        "deprovision_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("failed background check must prevent deletion")
        ),
    )

    cleaned = asyncio.run(
        app_mod._cleanup_temporary_user_candidate(
            SimpleNamespace(id="temp-user", mapping_username="temp_1"),
            SimpleNamespace(username="temp_1"),
        )
    )

    assert cleaned is False
    assert released_claims == [("temp-user", 456)]


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


def run() -> None:
    test_foreground_activity_resets_idle_baseline_after_long_turn()


if __name__ == "__main__":
    run()

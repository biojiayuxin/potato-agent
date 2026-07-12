from __future__ import annotations

import importlib
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import interface.auth_db as auth_db
from interface.runtime_state import (
    claim_temporary_user_cleanup,
    create_runtime_lease,
    finish_runtime_lease,
    get_temporary_user_idle_status,
    heartbeat_runtime_lease,
    list_idle_temporary_user_candidates,
    mark_background_activity,
    mark_foreground_activity,
    mark_runtime_started,
    release_temporary_cleanup_claim,
    temporary_cleanup_claim_is_valid,
)


def _table_columns(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {str(row[1]) for row in conn.execute(f"pragma table_info({table_name})")}


def test_temporary_user_db_helpers_create_and_delete(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)

    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )

    assert {
        "user_id",
        "mapping_username",
        "created_at",
        "last_cleanup_attempt_at",
        "cleanup_status",
        "cleanup_error",
    }.issubset(_table_columns(db_path, "temporary_users"))
    assert auth_db.is_temporary_user(user.id, db_path=db_path)
    temporary_row = auth_db.get_temporary_user(user.id, db_path=db_path)
    assert temporary_row is not None
    assert temporary_row["mapping_username"] == "temp_1"
    assert temporary_row["cleanup_status"] == auth_db.TEMPORARY_USER_STATUS_ACTIVE

    assert auth_db.delete_user_by_mapping_username("temp_1", db_path=db_path)
    assert auth_db.get_user_by_id(user.id, db_path=db_path) is None
    assert auth_db.get_temporary_user(user.id, db_path=db_path) is None


def test_idle_temporary_candidates_include_users_without_runtime_state(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    old_activity_at = 1000
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "update temporary_users set created_at = ? where user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()

    rows = list_idle_temporary_user_candidates(
        idle_timeout_seconds=30 * 60,
        cleanup_retry_seconds=60,
        db_path=db_path,
    )

    assert [row["user_id"] for row in rows] == [user.id]


def test_temporary_idle_uses_background_activity_like_regular_users(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    old_activity_at = 1000
    mark_runtime_started(user.id, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            update runtime_state
            set runtime_started_at = ?,
                last_user_message_at = ?,
                updated_at = ?
            where user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user.id),
        )
        conn.execute(
            "update temporary_users set created_at = ? where user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()

    mark_background_activity(user.id, db_path=db_path)

    rows = list_idle_temporary_user_candidates(
        idle_timeout_seconds=30 * 60,
        cleanup_retry_seconds=60,
        db_path=db_path,
    )
    idle_status = get_temporary_user_idle_status(
        user.id,
        idle_timeout_seconds=30 * 60,
        db_path=db_path,
    )

    assert rows == []
    assert idle_status is not None
    assert idle_status["is_expired"] is False


def test_temporary_idle_respects_active_runtime_lease(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    old_activity_at = 1000
    mark_runtime_started(user.id, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            update runtime_state
            set runtime_started_at = ?,
                last_user_message_at = ?,
                updated_at = ?
            where user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user.id),
        )
        conn.execute(
            "update temporary_users set created_at = ? where user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()
    create_runtime_lease(
        user.id,
        lease_type="foreground_chat",
        ttl_seconds=3600,
        resource_id="live-1",
        db_path=db_path,
    )

    rows = list_idle_temporary_user_candidates(
        idle_timeout_seconds=30 * 60,
        cleanup_retry_seconds=60,
        db_path=db_path,
    )
    idle_status = get_temporary_user_idle_status(
        user.id,
        idle_timeout_seconds=30 * 60,
        db_path=db_path,
    )

    assert rows == []
    assert idle_status is not None
    assert idle_status["has_active_runtime_lease"] is True
    assert idle_status["is_expired"] is False


def test_temporary_cleanup_claim_rechecks_and_marks_cleaning(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    checked_at = 10_000
    old_activity_at = checked_at - 600
    mark_runtime_started(user.id, db_path=db_path)
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
                user.id,
            ),
        )
        conn.execute(
            "UPDATE temporary_users SET created_at = ? WHERE user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()

    claim = claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at,
        db_path=db_path,
    )

    assert claim is not None
    assert claim["previous_cleanup_status"] == auth_db.TEMPORARY_USER_STATUS_ACTIVE
    assert claim["cleanup_status"] == auth_db.TEMPORARY_USER_STATUS_CLEANING
    temporary_row = auth_db.get_temporary_user(user.id, db_path=db_path)
    assert temporary_row is not None
    assert temporary_row["cleanup_status"] == auth_db.TEMPORARY_USER_STATUS_CLEANING
    assert temporary_row["last_cleanup_attempt_at"] == checked_at

    duplicate_claim = claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at,
        db_path=db_path,
    )
    assert duplicate_claim is None


def test_temporary_cleanup_claim_rejects_recent_activity(
    tmp_path,
) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    checked_at = int(time.time())
    old_activity_at = checked_at - 600
    mark_runtime_started(user.id, db_path=db_path)
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
                checked_at - 60,
                old_activity_at,
                checked_at - 60,
                user.id,
            ),
        )
        conn.execute(
            "UPDATE temporary_users SET created_at = ? WHERE user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()

    assert claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at,
        db_path=db_path,
    ) is None
    assert (
        auth_db.get_temporary_user(user.id, db_path=db_path)["cleanup_status"]
        == auth_db.TEMPORARY_USER_STATUS_ACTIVE
    )


def test_cleanup_claim_blocks_late_activity_and_active_lease(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    checked_at = 10_000
    old_activity_at = checked_at - 600
    mark_runtime_started(user.id, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?, last_user_message_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user.id),
        )
        conn.execute(
            "UPDATE temporary_users SET created_at = ? WHERE user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()

    claim = claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at,
        db_path=db_path,
    )

    assert claim is not None
    assert mark_foreground_activity(user.id, db_path=db_path) is False
    assert mark_background_activity(user.id, db_path=db_path) is False
    with pytest.raises(RuntimeError, match="cleanup or sleep"):
        create_runtime_lease(
            user.id,
            lease_type="foreground_chat",
            ttl_seconds=90,
            db_path=db_path,
        )
    assert temporary_cleanup_claim_is_valid(
        user.id,
        claimed_at=checked_at,
        idle_timeout_seconds=300,
        now=checked_at,
        db_path=db_path,
    ) is True
    assert release_temporary_cleanup_claim(
        user.id,
        claimed_at=checked_at,
        db_path=db_path,
    ) is True
    assert mark_foreground_activity(user.id, db_path=db_path) is True

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE runtime_state SET last_user_message_at = ? WHERE user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()
    create_runtime_lease(
        user.id,
        lease_type="foreground_chat",
        ttl_seconds=3600,
        db_path=db_path,
    )

    assert claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at,
        db_path=db_path,
    ) is None
    assert (
        auth_db.get_temporary_user(user.id, db_path=db_path)["cleanup_status"]
        == auth_db.TEMPORARY_USER_STATUS_ACTIVE
    )


def test_temporary_cleanup_claim_honors_retry_window(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    checked_at = 10_000
    old_activity_at = checked_at - 600
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE temporary_users
            SET created_at = ?,
                cleanup_status = ?,
                last_cleanup_attempt_at = ?
            WHERE user_id = ?
            """,
            (
                old_activity_at,
                auth_db.TEMPORARY_USER_STATUS_FAILED,
                checked_at - 30,
                user.id,
            ),
        )
        conn.commit()

    assert claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at,
        db_path=db_path,
    ) is None

    claim = claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at + 31,
        db_path=db_path,
    )
    assert claim is not None
    assert claim["previous_cleanup_status"] == auth_db.TEMPORARY_USER_STATUS_FAILED


def test_temporary_cleanup_claim_discards_expired_bridge_lease(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.create_temporary_user(
        username="temp_1",
        email="temp_1@temporary.example",
        password="random-password",
        mapping_username="temp_1",
        name="Temporary User",
        db_path=db_path,
    )
    checked_at = int(time.time())
    old_activity_at = checked_at - 600
    mark_runtime_started(user.id, db_path=db_path)
    lease_id = create_runtime_lease(
        user.id,
        lease_type="foreground_chat",
        ttl_seconds=90,
        db_path=db_path,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?, last_user_message_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user.id),
        )
        conn.execute(
            "UPDATE temporary_users SET created_at = ? WHERE user_id = ?",
            (old_activity_at, user.id),
        )
        conn.execute(
            "UPDATE runtime_leases SET expires_at = ? WHERE lease_id = ?",
            (checked_at - 1, lease_id),
        )
        conn.commit()

    claim = claim_temporary_user_cleanup(
        user.id,
        idle_timeout_seconds=300,
        cleanup_retry_seconds=60,
        now=checked_at,
        db_path=db_path,
    )

    assert claim is not None
    assert heartbeat_runtime_lease(
        lease_id,
        ttl_seconds=90,
        db_path=db_path,
    ) is False
    assert finish_runtime_lease(
        lease_id,
        user_id=user.id,
        db_path=db_path,
    ) is False
    assert temporary_cleanup_claim_is_valid(
        user.id,
        claimed_at=checked_at,
        idle_timeout_seconds=300,
        now=checked_at,
        db_path=db_path,
    ) is True


def _load_app(tmp_path, monkeypatch):
    db_path = tmp_path / "interface.db"
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text("users: []\n", encoding="utf-8")
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(db_path))
    monkeypatch.setenv("POTATO_AGENT_MAPPING_PATH", str(mapping_path))
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "test-secret")
    for module_name in (
        "interface.auth_db",
        "interface.runtime_state",
        "interface.display_store",
        "interface.mapping",
        "interface.app",
    ):
        sys.modules.pop(module_name, None)

    auth_db_mod = importlib.import_module("interface.auth_db")
    app_mod = importlib.import_module("interface.app")
    auth_db_mod.ensure_auth_db(db_path)

    targets: dict[str, SimpleNamespace] = {}

    def fake_provision_user(
        username: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        home_dir = tmp_path / username
        targets[username] = SimpleNamespace(
            username=username,
            email=email or "",
            display_name=display_name or username,
            linux_user=f"hmx_{username}",
            home_dir=home_dir,
            workdir=home_dir,
            hermes_home=home_dir / ".hermes",
            systemd_service=f"hermes-{username}.service",
        )

    monkeypatch.setattr(
        app_mod.privileged_client,
        "provision_user",
        fake_provision_user,
    )
    monkeypatch.setattr(
        app_mod.mapping_store,
        "get_target_by_username",
        lambda username: targets.get(username),
    )
    monkeypatch.setattr(
        app_mod.mapping_store,
        "resolve_target",
        lambda **kwargs: targets.get(
            str(kwargs.get("mapping_username") or kwargs.get("username") or "")
        ),
    )
    return TestClient(app_mod.app), app_mod, auth_db_mod, db_path


def test_temporary_auth_session_creates_user_and_cookie(tmp_path, monkeypatch) -> None:
    client, app_mod, auth_db_mod, db_path = _load_app(tmp_path, monkeypatch)

    response = client.post("/api/auth/temporary")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["is_temporary"] is True
    assert payload["username"].startswith("temp_")
    assert app_mod.SESSION_COOKIE_NAME in client.cookies

    users = auth_db_mod.list_users(db_path=db_path)
    assert len(users) == 1
    assert users[0].username == payload["username"]
    assert auth_db_mod.is_temporary_user(users[0].id, db_path=db_path)

    session_response = client.get("/api/auth/session")
    assert session_response.status_code == 200, session_response.text
    session_payload = session_response.json()
    assert session_payload["authenticated"] is True
    assert session_payload["user"]["is_temporary"] is True


def test_expired_temporary_session_waits_for_scheduler_revocation(tmp_path, monkeypatch) -> None:
    client, app_mod, auth_db_mod, db_path = _load_app(tmp_path, monkeypatch)
    response = client.post("/api/auth/temporary")
    assert response.status_code == 200, response.text
    user = auth_db_mod.list_users(db_path=db_path)[0]
    old_activity_at = 1000
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "update temporary_users set created_at = ? where user_id = ?",
            (old_activity_at, user.id),
        )
        conn.commit()

    session_response = client.get("/api/auth/session")
    assert session_response.status_code == 200, session_response.text
    session_payload = session_response.json()
    assert session_payload["authenticated"] is True

    app_mod.revoke_runtime_session(user.id, reason="temporary_user_expired")
    revoked_response = client.get("/api/auth/session")
    assert revoked_response.status_code == 200, revoked_response.text
    session_payload = revoked_response.json()
    assert session_payload["authenticated"] is False
    assert session_payload["reason"] == "temporary_user_expired"


def test_failed_temporary_cleanup_without_mapping_removes_local_state(
    tmp_path, monkeypatch
) -> None:
    _, app_mod, auth_db_mod, db_path = _load_app(tmp_path, monkeypatch)
    user = auth_db_mod.create_temporary_user(
        username="temp_orphan",
        email="temp_orphan@temporary.example",
        password="random-password",
        mapping_username="temp_orphan",
        name="Temporary User",
        db_path=db_path,
    )
    old_activity_at = int(time.time()) - 3600
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE temporary_users
            SET created_at = ?, cleanup_status = ?, last_cleanup_attempt_at = ?
            WHERE user_id = ?
            """,
            (
                old_activity_at,
                auth_db_mod.TEMPORARY_USER_STATUS_FAILED,
                old_activity_at,
                user.id,
            ),
        )
        conn.commit()

    monkeypatch.setattr(app_mod, "RUNTIME_IDLE_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(app_mod, "TEMPORARY_USER_CLEANUP_RETRY_SECONDS", 60)

    import asyncio

    assert asyncio.run(app_mod._run_runtime_idle_check_once()) == 1
    assert auth_db_mod.get_user_by_id(user.id, db_path=db_path) is None
    assert auth_db_mod.get_temporary_user(user.id, db_path=db_path) is None


def test_file_tree_refresh_does_not_extend_activity(tmp_path, monkeypatch) -> None:
    _, app_mod, _, _ = _load_app(tmp_path, monkeypatch)

    file_tree_request = SimpleNamespace(
        url=SimpleNamespace(path="/api/files/tree"),
    )
    file_revision_request = SimpleNamespace(
        url=SimpleNamespace(path="/api/files/revision"),
    )
    upload_request = SimpleNamespace(
        url=SimpleNamespace(path="/api/files/upload"),
    )
    background_session_refresh = SimpleNamespace(
        url=SimpleNamespace(path="/api/sessions/session-1"),
        method="GET",
        query_params={"background": "1"},
    )
    foreground_session_refresh = SimpleNamespace(
        url=SimpleNamespace(path="/api/sessions/session-1"),
        method="GET",
        query_params={},
    )

    assert app_mod._should_refresh_activity_for_request(file_tree_request) is False
    assert app_mod._should_refresh_activity_for_request(file_revision_request) is False
    assert app_mod._should_refresh_activity_for_request(upload_request) is True
    assert app_mod._should_refresh_activity_for_request(background_session_refresh) is False
    assert app_mod._should_refresh_activity_for_request(foreground_session_refresh) is True


def test_temporary_user_cannot_change_password(tmp_path, monkeypatch) -> None:
    client, _, _, _ = _load_app(tmp_path, monkeypatch)
    response = client.post("/api/auth/temporary")
    assert response.status_code == 200, response.text

    password_response = client.post(
        "/api/auth/password",
        json={
            "current_password": "anything",
            "new_password": "Newpassword1!",
        },
    )

    assert password_response.status_code == 403, password_response.text
    assert password_response.json()["detail"] == "Temporary users cannot change passwords."

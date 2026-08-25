from __future__ import annotations

import sqlite3

import pytest

from interface import admin_store, auth_db


def _formal_user(db_path, username: str = "alice") -> auth_db.InterfaceUser:
    auth_db.ensure_auth_db(db_path)
    return auth_db.upsert_user(
        username=username,
        email=f"{username}@example.com",
        password="Password1!",
        mapping_username=username,
        name=username.title(),
        db_path=db_path,
    )


def test_role_changes_revoke_sessions_and_protect_last_admin(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    alice = _formal_user(db_path)
    promoted = auth_db.set_user_role("alice", "admin", db_path=db_path)
    assert promoted.role == "admin"
    assert promoted.auth_session_version == alice.auth_session_version + 1

    unchanged = auth_db.set_user_role("alice", "admin", db_path=db_path)
    assert unchanged.auth_session_version == promoted.auth_session_version
    with pytest.raises(auth_db.LastAdministratorError):
        auth_db.set_user_role("alice", "user", db_path=db_path)
    with pytest.raises(auth_db.LastAdministratorError):
        auth_db.delete_user_by_id(alice.id, db_path=db_path)

    bob = _formal_user(db_path, "bob")
    auth_db.set_user_role("bob", "admin", db_path=db_path)
    demoted = auth_db.set_user_role("alice", "user", db_path=db_path)
    assert demoted.role == "user"
    with pytest.raises(auth_db.LastAdministratorError):
        auth_db.set_user_role("bob", "user", db_path=db_path)


def test_temporary_user_cannot_be_promoted(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    temporary = auth_db.create_temporary_user(
        username="temp_1234567890_0123abcd",
        email="temp@temporary.example",
        password="random password",
        mapping_username="temp_1234567890_0123abcd",
        db_path=db_path,
    )
    with pytest.raises(auth_db.RoleManagementError, match="Temporary"):
        auth_db.set_user_role(temporary.username, "admin", db_path=db_path)


def test_usage_principal_is_claimed_atomically_and_never_reused(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    user = _formal_user(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        identity = conn.execute(
            "select user_id, account_type, retired_at from user_usage_identities "
            "where mapping_username = 'alice'"
        ).fetchone()
    assert identity == (user.id, "formal", None)

    assert auth_db.delete_user_by_id(user.id, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        retired_at = conn.execute(
            "select retired_at from user_usage_identities where mapping_username = 'alice'"
        ).fetchone()[0]
    assert retired_at is not None
    with pytest.raises(auth_db.PrincipalReuseError):
        auth_db.upsert_user(
            username="alice2",
            email="alice2@example.com",
            password="Password1!",
            mapping_username="alice",
            db_path=db_path,
        )
    assert auth_db.get_user_by_login("alice2", db_path=db_path) is None


def test_temporary_cleanup_retires_identity_and_deletes_storage(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    username = "temp_1234567890_0123abcd"
    user = auth_db.create_temporary_user(
        username=username,
        email="temp@temporary.example",
        password="random password",
        mapping_username=username,
        db_path=db_path,
    )
    assert admin_store.save_storage_snapshot(
        user_id=user.id,
        mapping_username=username,
        snapshot_day="2026-08-25",
        sampled_at=100,
        allocated_bytes=4096,
        status="ok",
        db_path=db_path,
    )

    assert auth_db.delete_user_by_mapping_username(username, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        identity = conn.execute(
            "select account_type, retired_at from user_usage_identities "
            "where mapping_username = ?",
            (username,),
        ).fetchone()
        snapshots = conn.execute(
            "select count(*) from user_storage_snapshots where user_id = ?",
            (user.id,),
        ).fetchone()[0]
    assert identity[0] == "temporary"
    assert identity[1] is not None
    assert snapshots == 0


def test_snapshot_upsert_is_idempotent_and_rechecks_current_user(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    user = _formal_user(db_path)
    for allocated_bytes, sampled_at in ((100, 10), (200, 20)):
        assert admin_store.save_storage_snapshot(
            user_id=user.id,
            mapping_username=user.mapping_username,
            snapshot_day="2026-08-25",
            sampled_at=sampled_at,
            allocated_bytes=allocated_bytes,
            status="ok",
            db_path=db_path,
        )
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "select sampled_at, allocated_bytes from user_storage_snapshots"
        ).fetchall()
    assert rows == [(20, 200)]

    assert auth_db.delete_user_by_id(user.id, db_path=db_path)
    assert not admin_store.save_storage_snapshot(
        user_id=user.id,
        mapping_username=user.mapping_username,
        snapshot_day="2026-08-26",
        sampled_at=30,
        allocated_bytes=300,
        status="ok",
        db_path=db_path,
    )


def test_signin_failure_limits_are_persistent_per_login_and_ip(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    for _ in range(admin_store.ADMIN_LOGIN_IP_FAILURE_LIMIT):
        admin_store.record_signin_failure(
            login_ip_key="login-key",
            ip_key="ip-key",
            now=100,
            db_path=db_path,
        )
    assert admin_store.signin_retry_after(
        login_ip_key="login-key", ip_key="ip-key", now=101, db_path=db_path
    ) == admin_store.ADMIN_SIGNIN_WINDOW_SECONDS - 1
    admin_store.clear_login_signin_failures("login-key", db_path=db_path)
    assert admin_store.signin_retry_after(
        login_ip_key="login-key", ip_key="ip-key", now=101, db_path=db_path
    ) == 0


def test_versioned_principal_reconciliation_classifies_only_legacy_temp_pattern(
    tmp_path,
) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    catalog = [
        {"mapping_username": "temp_1234567890_0123abcd", "first_used_at": 10, "last_used_at": 20},
        {"mapping_username": "temp_old", "first_used_at": 11, "last_used_at": 21},
        {"mapping_username": "daily-updates-service", "first_used_at": 12, "last_used_at": 22},
    ]
    assert admin_store.reconcile_principal_catalog(catalog, now=30, db_path=db_path)
    assert not admin_store.reconcile_principal_catalog(catalog, now=40, db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        rows = dict(
            conn.execute(
                "select mapping_username, account_type from user_usage_identities"
            ).fetchall()
        )
    assert rows == {
        "temp_1234567890_0123abcd": "temporary",
        "temp_old": "unknown",
        "daily-updates-service": "service",
    }
    entities = admin_store.list_overview_entities(db_path=db_path)
    assert [item["mapping_username"] for item in entities] == [
        "temp_1234567890_0123abcd"
    ]

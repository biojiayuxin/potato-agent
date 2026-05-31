from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import interface.auth_db as auth_db


def _table_columns(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {str(row[1]) for row in conn.execute(f"pragma table_info({table_name})")}


def _password_hash_for_user(db_path: Path, user_id: str) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "select password_hash from users where id = ?",
            (user_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _verification_row(db_path: Path, verification_id: str) -> dict:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select * from email_verifications where id = ?",
            (verification_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_ensure_auth_db_migrates_user_auth_session_version(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            create table users (
                id text primary key,
                username text not null unique,
                email text not null unique,
                password_hash text not null,
                name text not null,
                role text not null default 'user',
                mapping_username text not null,
                active integer not null default 1,
                created_at integer not null,
                updated_at integer not null
            );
            """
        )

    auth_db.ensure_auth_db(db_path)

    assert "auth_session_version" in _table_columns(db_path, "users")


def test_update_user_password_changes_hash_and_increments_session_version(
    tmp_path,
) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.upsert_user(
        username="alice",
        email="alice@example.com",
        password="oldpassword",
        mapping_username="alice",
        name="Alice",
        db_path=db_path,
    )
    old_hash = _password_hash_for_user(db_path, user.id)

    updated = auth_db.update_user_password(
        user.id,
        "newpassword",
        db_path=db_path,
    )

    assert updated is not None
    assert updated.auth_session_version == user.auth_session_version + 1
    new_hash = _password_hash_for_user(db_path, user.id)
    assert new_hash != old_hash
    assert auth_db.verify_password("newpassword", new_hash)
    assert not auth_db.verify_password("oldpassword", new_hash)


def test_reset_user_password_with_email_verification_consumes_code_and_revokes_sessions(
    tmp_path,
) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    user = auth_db.upsert_user(
        username="alice",
        email="alice@example.com",
        password="oldpassword",
        mapping_username="alice",
        name="Alice",
        db_path=db_path,
    )
    verification_id = auth_db.create_pending_email_verification(
        email="alice@example.com",
        code_hash="good-hash",
        purpose=auth_db.EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
        expires_at=2000,
        now=1000,
        db_path=db_path,
    )

    updated = auth_db.reset_user_password_with_email_verification(
        email="Alice@Example.COM",
        new_password="newpassword",
        email_verification_id=verification_id,
        email_verification_code_hash="good-hash",
        now=1100,
        db_path=db_path,
    )

    assert updated.auth_session_version == user.auth_session_version + 1
    assert _verification_row(db_path, verification_id)["status"] == "consumed"
    new_hash = _password_hash_for_user(db_path, user.id)
    assert auth_db.verify_password("newpassword", new_hash)
    assert not auth_db.verify_password("oldpassword", new_hash)

    with pytest.raises(auth_db.EmailVerificationError) as exc_info:
        auth_db.reset_user_password_with_email_verification(
            email="alice@example.com",
            new_password="anotherpassword",
            email_verification_id=verification_id,
            email_verification_code_hash="good-hash",
            now=1101,
            db_path=db_path,
        )
    assert exc_info.value.reason == "consumed"


def _load_app(tmp_path, monkeypatch):
    db_path = tmp_path / "interface.db"
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(db_path))
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "test-secret")
    for module_name in ("interface.auth_db", "interface.runtime_state", "interface.app"):
        sys.modules.pop(module_name, None)
    auth_db_mod = importlib.import_module("interface.auth_db")
    app_mod = importlib.import_module("interface.app")
    auth_db_mod.ensure_auth_db(db_path)
    monkeypatch.setattr(
        app_mod.mapping_store,
        "resolve_target",
        lambda **kwargs: SimpleNamespace(
            home_dir=tmp_path,
            workdir=tmp_path,
        ),
    )
    return TestClient(app_mod.app), app_mod, auth_db_mod, db_path


def test_change_password_requires_login(tmp_path, monkeypatch) -> None:
    client, _, _, _ = _load_app(tmp_path, monkeypatch)
    try:
        response = client.post(
            "/api/auth/password",
            json={
                "current_password": "oldpassword",
                "new_password": "newpassword",
            },
        )
        assert response.status_code == 401, response.text
    finally:
        client.close()


def test_change_password_rejects_wrong_current_password_without_update(
    tmp_path,
    monkeypatch,
) -> None:
    client, app_mod, auth_db_mod, db_path = _load_app(tmp_path, monkeypatch)
    user = auth_db_mod.upsert_user(
        username="alice",
        email="alice@example.com",
        password="oldpassword",
        mapping_username="alice",
        name="Alice",
        db_path=db_path,
    )
    old_hash = _password_hash_for_user(db_path, user.id)
    token = app_mod._create_session_token(user.id, user.auth_session_version)
    client.cookies.set(app_mod.SESSION_COOKIE_NAME, token)

    try:
        response = client.post(
            "/api/auth/password",
            json={
                "current_password": "wrongpassword",
                "new_password": "newpassword",
            },
        )
        assert response.status_code == 401, response.text
        reloaded = auth_db_mod.get_user_by_id(user.id, db_path=db_path)
        assert reloaded is not None
        assert reloaded.auth_session_version == user.auth_session_version
        assert _password_hash_for_user(db_path, user.id) == old_hash
    finally:
        client.close()


def test_change_password_keeps_current_client_authenticated_and_revokes_old_token(
    tmp_path,
    monkeypatch,
) -> None:
    client, app_mod, auth_db_mod, db_path = _load_app(tmp_path, monkeypatch)
    user = auth_db_mod.upsert_user(
        username="alice",
        email="alice@example.com",
        password="oldpassword",
        mapping_username="alice",
        name="Alice",
        db_path=db_path,
    )
    old_token = app_mod._create_session_token(user.id, user.auth_session_version)
    client.cookies.set(app_mod.SESSION_COOKIE_NAME, old_token)

    try:
        change_response = client.post(
            "/api/auth/password",
            json={
                "current_password": "oldpassword",
                "new_password": "newpassword",
            },
        )
        assert change_response.status_code == 200, change_response.text
        assert change_response.json()["ok"] is True

        current_session = client.get("/api/auth/session")
        assert current_session.status_code == 200, current_session.text
        assert current_session.json()["authenticated"] is True

        old_client = TestClient(app_mod.app)
        try:
            old_client.cookies.set(app_mod.SESSION_COOKIE_NAME, old_token)
            old_session = old_client.get("/api/auth/session")
            assert old_session.status_code == 200, old_session.text
            assert old_session.json() == {
                "authenticated": False,
                "reason": "password_changed",
                "message": "Password changed. Please sign in again.",
            }
        finally:
            old_client.close()

        _, password_hash = auth_db_mod.get_user_with_password_by_login(
            "alice",
            db_path=db_path,
        )
        assert auth_db_mod.verify_password("newpassword", password_hash)
        assert not auth_db_mod.verify_password("oldpassword", password_hash)
    finally:
        client.close()


def test_password_reset_api_sends_code_resets_password_and_revokes_old_token(
    tmp_path,
    monkeypatch,
) -> None:
    client, app_mod, auth_db_mod, db_path = _load_app(tmp_path, monkeypatch)
    user = auth_db_mod.upsert_user(
        username="alice",
        email="alice@example.com",
        password="oldpassword",
        mapping_username="alice",
        name="Alice",
        db_path=db_path,
    )
    old_token = app_mod._create_session_token(user.id, user.auth_session_version)
    sent: dict[str, object] = {}

    async def fake_send_password_reset_email(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(email_id="email_reset_1", status_code=200)

    monkeypatch.setattr(
        app_mod, "send_password_reset_email", fake_send_password_reset_email
    )

    try:
        send_response = client.post(
            "/api/auth/password-reset/email-verifications",
            json={"email": "Alice@Example.COM"},
        )
        assert send_response.status_code == 200, send_response.text
        payload = send_response.json()
        assert payload["ok"] is True
        assert sent["email"] == "alice@example.com"
        assert isinstance(sent["code"], str)
        assert len(str(sent["code"])) == 6

        row = _verification_row(db_path, payload["verification_id"])
        assert row["purpose"] == auth_db_mod.EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET
        assert row["status"] == "pending"
        assert row["resend_email_id"] == "email_reset_1"

        reset_response = client.post(
            "/api/auth/password-reset",
            json={
                "email": "alice@example.com",
                "new_password": "newpassword",
                "email_verification_id": payload["verification_id"],
                "email_verification_code": sent["code"],
            },
        )
        assert reset_response.status_code == 200, reset_response.text
        assert reset_response.json()["ok"] is True
        assert _verification_row(db_path, payload["verification_id"])["status"] == "consumed"

        _, password_hash = auth_db_mod.get_user_with_password_by_login(
            "alice@example.com",
            db_path=db_path,
        )
        assert auth_db_mod.verify_password("newpassword", password_hash)
        assert not auth_db_mod.verify_password("oldpassword", password_hash)

        old_client = TestClient(app_mod.app)
        try:
            old_client.cookies.set(app_mod.SESSION_COOKIE_NAME, old_token)
            old_session = old_client.get("/api/auth/session")
            assert old_session.status_code == 200, old_session.text
            assert old_session.json() == {
                "authenticated": False,
                "reason": "password_changed",
                "message": "Password changed. Please sign in again.",
            }
        finally:
            old_client.close()
    finally:
        client.close()


def test_password_reset_email_verification_does_not_send_for_unknown_email(
    tmp_path,
    monkeypatch,
) -> None:
    client, app_mod, _, db_path = _load_app(tmp_path, monkeypatch)
    send_count = 0

    async def fake_send_password_reset_email(**kwargs):
        nonlocal send_count
        send_count += 1
        return SimpleNamespace(email_id="email_reset_1", status_code=200)

    monkeypatch.setattr(
        app_mod, "send_password_reset_email", fake_send_password_reset_email
    )

    try:
        response = client.post(
            "/api/auth/password-reset/email-verifications",
            json={"email": "unknown@example.com"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert send_count == 0

        row = _verification_row(db_path, response.json()["verification_id"])
        assert row["email"] == "unknown@example.com"
        assert row["purpose"] == auth_db.EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET
        assert row["status"] == "pending"
    finally:
        client.close()

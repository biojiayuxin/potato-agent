from __future__ import annotations

import sqlite3
from pathlib import Path

import interface.auth_db as auth_db
import manage_interface_users


def _password_hash_for_user(db_path: Path, user_id: str) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "select password_hash from users where id = ?",
            (user_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _create_user(
    db_path: Path, *, password: str = "Oldpassword1"
) -> auth_db.InterfaceUser:
    auth_db.ensure_auth_db(db_path)
    return auth_db.upsert_user(
        username="alice",
        email="alice@example.com",
        password=password,
        mapping_username="alice",
        name="Alice",
        db_path=db_path,
    )


def test_show_user_does_not_print_password_hash_by_default(tmp_path, capsys) -> None:
    db_path = tmp_path / "interface.db"
    _create_user(db_path)

    result = manage_interface_users.main(
        ["--auth-db", str(db_path), "show", "alice"]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "plaintext_password: unavailable" in captured.out
    assert "password_hash: bcrypt" in captured.out
    assert "$2" not in captured.out


def test_reset_password_rejects_weak_password_without_update(tmp_path, capsys) -> None:
    db_path = tmp_path / "interface.db"
    user = _create_user(db_path)
    old_hash = _password_hash_for_user(db_path, user.id)

    result = manage_interface_users.main(
        [
            "--auth-db",
            str(db_path),
            "reset-password",
            "alice",
            "--password",
            "newpassword",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Refusing to write a weak password" in captured.err
    assert _password_hash_for_user(db_path, user.id) == old_hash
    reloaded = auth_db.get_user_by_id(user.id, db_path=db_path)
    assert reloaded is not None
    assert reloaded.auth_session_version == user.auth_session_version


def test_reset_password_updates_hash_and_revokes_sessions(tmp_path, capsys) -> None:
    db_path = tmp_path / "interface.db"
    user = _create_user(db_path)
    old_hash = _password_hash_for_user(db_path, user.id)

    result = manage_interface_users.main(
        [
            "--auth-db",
            str(db_path),
            "reset-password",
            "alice@example.com",
            "--password",
            "Newpassword1!",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Existing browser sessions for this user are revoked." in captured.out
    new_hash = _password_hash_for_user(db_path, user.id)
    assert new_hash != old_hash
    assert auth_db.verify_password("Newpassword1!", new_hash)
    assert not auth_db.verify_password("Oldpassword1", new_hash)
    reloaded = auth_db.get_user_by_id(user.id, db_path=db_path)
    assert reloaded is not None
    assert reloaded.auth_session_version == user.auth_session_version + 1


def test_check_password_returns_match_status(tmp_path, capsys) -> None:
    db_path = tmp_path / "interface.db"
    _create_user(db_path)

    matched = manage_interface_users.main(
        [
            "--auth-db",
            str(db_path),
            "check-password",
            "alice",
            "--password",
            "Oldpassword1",
        ]
    )
    mismatched = manage_interface_users.main(
        [
            "--auth-db",
            str(db_path),
            "check-password",
            "alice",
            "--password",
            "Wrongpassword1",
        ]
    )

    captured = capsys.readouterr()
    assert matched == 0
    assert mismatched == 2
    assert "Password matches user alice." in captured.out
    assert "Password does not match user alice." in captured.out


def test_audit_passwords_reports_common_password_match(tmp_path, capsys) -> None:
    db_path = tmp_path / "interface.db"
    _create_user(db_path, password="Password123")

    result = manage_interface_users.main(
        ["--auth-db", str(db_path), "audit-passwords"]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Weak/common password matches found:" in captured.out
    assert "alice <alice@example.com>" in captured.out

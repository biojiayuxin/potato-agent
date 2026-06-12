from __future__ import annotations

import sqlite3
from pathlib import Path

import interface.auth_db as auth_db
import interface.mailer as mailer
import rotate_interface_passwords


OLD_PASSWORD = "Oldpassword1!"


def _password_hash_for_user(db_path: Path, user_id: str) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "select password_hash from users where id = ?",
            (user_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _set_user_created_at(db_path: Path, user_id: str, timestamp: int) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "update users set created_at = ?, updated_at = ? where id = ?",
            (timestamp, timestamp, user_id),
        )
        conn.commit()


def _create_user(
    db_path: Path,
    *,
    username: str,
    email: str,
    created_at: int,
) -> auth_db.InterfaceUser:
    auth_db.ensure_auth_db(db_path)
    user = auth_db.upsert_user(
        username=username,
        email=email,
        password=OLD_PASSWORD,
        mapping_username=username,
        name=username.title(),
        db_path=db_path,
    )
    _set_user_created_at(db_path, user.id, created_at)
    reloaded = auth_db.get_user_by_id(user.id, db_path=db_path)
    assert reloaded is not None
    return reloaded


def test_dry_run_selects_users_before_cutoff_without_updating(tmp_path, capsys) -> None:
    db_path = tmp_path / "interface.db"
    before = _create_user(
        db_path,
        username="before",
        email="before@example.com",
        created_at=1_748_563_200,
    )
    after = _create_user(
        db_path,
        username="after",
        email="after@example.com",
        created_at=1_748_822_400,
    )
    before_hash = _password_hash_for_user(db_path, before.id)
    after_hash = _password_hash_for_user(db_path, after.id)

    result = rotate_interface_passwords.main(
        ["--auth-db", str(db_path), "--before-date", "2025-06-01"]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "DRY RUN: 1 user(s) selected" in captured.out
    assert "before <before@example.com>" in captured.out
    assert "after <after@example.com>" not in captured.out
    assert _password_hash_for_user(db_path, before.id) == before_hash
    assert _password_hash_for_user(db_path, after.id) == after_hash


def test_execute_rotates_password_and_sends_notice(tmp_path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "interface.db"
    user = _create_user(
        db_path,
        username="alice",
        email="alice@example.com",
        created_at=1_748_563_200,
    )
    old_hash = _password_hash_for_user(db_path, user.id)
    sent: list[dict[str, object]] = []
    monkeypatch.setenv("INTERFACE_RESEND_API_KEY", "sk_test")
    monkeypatch.setenv("INTERFACE_MAIL_FROM", "Potato Agent <noreply@example.com>")

    async def fake_send_password_rotation_notice_email(**kwargs):
        sent.append(kwargs)
        return mailer.ResendEmailResult(email_id="email_1", status_code=200)

    monkeypatch.setattr(
        rotate_interface_passwords,
        "send_password_rotation_notice_email",
        fake_send_password_rotation_notice_email,
    )

    result = rotate_interface_passwords.main(
        [
            "--auth-db",
            str(db_path),
            "--login",
            "alice",
            "--execute",
            "--no-systemd-env",
            "--site-url",
            "https://potato.example/lite",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert len(sent) == 1
    new_password = str(sent[0]["new_password"])
    assert sent[0]["email"] == "alice@example.com"
    assert sent[0]["username"] == "alice"
    assert sent[0]["site_url"] == "https://potato.example/lite"
    assert new_password not in captured.out
    new_hash = _password_hash_for_user(db_path, user.id)
    assert new_hash != old_hash
    assert auth_db.verify_password(new_password, new_hash)
    assert not auth_db.verify_password(OLD_PASSWORD, new_hash)
    reloaded = auth_db.get_user_by_id(user.id, db_path=db_path)
    assert reloaded is not None
    assert reloaded.auth_session_version == user.auth_session_version + 1


def test_email_failure_rolls_back_password_hash_and_session_version(
    tmp_path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "interface.db"
    user = _create_user(
        db_path,
        username="alice",
        email="alice@example.com",
        created_at=1_748_563_200,
    )
    old_hash = _password_hash_for_user(db_path, user.id)
    monkeypatch.setenv("INTERFACE_RESEND_API_KEY", "sk_test")
    monkeypatch.setenv("INTERFACE_MAIL_FROM", "Potato Agent <noreply@example.com>")

    async def failing_send_password_rotation_notice_email(**kwargs):
        raise mailer.MailerDeliveryError("resend failed", status_code=503)

    monkeypatch.setattr(
        rotate_interface_passwords,
        "send_password_rotation_notice_email",
        failing_send_password_rotation_notice_email,
    )

    result = rotate_interface_passwords.main(
        [
            "--auth-db",
            str(db_path),
            "--login",
            "alice",
            "--execute",
            "--no-systemd-env",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "FAILED alice <alice@example.com>: resend failed" in captured.err
    assert _password_hash_for_user(db_path, user.id) == old_hash
    assert auth_db.verify_password(OLD_PASSWORD, old_hash)
    reloaded = auth_db.get_user_by_id(user.id, db_path=db_path)
    assert reloaded is not None
    assert reloaded.auth_session_version == user.auth_session_version
    assert reloaded.updated_at == user.updated_at

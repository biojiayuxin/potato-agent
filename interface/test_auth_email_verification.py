from __future__ import annotations

import importlib
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import interface.auth_db as auth_db
import interface.mailer as mailer


def _table_columns(db_path: Path, table_name: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {str(row[1]) for row in conn.execute(f"pragma table_info({table_name})")}


def _verification_row(db_path: Path, verification_id: str) -> dict:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select * from email_verifications where id = ?",
            (verification_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def test_ensure_auth_db_migrates_signup_and_email_verification_schema(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            create table signup_jobs (
                job_id text primary key,
                username text not null,
                email text not null,
                password_hash text not null,
                display_name text not null,
                status text not null,
                error_message text not null default '',
                created_at integer not null,
                updated_at integer not null
            );
            """
        )

    auth_db.ensure_auth_db(db_path)

    signup_columns = _table_columns(db_path, "signup_jobs")
    verification_columns = _table_columns(db_path, "email_verifications")
    assert "email_verification_id" in signup_columns
    assert "email_verified_at" in signup_columns
    assert {
        "id",
        "email",
        "purpose",
        "code_hash",
        "status",
        "attempt_count",
        "resend_email_id",
        "last_sent_at",
        "expires_at",
        "verified_at",
        "consumed_at",
        "client_ip_hash",
    }.issubset(verification_columns)


def test_create_signup_job_accepts_optional_email_verification_fields(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)

    job_id = auth_db.create_signup_job(
        username="alice",
        email="alice@example.com",
        password="password123",
        display_name="Alice",
        email_verification_id="verify-1",
        email_verified_at=1234,
        db_path=db_path,
    )

    job = auth_db.get_signup_job(job_id, db_path=db_path)
    assert job is not None
    assert job["email_verification_id"] == "verify-1"
    assert job["email_verified_at"] == 1234


def test_verified_signup_job_consumes_code_and_rejects_reuse(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    verification_id = auth_db.create_pending_email_verification(
        email="alice@example.com",
        code_hash="good-hash",
        expires_at=2000,
        now=1000,
        db_path=db_path,
    )

    job_id = auth_db.create_signup_job_with_email_verification(
        username="alice",
        email="alice@example.com",
        password="password123",
        display_name="Alice",
        email_verification_id=verification_id,
        email_verification_code_hash="good-hash",
        now=1100,
        db_path=db_path,
    )

    job = auth_db.get_signup_job(job_id, db_path=db_path)
    assert job is not None
    assert job["email_verification_id"] == verification_id
    assert job["email_verified_at"] == 1100
    assert _verification_row(db_path, verification_id)["status"] == "consumed"

    with pytest.raises(auth_db.EmailVerificationError) as exc_info:
        auth_db.create_signup_job_with_email_verification(
            username="alice2",
            email="alice@example.com",
            password="password123",
            display_name="Alice Two",
            email_verification_id=verification_id,
            email_verification_code_hash="good-hash",
            now=1101,
            db_path=db_path,
        )
    assert exc_info.value.reason == "consumed"


def test_wrong_verification_code_counts_attempts_and_fails_limit(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    verification_id = auth_db.create_pending_email_verification(
        email="bob@example.com",
        code_hash="good-hash",
        expires_at=2000,
        now=1000,
        db_path=db_path,
    )

    with pytest.raises(auth_db.EmailVerificationError) as first_error:
        auth_db.create_signup_job_with_email_verification(
            username="bob",
            email="bob@example.com",
            password="password123",
            display_name="Bob",
            email_verification_id=verification_id,
            email_verification_code_hash="bad-hash",
            max_attempts=2,
            now=1001,
            db_path=db_path,
        )
    assert first_error.value.reason == "invalid_code"
    assert _verification_row(db_path, verification_id)["attempt_count"] == 1

    with pytest.raises(auth_db.EmailVerificationError) as second_error:
        auth_db.create_signup_job_with_email_verification(
            username="bob",
            email="bob@example.com",
            password="password123",
            display_name="Bob",
            email_verification_id=verification_id,
            email_verification_code_hash="bad-hash",
            max_attempts=2,
            now=1002,
            db_path=db_path,
        )
    row = _verification_row(db_path, verification_id)
    assert second_error.value.reason == "too_many_attempts"
    assert row["attempt_count"] == 2
    assert row["status"] == "failed"


def test_expired_and_email_mismatch_verifications_are_rejected(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    expired_id = auth_db.create_pending_email_verification(
        email="carol@example.com",
        code_hash="good-hash",
        expires_at=1000,
        now=900,
        db_path=db_path,
    )
    mismatch_id = auth_db.create_pending_email_verification(
        email="dave@example.com",
        code_hash="good-hash",
        expires_at=2000,
        now=900,
        db_path=db_path,
    )

    with pytest.raises(auth_db.EmailVerificationError) as expired_error:
        auth_db.create_signup_job_with_email_verification(
            username="carol",
            email="carol@example.com",
            password="password123",
            display_name="Carol",
            email_verification_id=expired_id,
            email_verification_code_hash="good-hash",
            now=1001,
            db_path=db_path,
        )
    assert expired_error.value.reason == "expired"
    assert _verification_row(db_path, expired_id)["status"] == "expired"

    with pytest.raises(auth_db.EmailVerificationError) as mismatch_error:
        auth_db.create_signup_job_with_email_verification(
            username="dave",
            email="other@example.com",
            password="password123",
            display_name="Dave",
            email_verification_id=mismatch_id,
            email_verification_code_hash="good-hash",
            now=1001,
            db_path=db_path,
        )
    assert mismatch_error.value.reason == "email_mismatch"


@pytest.mark.asyncio
async def test_resend_mailer_uses_expected_https_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "email_123"}

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(mailer.httpx, "AsyncClient", FakeAsyncClient)

    result = await mailer.send_signup_verification_email(
        email="alice@example.com",
        code="123456",
        verification_id="verify-123",
        expires_at=2000,
        settings=mailer.ResendSettings(
            api_key="sk_test",
            mail_from="Potato Agent <noreply@example.com>",
            reply_to="support@example.com",
        ),
    )

    assert result.email_id == "email_123"
    assert captured["url"] == "https://api.resend.com/emails"
    headers = captured["headers"]
    assert headers["Authorization"] == "Bearer sk_test"
    assert headers["User-Agent"] == "potato-agent-interface/1.0"
    assert headers["Idempotency-Key"] == "verify-123"
    payload = captured["json"]
    assert payload["from"] == "Potato Agent <noreply@example.com>"
    assert payload["to"] == ["alice@example.com"]
    assert payload["reply_to"] == "support@example.com"
    assert "123456" in payload["text"]
    assert "123456" in payload["html"]


def _load_app(tmp_path, monkeypatch):
    db_path = tmp_path / "interface.db"
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(db_path))
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "test-secret")
    monkeypatch.delenv("INTERFACE_RESEND_API_KEY", raising=False)
    monkeypatch.delenv("INTERFACE_MAIL_FROM", raising=False)
    for module_name in (
        "interface.auth_db",
        "interface.runtime_state",
        "interface.mailer",
        "interface.app",
    ):
        sys.modules.pop(module_name, None)
    auth_db_mod = importlib.import_module("interface.auth_db")
    mailer_mod = importlib.import_module("interface.mailer")
    app_mod = importlib.import_module("interface.app")
    auth_db_mod.ensure_auth_db(db_path)
    return TestClient(app_mod.app), app_mod, auth_db_mod, mailer_mod, db_path


def test_signup_email_verification_api_sends_and_signup_consumes_code(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, _, mailer_mod, db_path = _load_app(tmp_path, monkeypatch)
    sent: dict[str, object] = {}

    async def fake_send_signup_verification_email(**kwargs):
        sent.update(kwargs)
        return mailer_mod.ResendEmailResult(email_id="email_123", status_code=200)

    monkeypatch.setattr(
        app_mod, "send_signup_verification_email", fake_send_signup_verification_email
    )
    try:
        response = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "Alice@Example.COM"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert re.fullmatch(r"\d{6}", str(sent["code"]))
        assert sent["email"] == "alice@example.com"

        row = _verification_row(db_path, payload["verification_id"])
        assert row["status"] == "pending"
        assert row["resend_email_id"] == "email_123"

        signup_response = client.post(
            "/api/auth/signup",
            json={
                "username": "alice",
                "email": "alice@example.com",
                "password": "password123",
                "display_name": "Alice",
                "email_verification_id": payload["verification_id"],
                "email_verification_code": sent["code"],
            },
        )
        assert signup_response.status_code == 200, signup_response.text
        job_id = signup_response.json()["job_id"]

        status_response = client.get(f"/api/auth/signup/{job_id}")
        assert status_response.status_code == 200, status_response.text
        job = status_response.json()["job"]
        assert job["status"] == "pending"
        assert job["email_verification_id"] == payload["verification_id"]
        assert _verification_row(db_path, payload["verification_id"])["status"] == "consumed"
    finally:
        client.close()


def test_signup_email_verification_api_marks_delivery_failure_unusable(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, _, mailer_mod, db_path = _load_app(tmp_path, monkeypatch)

    async def failing_send_signup_verification_email(**kwargs):
        raise mailer_mod.MailerDeliveryError(
            "resend failed", status_code=503, error_type="server_error"
        )

    monkeypatch.setattr(
        app_mod, "send_signup_verification_email", failing_send_signup_verification_email
    )
    try:
        response = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "fail@example.com"},
        )
        assert response.status_code == 503, response.text
        with sqlite3.connect(str(db_path)) as conn:
            statuses = [
                row[0]
                for row in conn.execute(
                    "select status from email_verifications where email = ?",
                    ("fail@example.com",),
                )
            ]
        assert statuses == ["failed"]
    finally:
        client.close()


def test_signup_email_verification_api_rejects_existing_user_email(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, auth_db_mod, mailer_mod, db_path = _load_app(
        tmp_path, monkeypatch
    )
    send_count = 0

    async def fake_send_signup_verification_email(**kwargs):
        nonlocal send_count
        send_count += 1
        return mailer_mod.ResendEmailResult(email_id="email_1", status_code=200)

    auth_db_mod.upsert_user(
        username="taken",
        email="taken@example.com",
        password="password123",
        mapping_username="taken",
        name="Taken",
        db_path=db_path,
    )
    monkeypatch.setattr(
        app_mod, "send_signup_verification_email", fake_send_signup_verification_email
    )
    try:
        response = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "taken@example.com"},
        )
        assert response.status_code == 409, response.text
        assert send_count == 0
    finally:
        client.close()


def test_signup_email_verification_api_rejects_missing_resend_config(
    tmp_path, monkeypatch
) -> None:
    client, _, _, _, db_path = _load_app(tmp_path, monkeypatch)
    try:
        response = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "missing-config@example.com"},
        )
        assert response.status_code == 503, response.text
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "select status from email_verifications where email = ?",
                ("missing-config@example.com",),
            ).fetchone()
        assert row is not None
        assert row[0] == "failed"
    finally:
        client.close()


def test_signup_email_verification_api_rejects_hourly_limits(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, auth_db_mod, mailer_mod, db_path = _load_app(
        tmp_path, monkeypatch
    )
    fixed_now = 10_000
    send_count = 0

    async def fake_send_signup_verification_email(**kwargs):
        nonlocal send_count
        send_count += 1
        return mailer_mod.ResendEmailResult(email_id="email_1", status_code=200)

    monkeypatch.setattr(app_mod, "_now_seconds", lambda: fixed_now)
    monkeypatch.setattr(
        app_mod, "send_signup_verification_email", fake_send_signup_verification_email
    )
    for index in range(5):
        auth_db_mod.create_pending_email_verification(
            email="hourly@example.com",
            code_hash=f"hash-{index}",
            expires_at=fixed_now + 600,
            now=fixed_now - 120 - index,
            db_path=db_path,
        )

    ip_hash = app_mod._hash_client_ip("203.0.113.10")
    for index in range(20):
        auth_db_mod.create_pending_email_verification(
            email=f"ip-limit-{index}@example.com",
            code_hash=f"ip-hash-{index}",
            client_ip_hash=ip_hash,
            expires_at=fixed_now + 600,
            now=fixed_now - 120 - index,
            db_path=db_path,
        )

    try:
        email_response = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "hourly@example.com"},
        )
        assert email_response.status_code == 429, email_response.text

        ip_response = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "new@example.com"},
            headers={"x-forwarded-for": "203.0.113.10"},
        )
        assert ip_response.status_code == 429, ip_response.text
        assert send_count == 0
    finally:
        client.close()


def test_signup_email_verification_api_rate_limits_recent_email(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, _, mailer_mod, _ = _load_app(tmp_path, monkeypatch)
    send_count = 0

    async def fake_send_signup_verification_email(**kwargs):
        nonlocal send_count
        send_count += 1
        return mailer_mod.ResendEmailResult(email_id=f"email_{send_count}", status_code=200)

    monkeypatch.setattr(
        app_mod, "send_signup_verification_email", fake_send_signup_verification_email
    )
    try:
        first = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "rate@example.com"},
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/auth/signup/email-verifications",
            json={"email": "rate@example.com"},
        )
        assert second.status_code == 429, second.text
        assert send_count == 1
    finally:
        client.close()

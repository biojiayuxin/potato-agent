from __future__ import annotations

import hashlib
import importlib
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _load_app(tmp_path: Path, monkeypatch):
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

    auth_db = importlib.import_module("interface.auth_db")
    app_mod = importlib.import_module("interface.app")
    auth_db.ensure_auth_db(db_path)

    target = SimpleNamespace(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=tmp_path / "alice",
        workdir=tmp_path / "alice",
        hermes_home=tmp_path / "alice" / ".hermes",
        systemd_service="hermes-alice.service",
    )
    provision_calls: list[str] = []

    def fake_provision_user(
        username: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        provision_calls.append(username)
        target.username = username
        target.email = email or ""
        target.display_name = display_name or username

    monkeypatch.setattr(app_mod.privileged_client, "provision_user", fake_provision_user)
    monkeypatch.setattr(
        app_mod.mapping_store,
        "get_target_by_username",
        lambda username: target if username == target.username else None,
    )
    monkeypatch.setattr(app_mod.mapping_store, "resolve_target", lambda **kwargs: target)
    return TestClient(app_mod.app), app_mod, auth_db, db_path, provision_calls


def _acceptance_payload(app_mod) -> dict[str, object]:
    return {
        "agreement_version": app_mod.CURRENT_AGREEMENT_VERSION,
        "agreement_accepted": True,
    }


def test_auth_consent_copy_is_concise_and_keeps_terms_links() -> None:
    html = (Path(__file__).parent / "static" / "lite" / "index.html").read_text(
        encoding="utf-8"
    )

    consent_copy = (
        'I have read and agree to the <a class="agreement-link" '
        'href="/user-agreement" target="_blank" rel="noopener noreferrer">'
        "Research Preview Terms and Privacy Notice</a>."
    )
    assert html.count(consent_copy) == 3
    assert "Research preview risks" not in html
    assert "I am a researcher aged 18 or older and accept" not in html


def test_agreement_metadata_and_versioned_document_are_consistent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("INTERFACE_SESSION_TTL_SECONDS", str(30 * 24 * 60 * 60))
    client, app_mod, _, _, _ = _load_app(tmp_path, monkeypatch)
    try:
        metadata_response = client.get("/api/legal/agreement")
        current_response = client.get("/user-agreement")
        versioned_response = client.get(
            f"/user-agreement/{app_mod.CURRENT_AGREEMENT_VERSION}"
        )

        assert metadata_response.status_code == 200
        metadata = metadata_response.json()
        assert app_mod.SESSION_TTL_SECONDS == 7 * 24 * 60 * 60
        assert metadata["version"] == "2026-08-25"
        assert metadata["effective_date"] == "2026-08-25"
        assert metadata["url"] == "/user-agreement/2026-08-25"
        assert metadata["sha256"] == hashlib.sha256(versioned_response.content).hexdigest()
        assert current_response.content == versioned_response.content
        assert "immutable" in versioned_response.headers["cache-control"]
        assert client.get("/user-agreement/obsolete").status_code == 404
        for policy_url in (
            "https://openai.com/policies/business-data/",
            "https://ai.google.dev/gemini-api/terms",
            "https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html",
            "https://platform.kimi.com/docs/agreement/userprivacy",
            "https://resend.com/legal/privacy-policy",
            "https://tavily.com/privacy",
        ):
            assert policy_url in versioned_response.text
    finally:
        client.close()


def test_old_auth_database_migration_does_not_fabricate_acceptance(
    tmp_path, monkeypatch
) -> None:
    client, _, auth_db, db_path, _ = _load_app(tmp_path, monkeypatch)
    client.close()
    user = auth_db.upsert_user(
        username="legacy",
        email="legacy@example.com",
        password="Password123!",
        mapping_username="legacy",
        db_path=db_path,
    )

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            drop table agreement_acceptances;
            drop index idx_signup_jobs_username;
            drop index idx_signup_jobs_email;
            drop table signup_jobs;
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
    auth_db._AUTH_DB_IDENTITIES.clear()
    auth_db.ensure_auth_db(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        acceptance_count = conn.execute(
            "select count(*) from agreement_acceptances"
        ).fetchone()[0]
        acceptance_columns = {
            str(row[1])
            for row in conn.execute("pragma table_info(agreement_acceptances)")
        }
        signup_columns = {
            str(row[1]) for row in conn.execute("pragma table_info(signup_jobs)")
        }
    assert acceptance_count == 0
    assert acceptance_columns == {
        "user_id",
        "agreement_version",
        "document_sha256",
        "accepted_at",
        "source",
        "user_deleted_at",
        "retain_until",
    }
    assert auth_db.has_agreement_acceptance(user.id, "2026-08-25", db_path=db_path) is False
    assert {
        "agreement_version",
        "agreement_document_sha256",
        "agreement_accepted_at",
        "agreement_source",
    }.issubset(signup_columns)


def test_signup_acceptance_transfers_atomically_and_audit_expires_after_deletion(
    tmp_path, monkeypatch
) -> None:
    client, _, auth_db, db_path, _ = _load_app(tmp_path, monkeypatch)
    client.close()
    document_sha256 = "a" * 64
    job_id = auth_db.create_signup_job(
        username="researcher",
        email="researcher@example.com",
        password="Password123!",
        display_name="Researcher",
        agreement_version="2026-08-25",
        agreement_document_sha256=document_sha256,
        agreement_accepted_at=1234,
        db_path=db_path,
    )

    user = auth_db.activate_signup_user(
        job_id,
        mapping_username="researcher",
        db_path=db_path,
    )
    acceptance = auth_db.get_agreement_acceptance(
        user.id, "2026-08-25", db_path=db_path
    )
    assert acceptance == {
        "user_id": user.id,
        "agreement_version": "2026-08-25",
        "document_sha256": document_sha256,
        "accepted_at": 1234,
        "source": "signup",
        "user_deleted_at": None,
        "retain_until": None,
    }

    assert auth_db.record_agreement_acceptance(
        user_id=user.id,
        agreement_version="2026-08-25",
        document_sha256=document_sha256,
        source="signin",
        now=9999,
        db_path=db_path,
    ) is False
    assert auth_db.get_agreement_acceptance(
        user.id, "2026-08-25", db_path=db_path
    )["accepted_at"] == 1234
    with pytest.raises(ValueError, match="version and document digest"):
        auth_db.record_agreement_acceptance(
            user_id=user.id,
            agreement_version="2026-08-25",
            document_sha256="b" * 64,
            source="signin",
            now=10_000,
            db_path=db_path,
        )

    deleted_at = 20_000
    monkeypatch.setattr(auth_db.time, "time", lambda: deleted_at)
    assert auth_db.delete_user_by_id(user.id, db_path=db_path)
    retained = auth_db.get_agreement_acceptance(
        user.id, "2026-08-25", db_path=db_path
    )
    assert retained["user_deleted_at"] == deleted_at
    assert retained["retain_until"] == (
        deleted_at + auth_db.DEFAULT_AGREEMENT_AUDIT_RETENTION_SECONDS
    )
    assert auth_db.cleanup_expired_agreement_acceptances(
        now=retained["retain_until"] - 1, db_path=db_path
    ) == 0
    assert auth_db.cleanup_expired_agreement_acceptances(
        now=retained["retain_until"], db_path=db_path
    ) == 1


def test_signin_requires_current_account_acceptance_and_preserves_first_record(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, auth_db, db_path, _ = _load_app(tmp_path, monkeypatch)
    user = auth_db.upsert_user(
        username="alice",
        email="alice@example.com",
        password="Password123!",
        mapping_username="alice",
        db_path=db_path,
    )
    try:
        invalid = client.post(
            "/api/auth/signin",
            json={
                "email": "alice@example.com",
                "password": "wrong",
                **_acceptance_payload(app_mod),
            },
        )
        assert invalid.status_code == 401
        assert app_mod.SESSION_COOKIE_NAME not in client.cookies
        assert auth_db.get_agreement_acceptance(
            user.id, app_mod.CURRENT_AGREEMENT_VERSION, db_path=db_path
        ) is None

        required = client.post(
            "/api/auth/signin",
            json={"email": "alice@example.com", "password": "Password123!"},
        )
        assert required.status_code == 428
        assert required.json()["error"] == "agreement_required"
        assert app_mod.SESSION_COOKIE_NAME not in client.cookies

        stale = client.post(
            "/api/auth/signin",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "agreement_version": "obsolete",
                "agreement_accepted": True,
            },
        )
        assert stale.status_code == 428

        accepted = client.post(
            "/api/auth/signin",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                **_acceptance_payload(app_mod),
            },
        )
        assert accepted.status_code == 200
        assert app_mod.SESSION_COOKIE_NAME in client.cookies
        first_record = auth_db.get_agreement_acceptance(
            user.id, app_mod.CURRENT_AGREEMENT_VERSION, db_path=db_path
        )
        assert first_record["source"] == "signin"

        client.cookies.clear()
        remembered = client.post(
            "/api/auth/signin",
            json={"email": "alice@example.com", "password": "Password123!"},
        )
        assert remembered.status_code == 200
        assert auth_db.get_agreement_acceptance(
            user.id, app_mod.CURRENT_AGREEMENT_VERSION, db_path=db_path
        )["accepted_at"] == first_record["accepted_at"]
    finally:
        client.close()


def test_terms_upgrade_preserves_existing_session_and_requires_next_signin(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, auth_db, db_path, _ = _load_app(tmp_path, monkeypatch)
    user = auth_db.upsert_user(
        username="alice",
        email="alice@example.com",
        password="Password123!",
        mapping_username="alice",
        db_path=db_path,
    )
    try:
        assert client.post(
            "/api/auth/signin",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                **_acceptance_payload(app_mod),
            },
        ).status_code == 200

        upgraded = {
            "version": "2026-09-01",
            "effective_date": "2026-09-01",
            "title": "Updated terms",
            "url": "/user-agreement/2026-09-01",
            "sha256": "b" * 64,
        }
        monkeypatch.setattr(app_mod, "current_agreement_metadata", lambda: upgraded)

        session = client.get("/api/auth/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is True

        client.cookies.clear()
        required = client.post(
            "/api/auth/signin",
            json={"email": "alice@example.com", "password": "Password123!"},
        )
        assert required.status_code == 428
        assert required.json()["agreement"]["version"] == "2026-09-01"

        accepted = client.post(
            "/api/auth/signin",
            json={
                "email": "alice@example.com",
                "password": "Password123!",
                "agreement_version": "2026-09-01",
                "agreement_accepted": True,
            },
        )
        assert accepted.status_code == 200
        assert auth_db.has_agreement_acceptance(
            user.id,
            "2026-09-01",
            document_sha256="b" * 64,
            db_path=db_path,
        )
    finally:
        client.close()


def test_quick_start_rejects_missing_refused_and_stale_terms_without_provisioning(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, auth_db, db_path, provision_calls = _load_app(
        tmp_path, monkeypatch
    )
    try:
        requests = (
            None,
            {
                "agreement_version": app_mod.CURRENT_AGREEMENT_VERSION,
                "agreement_accepted": False,
            },
            {"agreement_version": "obsolete", "agreement_accepted": True},
        )
        for payload in requests:
            response = (
                client.post("/api/auth/temporary")
                if payload is None
                else client.post("/api/auth/temporary", json=payload)
            )
            assert response.status_code == 400
        assert provision_calls == []
        assert auth_db.list_users(db_path=db_path) == []
        assert app_mod.SESSION_COOKIE_NAME not in client.cookies

        accepted = client.post(
            "/api/auth/temporary", json=_acceptance_payload(app_mod)
        )
        assert accepted.status_code == 200
        assert len(provision_calls) == 1
        temporary_user = auth_db.list_users(db_path=db_path)[0]
        evidence = auth_db.get_agreement_acceptance(
            temporary_user.id,
            app_mod.CURRENT_AGREEMENT_VERSION,
            db_path=db_path,
        )
        assert evidence["source"] == "temporary"
    finally:
        client.close()


def test_signup_api_rejects_missing_refused_and_stale_terms(tmp_path, monkeypatch) -> None:
    client, app_mod, _, _, _ = _load_app(tmp_path, monkeypatch)
    create_calls: list[dict] = []
    monkeypatch.setattr(
        app_mod,
        "create_signup_job_with_email_verification",
        lambda **kwargs: create_calls.append(kwargs) or "job-1",
    )
    payload = {
        "username": "new_user",
        "email": "new@example.com",
        "password": "Password123!",
        "display_name": "New User",
        "email_verification_id": "verification-1",
        "email_verification_code": "123456",
    }
    try:
        assert client.post("/api/auth/signup", json=payload).status_code == 400
        assert client.post(
            "/api/auth/signup",
            json={
                **payload,
                "agreement_version": app_mod.CURRENT_AGREEMENT_VERSION,
                "agreement_accepted": False,
            },
        ).status_code == 400
        assert client.post(
            "/api/auth/signup",
            json={
                **payload,
                "agreement_version": "obsolete",
                "agreement_accepted": True,
            },
        ).status_code == 400
        assert create_calls == []

        accepted = client.post(
            "/api/auth/signup", json={**payload, **_acceptance_payload(app_mod)}
        )
        assert accepted.status_code == 200
        assert create_calls[0]["agreement_version"] == app_mod.CURRENT_AGREEMENT_VERSION
        assert create_calls[0]["agreement_document_sha256"] == (
            app_mod.current_agreement_metadata()["sha256"]
        )
    finally:
        client.close()


def test_lite_frontend_has_unchecked_accessible_agreement_controls() -> None:
    root = Path(__file__).resolve().parent
    html = (root / "static" / "lite" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "static" / "lite" / "app.js").read_text(encoding="utf-8")
    styles = (root / "static" / "lite" / "styles.css").read_text(encoding="utf-8")

    assert 'id="register-agreement-checkbox" type="checkbox"' in html
    assert 'id="signin-agreement-checkbox" type="checkbox"' in html
    assert 'id="temporary-agreement-checkbox" type="checkbox"' in html
    assert 'id="temporary-confirm-start" type="button" class="primary" disabled' in html
    assert "agreement_accepted: true" in app_js
    assert "localStorage" not in "\n".join(
        line for line in app_js.splitlines() if "agreement" in line.lower()
    )
    assert ".agreement-checkbox-row input" in styles
    assert ".agreement-checkbox-row a:focus-visible" in styles

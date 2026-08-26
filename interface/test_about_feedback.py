from __future__ import annotations

import importlib
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "interface" / "static"
HPC_DEPLOYMENT_PATH = REPO_ROOT / "HPC_DEPLOYMENT.md"
PUBLIC_HTML_PATHS = (
    STATIC_DIR / "lite" / "index.html",
    STATIC_DIR / "lite" / "high-resolution-required.html",
    STATIC_DIR / "genes" / "index.html",
    STATIC_DIR / "spatial" / "index.html",
    STATIC_DIR / "wgcna" / "index.html",
    STATIC_DIR / "bulk_rnaseq" / "index.html",
    STATIC_DIR / "genomes" / "index.html",
    STATIC_DIR / "genome_browser" / "index.html",
    STATIC_DIR / "about" / "index.html",
)


def _load_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(tmp_path / "interface.db"))
    monkeypatch.setenv("INTERFACE_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("INTERFACE_FEEDBACK_DB", str(tmp_path / "feedback.db"))
    monkeypatch.delenv("INTERFACE_RESEND_API_KEY", raising=False)
    monkeypatch.delenv("INTERFACE_RESEND_API_KEY_FILE", raising=False)
    monkeypatch.delenv("INTERFACE_MAIL_FROM", raising=False)
    for module_name in (
        "interface.feedback_store",
        "interface.mailer",
        "interface.app",
    ):
        sys.modules.pop(module_name, None)
    feedback_store_mod = importlib.import_module("interface.feedback_store")
    mailer_mod = importlib.import_module("interface.mailer")
    app_mod = importlib.import_module("interface.app")
    return (
        TestClient(app_mod.app),
        app_mod,
        feedback_store_mod,
        mailer_mod,
        tmp_path / "feedback.db",
    )


def _feedback_rows(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return list(
            conn.execute(
                "select * from feedback_submissions order by created_at, id"
            )
        )


def test_about_and_shared_feedback_assets_are_public(tmp_path, monkeypatch) -> None:
    client, _, _, _, _ = _load_app(tmp_path, monkeypatch)
    try:
        expected_types = {
            "/about": "text/html",
            "/static/about/styles.css": "text/css",
            "/static/about/potato-agent-architecture.png": "image/png",
            "/static/shared/feedback.css": "text/css",
            "/static/shared/feedback.js": "text/javascript",
        }
        for path, content_type in expected_types.items():
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert response.headers["content-type"].startswith(content_type)

        about_html = client.get("/about").text
        assert '/static/genes/styles.css' in about_html
        assert '<div class="genes-app">' in about_html
        assert '<main class="genes-main about-main">' in about_html
        assert '<article class="about-readme"' in about_html
        assert "About Potato Agent" in about_html
        assert "Source Code" in about_html
        assert "Open APIs" in about_html
        assert "Architecture, Skills, and Databases" in about_html
        assert "README.md" not in about_html
        assert "Potato Agent connects an AI agent" not in about_html
        assert 'class="api-list"' not in about_html
        assert "Browse the skills and installation resources" not in about_html
        assert "The diagram below provides an overview" not in about_html
        assert "Potato Agent architecture, integrated Agent Skills" not in about_html
        assert "View full-size image" not in about_html
        assert "<figcaption>" not in about_html
        assert "https://github.com/biojiayuxin/potato-agent" in about_html
        assert (
            "https://github.com/biojiayuxin/potato-agent/tree/lite/skills"
            in about_html
        )
        normalized_about_html = " ".join(about_html.split()).lower()
        for api_domain in (
            "genomic data",
            "bulk rna-seq",
            "spatial transcriptomics",
            "wgcna networks",
            "genome-wide gene function prediction",
        ):
            assert api_domain in normalized_about_html
        assert 'target="_blank"' in about_html
        assert 'src="/static/about/potato-agent-architecture.png"' in about_html
        assert 'width="2405" height="2739"' in about_html
        assert 'alt="Potato Agent architecture diagram"' in about_html
    finally:
        client.close()


def test_all_public_pages_include_about_and_shared_feedback_assets() -> None:
    for html_path in PUBLIC_HTML_PATHS:
        content = html_path.read_text(encoding="utf-8")
        assert 'href="/about"' in content, html_path
        assert "/static/shared/feedback.css" in content, html_path
        assert "/static/shared/feedback.js" in content, html_path

    lite_html = (STATIC_DIR / "lite" / "index.html").read_text(encoding="utf-8")
    assert (
        '<a class="portal-nav-item" href="/about" '
        'data-mobile-supported="true">About</a>'
    ) in lite_html


def test_shared_feedback_ui_covers_accessibility_and_workspace_states() -> None:
    feedback_js = (STATIC_DIR / "shared" / "feedback.js").read_text(
        encoding="utf-8"
    )
    feedback_css = (STATIC_DIR / "shared" / "feedback.css").read_text(
        encoding="utf-8"
    )

    for marker in (
        'role="dialog"',
        'aria-modal="true"',
        'maxlength="5000"',
        'maxlength="254"',
        "event.key === 'Escape'",
        "event.key !== 'Tab'",
        "if (submitting) return",
        "previousFocus",
        "form.reset()",
        "setStatus('pending', 'Sending feedback...')",
        "setStatus('success', 'Thank you. Your feedback was sent.')",
        "setStatus('error', 'Feedback could not be sent. Please try again later.')",
        "MutationObserver",
        "attributeFilter: ['hidden', 'style', 'class']",
        "modal.dataset.state !== 'success'",
        "credentials: 'same-origin'",
    ):
        assert marker in feedback_js

    assert "env(safe-area-inset-bottom" in feedback_css
    assert ".feedback-modal[hidden]" in feedback_css
    assert "body.feedback-dialog-open" in feedback_css


@pytest.mark.asyncio
async def test_feedback_mail_uses_fixed_fields_and_escapes_html(monkeypatch) -> None:
    import interface.mailer as mailer

    captured: dict[str, object] = {}

    async def fake_send_resend_email(**kwargs):
        captured.update(kwargs)
        return mailer.ResendEmailResult(email_id="email_123", status_code=200)

    monkeypatch.setattr(mailer, "send_resend_email", fake_send_resend_email)
    settings = mailer.ResendSettings(
        api_key="sk_test",
        mail_from="Potato Agent <noreply@example.com>",
        reply_to="support@example.com",
    )

    result = await mailer.send_feedback_email(
        message='<script>alert("feedback")</script> & more',
        contact_email="contact@example.com",
        page_path="/genes/<unsafe>",
        submitted_at="2026-08-19T00:00:00Z",
        submission_id="submission-123",
        settings=settings,
    )

    assert result.email_id == "email_123"
    assert captured["email"] == "jiayuxin@ynnu.edu.cn"
    assert captured["subject"] == mailer.FEEDBACK_EMAIL_SUBJECT
    assert captured["idempotency_key"] == "submission-123"
    assert captured["settings"] is settings
    assert "contact@example.com" in str(captured["text"])
    assert "Unverified contact email" in str(captured["text"])
    assert '<script>alert("feedback")</script>' not in str(captured["html"])
    assert "&lt;script&gt;alert(&quot;feedback&quot;)&lt;/script&gt;" in str(
        captured["html"]
    )
    assert "/genes/&lt;unsafe&gt;" in str(captured["html"])
    assert "reply_to" not in captured


def test_hpc_deployment_documents_feedback_without_recipient_override() -> None:
    content = HPC_DEPLOYMENT_PATH.read_text(encoding="utf-8")

    for marker in (
        "Environment=INTERFACE_FEEDBACK_DB=/var/lib/potato-agent/data/feedback.db",
        "interface/static/shared/feedback.js",
        "interface/static/about/potato-agent-architecture.png",
        "feedback.db` 会保存明文反馈正文和可选联系邮箱",
        "FEEDBACK_VALIDATION_STATUS",
        "required = {\"message\", \"contact_email\"}",
        "sudo -u \"$ORDINARY_USER\" test ! -r /var/lib/potato-agent/data/feedback.db",
    ):
        assert marker in content
    assert "Environment=INTERFACE_FEEDBACK_TO" not in content


def test_feedback_api_success_validation_and_content_storage(
    tmp_path, monkeypatch
) -> None:
    client, app_mod, _, mailer_mod, db_path = _load_app(tmp_path, monkeypatch)
    sent: dict[str, object] = {}

    async def fake_send_feedback_email(**kwargs):
        sent.update(kwargs)
        return mailer_mod.ResendEmailResult(email_id="email_success", status_code=200)

    monkeypatch.setattr(app_mod, "send_feedback_email", fake_send_feedback_email)
    try:
        response = client.post(
            "/api/feedback",
            json={
                "message": "  Useful feedback to store.  ",
                "contact_email": "contact@example.com",
                "page_path": "/genes",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True}
        assert sent["message"] == "Useful feedback to store."
        assert sent["contact_email"] == "contact@example.com"
        assert sent["page_path"] == "/genes"

        rows = _feedback_rows(db_path)
        assert len(rows) == 1
        assert set(rows[0].keys()) == {
            "id",
            "message",
            "contact_email",
            "status",
            "resend_email_id",
            "created_at",
            "updated_at",
        }
        assert rows[0]["status"] == "sent"
        assert rows[0]["resend_email_id"] == "email_success"
        assert rows[0]["message"] == "Useful feedback to store."
        assert rows[0]["contact_email"] == "contact@example.com"

        bad_requests = (
            {"message": " ", "contact_email": "", "page_path": "/genes"},
            {"message": "x" * 5_001, "contact_email": "", "page_path": "/genes"},
            {"message": "hello", "contact_email": "not-an-email", "page_path": "/genes"},
            {"message": "hello", "contact_email": "", "page_path": "https://example.com"},
        )
        for payload in bad_requests:
            invalid = client.post("/api/feedback", json=payload)
            assert invalid.status_code == 400, (payload, invalid.text)

        wrong_type = client.post(
            "/api/feedback",
            json={"message": 42, "contact_email": "", "page_path": "/genes"},
        )
        assert wrong_type.status_code == 422
        client_fields = client.post(
            "/api/feedback",
            json={
                "message": "hello",
                "contact_email": "",
                "page_path": "/genes",
                "subject": "client subject",
                "recipient": "attacker@example.com",
            },
        )
        assert client_fields.status_code == 422

        oversized = client.post(
            "/api/feedback",
            content=b"x" * (32 * 1024 + 1),
            headers={"content-type": "application/json"},
        )
        assert oversized.status_code == 413
    finally:
        client.close()


def test_feedback_store_migrates_existing_metadata_database(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "feedback.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE feedback_submissions (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                resend_email_id TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO feedback_submissions (
                id, status, resend_email_id, created_at, updated_at
            ) VALUES ('existing', 'sent', 'email_existing', 1, 1)
            """
        )

    monkeypatch.setenv("INTERFACE_FEEDBACK_DB", str(db_path))
    sys.modules.pop("interface.feedback_store", None)
    feedback_store = importlib.import_module("interface.feedback_store")
    feedback_store.ensure_feedback_store()
    feedback_store.ensure_feedback_store()

    rows = _feedback_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == "existing"
    assert rows[0]["message"] == ""
    assert rows[0]["contact_email"] == ""


def test_feedback_api_configuration_failure_is_generic_and_counts_toward_limit(
    tmp_path, monkeypatch, caplog
) -> None:
    client, app_mod, _, mailer_mod, db_path = _load_app(tmp_path, monkeypatch)
    private_message = "private feedback body"
    private_email = "private-contact@example.com"

    async def fail_delivery(**_kwargs):
        raise mailer_mod.MailerDeliveryError(
            f"provider response included {private_message} and {private_email}",
            status_code=503,
            error_type="upstream_failure",
        )

    monkeypatch.setattr(app_mod, "send_feedback_email", fail_delivery)
    try:
        for _ in range(20):
            response = client.post(
                "/api/feedback",
                json={
                    "message": private_message,
                    "contact_email": private_email,
                    "page_path": "/about",
                },
            )
            assert response.status_code == 503
            assert response.json() == {
                "detail": "Feedback is temporarily unavailable. Please try again later."
            }

        limited = client.post(
            "/api/feedback",
            json={
                "message": private_message,
                "contact_email": private_email,
                "page_path": "/about",
            },
        )
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 3_600
        assert [row["status"] for row in _feedback_rows(db_path)] == ["failed"] * 20
        assert private_message not in caplog.text
        assert private_email not in caplog.text
        rows = _feedback_rows(db_path)
        assert {row["message"] for row in rows} == {private_message}
        assert {row["contact_email"] for row in rows} == {private_email}
    finally:
        client.close()


def test_feedback_api_missing_mail_configuration_is_generic(
    tmp_path, monkeypatch, caplog
) -> None:
    client, _, _, _, _ = _load_app(tmp_path, monkeypatch)
    try:
        response = client.post(
            "/api/feedback",
            json={
                "message": "not logged body",
                "contact_email": "not-logged@example.com",
                "page_path": "/",
            },
        )
        assert response.status_code == 503
        assert response.json() == {
            "detail": "Feedback is temporarily unavailable. Please try again later."
        }
        assert "not logged body" not in caplog.text
        assert "not-logged@example.com" not in caplog.text
    finally:
        client.close()


def test_feedback_store_enforces_limit_atomically_under_concurrency(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("INTERFACE_FEEDBACK_DB", str(tmp_path / "feedback.db"))
    sys.modules.pop("interface.feedback_store", None)
    feedback_store = importlib.import_module("interface.feedback_store")
    now = 2_000_000_000
    barrier = threading.Barrier(32)

    def claim_once():
        barrier.wait()
        return feedback_store.claim_feedback_submission(
            message="concurrent feedback",
            contact_email="contact@example.com",
            now=now,
        )

    with ThreadPoolExecutor(max_workers=32) as executor:
        claims = list(executor.map(lambda _index: claim_once(), range(32)))

    accepted = [claim for claim in claims if claim.accepted]
    rejected = [claim for claim in claims if not claim.accepted]
    assert len(accepted) == 20
    assert len(rejected) == 12
    assert {claim.retry_after for claim in rejected} == {3_600}
    assert len(_feedback_rows(tmp_path / "feedback.db")) == 20

    next_window = feedback_store.claim_feedback_submission(
        message="next window",
        contact_email="",
        now=now + 3_600,
    )
    assert next_window.accepted is True


def test_feedback_store_cleans_metadata_after_thirty_days(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INTERFACE_FEEDBACK_DB", str(tmp_path / "feedback.db"))
    sys.modules.pop("interface.feedback_store", None)
    feedback_store = importlib.import_module("interface.feedback_store")
    now = 2_000_000_000
    old_claim = feedback_store.claim_feedback_submission(
        message="expired feedback",
        contact_email="expired@example.com",
        now=now - feedback_store.FEEDBACK_RETENTION_SECONDS - 1
    )
    current_claim = feedback_store.claim_feedback_submission(
        message="current feedback",
        contact_email="current@example.com",
        now=now,
    )
    assert old_claim.accepted and current_claim.accepted

    rows = _feedback_rows(tmp_path / "feedback.db")
    assert [row["id"] for row in rows] == [current_claim.submission_id]
    assert rows[0]["message"] == "current feedback"
    assert rows[0]["contact_email"] == "current@example.com"


def test_feedback_api_does_not_refresh_runtime_activity(tmp_path, monkeypatch) -> None:
    _, app_mod, _, _, _ = _load_app(tmp_path, monkeypatch)

    class Request:
        method = "POST"
        scope = {"path": "/api/feedback"}
        query_params: dict[str, str] = {}

    assert app_mod._should_refresh_activity_for_request(Request()) is False

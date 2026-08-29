from __future__ import annotations

import contextlib
import importlib
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


SHARE_HEADERS = {"X-Potato-Request": "1"}
SHARED_MESSAGES = [
    {"role": "user", "content": "Shared question"},
    {"role": "assistant", "content": "Shared answer"},
]


def _target(tmp_path: Path, username: str):
    home_dir = tmp_path / username
    hermes_home = home_dir / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        username=username,
        email=f"{username}@example.com",
        display_name=username.title(),
        linux_user=f"hmx_{username}",
        home_dir=home_dir,
        workdir=home_dir / "work",
        hermes_home=hermes_home,
        state_db_path=hermes_home / "state.db",
        systemd_service=f"hermes-{username}.service",
    )


def _load_app(tmp_path: Path, monkeypatch):
    auth_db_path = tmp_path / "interface.db"
    share_db_path = tmp_path / "chat-shares.db"
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text("users: []\n", encoding="utf-8")
    monkeypatch.setenv(
        "INTERFACE_SESSION_SECRET", "chat-sharing-test-secret-at-least-32-bytes"
    )
    monkeypatch.setenv("INTERFACE_SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(auth_db_path))
    monkeypatch.setenv("INTERFACE_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("INTERFACE_FEEDBACK_DB", str(tmp_path / "feedback.db"))
    monkeypatch.setenv("INTERFACE_CHAT_SHARE_DB", str(share_db_path))
    monkeypatch.setenv("POTATO_AGENT_MAPPING_PATH", str(mapping_path))
    monkeypatch.delenv("INTERFACE_CHAT_SHARING_ENABLED", raising=False)

    for module_name in list(sys.modules):
        if module_name == "interface" or module_name.startswith("interface."):
            monkeypatch.delitem(sys.modules, module_name, raising=False)

    auth_db = importlib.import_module("interface.auth_db")
    share_store = importlib.import_module("interface.chat_share_store")
    app_mod = importlib.import_module("interface.app")
    assert app_mod.CHAT_SHARING_ENABLED is True
    auth_db.ensure_auth_db(auth_db_path)

    targets: dict[str, object] = {}

    def fake_provision_user(
        username: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        target = _target(tmp_path, username)
        target.email = email or ""
        target.display_name = display_name or username
        targets[username] = target

    monkeypatch.setattr(app_mod.privileged_client, "provision_user", fake_provision_user)
    monkeypatch.setattr(
        app_mod.privileged_client,
        "ensure_runtime",
        lambda target: {"active": True, "username": target.username},
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
    return app_mod, auth_db, share_store, targets, auth_db_path, share_db_path


def _add_formal_user(auth_db, targets, tmp_path: Path, username: str):
    targets[username] = _target(tmp_path, username)
    return auth_db.upsert_user(
        username=username,
        email=f"{username}@example.com",
        password="Password123!",
        mapping_username=username,
        name=username.title(),
    )


def _signin(client: TestClient, app_mod, username: str) -> None:
    response = client.post(
        "/api/auth/signin",
        json={
            "email": f"{username}@example.com",
            "password": "Password123!",
            "agreement_version": app_mod.CURRENT_AGREEMENT_VERSION,
            "agreement_accepted": True,
        },
    )
    assert response.status_code == 200, response.text


def _temporary_signin(client: TestClient, app_mod) -> dict[str, object]:
    response = client.post(
        "/api/auth/temporary",
        json={
            "agreement_version": app_mod.CURRENT_AGREEMENT_VERSION,
            "agreement_accepted": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_share_endpoints_require_auth_protected_json_and_feature_flag(
    tmp_path, monkeypatch
) -> None:
    app_mod, auth_db, share_store, targets, _, share_db_path = _load_app(
        tmp_path, monkeypatch
    )
    _add_formal_user(auth_db, targets, tmp_path, "alice")
    client = TestClient(app_mod.app)
    _signin(client, app_mod, "alice")
    monkeypatch.setattr(
        app_mod,
        "_load_chat_share_snapshot_sync",
        lambda user, session_id: (
            session_id,
            "Original conversation",
            SHARED_MESSAGES,
        ),
    )
    try:
        missing_header = client.post("/api/sessions/source-1/shares", json={})
        assert missing_header.status_code == 403
        assert missing_header.json()["detail"]["code"] == "share_request_required"

        wrong_type = client.post(
            "/api/sessions/source-1/shares",
            content="{}",
            headers={**SHARE_HEADERS, "Content-Type": "text/plain"},
        )
        assert wrong_type.status_code == 415
        assert wrong_type.json()["detail"]["code"] == "json_required"

        extra_field = client.post(
            "/api/sessions/source-1/shares",
            headers=SHARE_HEADERS,
            json={"title": "client controlled"},
        )
        assert extra_field.status_code == 422

        body_limit = client.post(
            "/api/chat-shares/import",
            content=b"x" * (2 * 1024 * 1024 + 1),
            headers={**SHARE_HEADERS, "Content-Type": "application/json"},
        )
        assert body_limit.status_code == 413
        assert body_limit.headers["cache-control"] == "no-store"
        assert body_limit.headers["pragma"] == "no-cache"

        before_create = int(time.time())
        created = client.post(
            "/api/sessions/source-1/shares",
            headers=SHARE_HEADERS,
            json={},
        )
        assert created.status_code == 200, created.text
        assert set(created.json()) == {"token", "expires_at", "max_recipients"}
        assert created.json()["max_recipients"] == 100
        assert before_create + 7 * 24 * 60 * 60 <= created.json()["expires_at"] <= (
            int(time.time()) + 7 * 24 * 60 * 60
        )
        assert created.headers["cache-control"] == "no-store"
        claim = share_store.claim_chat_share_import(
            token=created.json()["token"],
            recipient_user_id="title-check",
            db_path=share_db_path,
        )
        assert claim.title == "Original conversation"

        monkeypatch.setattr(
            app_mod,
            "_load_chat_share_snapshot_sync",
            lambda user, session_id: (
                session_id,
                "Original conversation",
                [{"role": "user", "content": "x" * (64 * 1024 + 1)}],
            ),
        )
        oversized = client.post(
            "/api/sessions/source-1/shares",
            headers=SHARE_HEADERS,
            json={},
        )
        assert oversized.status_code == 413
        assert oversized.json()["detail"]["code"] == "share_too_large"

        monkeypatch.setattr(
            app_mod,
            "create_chat_share",
            lambda **kwargs: (_ for _ in ()).throw(
                app_mod.ChatShareLimitError("create_rate_limited", 23)
            ),
        )
        limited = client.post(
            "/api/sessions/source-1/shares",
            headers=SHARE_HEADERS,
            json={},
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "23"

        unauthenticated_client = TestClient(app_mod.app)
        try:
            unauthenticated = unauthenticated_client.post(
                "/api/chat-shares/import",
                headers=SHARE_HEADERS,
                json={"token": "A" * 43},
            )
        finally:
            unauthenticated_client.close()
        assert unauthenticated.status_code == 401

        session = client.get("/api/auth/session").json()
        assert session["user"]["features"]["chat_sharing"] is True

        monkeypatch.setattr(app_mod, "CHAT_SHARING_ENABLED", False)
        disabled = client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": "A" * 43},
        )
        assert disabled.status_code == 503
        assert disabled.json()["detail"]["code"] == "sharing_disabled"
    finally:
        client.close()


def test_formal_login_and_quick_start_import_idempotently(
    tmp_path, monkeypatch
) -> None:
    app_mod, auth_db, share_store, targets, _, share_db_path = _load_app(
        tmp_path, monkeypatch
    )
    owner = _add_formal_user(auth_db, targets, tmp_path, "owner")
    formal = _add_formal_user(auth_db, targets, tmp_path, "formal")
    created = share_store.create_chat_share(
        owner_user_id=owner.id,
        source_session_id="source-1",
        title="Source conversation",
        messages=SHARED_MESSAGES,
        now=int(time.time()),
        db_path=share_db_path,
    )
    import_calls: list[tuple[str, str]] = []
    real_import = app_mod._import_shared_session_sync

    def tracked_import(target, *, session_id, title, messages):
        import_calls.append((target.username, session_id))
        return real_import(
            target,
            session_id=session_id,
            title=title,
            messages=messages,
        )

    monkeypatch.setattr(app_mod, "_import_shared_session_sync", tracked_import)

    formal_client = TestClient(app_mod.app)
    quick_client = TestClient(app_mod.app)
    try:
        _signin(formal_client, app_mod, "formal")
        first = formal_client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": created.token},
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] is True
        assert first.json()["session"]["title"] == "Source conversation (shared)"
        assert first.json()["messages"][0]["content"] == "Shared question"
        formal_session_id = first.json()["session"]["id"]
        with sqlite3.connect(str(targets["formal"].state_db_path)) as conn:
            session_row = conn.execute(
                "select source, title from sessions where id = ?",
                (formal_session_id,),
            ).fetchone()
            message_rows = conn.execute(
                "select role, content, reasoning, tool_calls from messages "
                "where session_id = ? order by id",
                (formal_session_id,),
            ).fetchall()
        assert session_row == ("tui", "Source conversation (shared)")
        assert message_rows == [
            ("user", "Shared question", None, None),
            ("assistant", "Shared answer", None, None),
        ]

        recipient_follow_up = {
            "id": "recipient-follow-up",
            "role": "user",
            "content": "A follow-up after import",
            "reasoningContent": "",
            "toolCalls": [],
            "progressLines": [],
            "files": [],
            "timestamp": int(time.time()),
            "done": True,
        }
        app_mod.save_display_messages(
            formal.id,
            formal_session_id,
            [*first.json()["messages"], recipient_follow_up],
        )

        retried = formal_client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": created.token},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["created"] is False
        assert retried.json()["session"]["id"] == formal_session_id
        assert [
            message["content"] for message in retried.json()["messages"]
        ] == ["Shared question", "Shared answer", "A follow-up after import"]
        persisted_messages = app_mod.get_display_messages(formal.id, formal_session_id)
        assert [message["content"] for message in persisted_messages] == [
            "Shared question",
            "Shared answer",
            "A follow-up after import",
        ]
        assert [
            message["id"]
            for message in persisted_messages
            if message["id"] == "recipient-follow-up"
        ] == ["recipient-follow-up"]
        assert [username for username, _ in import_calls].count("formal") == 1

        temporary = _temporary_signin(quick_client, app_mod)
        runtime = quick_client.post("/api/runtime/start")
        assert runtime.status_code == 200, runtime.text
        quick = quick_client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": created.token},
        )
        assert quick.status_code == 200, quick.text
        assert quick.json()["created"] is True
        assert quick.json()["session"]["title"] == "Source conversation (shared)"
        assert any(username.startswith("temp_") for username, _ in import_calls)

        formal_claim = share_store.claim_chat_share_import(
            token=created.token,
            recipient_user_id=f"formal:{formal.id}",
            db_path=share_db_path,
        )
        quick_claim = share_store.claim_chat_share_import(
            token=created.token,
            recipient_user_id=f"temporary:{temporary['id']}",
            db_path=share_db_path,
        )
        assert formal_claim.status == share_store.CLAIM_STATUS_COMPLETED
        assert quick_claim.status == share_store.CLAIM_STATUS_COMPLETED
    finally:
        formal_client.close()
        quick_client.close()


def test_quick_start_cannot_create_shares(tmp_path, monkeypatch) -> None:
    app_mod, _, _, _, _, _ = _load_app(tmp_path, monkeypatch)
    client = TestClient(app_mod.app)
    try:
        _temporary_signin(client, app_mod)
        response = client.post(
            "/api/sessions/source-1/shares",
            headers=SHARE_HEADERS,
            json={},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "formal_account_required"
    finally:
        client.close()


def test_import_recovers_same_destination_after_receipt_write_failure(
    tmp_path, monkeypatch
) -> None:
    app_mod, auth_db, share_store, targets, _, share_db_path = _load_app(
        tmp_path, monkeypatch
    )
    owner = _add_formal_user(auth_db, targets, tmp_path, "recovery-owner")
    _add_formal_user(auth_db, targets, tmp_path, "recovery-recipient")
    created = share_store.create_chat_share(
        owner_user_id=owner.id,
        source_session_id="recovery-source",
        messages=SHARED_MESSAGES,
        db_path=share_db_path,
    )
    real_complete = app_mod.complete_chat_share_import
    complete_attempts = 0

    def fail_first_completion(**kwargs):
        nonlocal complete_attempts
        complete_attempts += 1
        if complete_attempts == 1:
            raise sqlite3.OperationalError("simulated receipt write failure")
        return real_complete(**kwargs)

    monkeypatch.setattr(
        app_mod,
        "complete_chat_share_import",
        fail_first_completion,
    )
    client = TestClient(app_mod.app)
    try:
        _signin(client, app_mod, "recovery-recipient")
        failed = client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": created.token},
        )
        assert failed.status_code == 503

        with sqlite3.connect(str(share_db_path)) as conn:
            failed_receipt = conn.execute(
                "select imported_session_id, status from chat_share_imports"
            ).fetchone()
        assert failed_receipt is not None
        imported_session_id, receipt_status = failed_receipt
        assert receipt_status == "failed"

        recovered = client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": created.token},
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["created"] is True
        assert recovered.json()["session"]["id"] == imported_session_id
        assert [
            message["content"] for message in recovered.json()["messages"]
        ] == ["Shared question", "Shared answer"]

        with sqlite3.connect(str(targets["recovery-recipient"].state_db_path)) as conn:
            matching_sessions = conn.execute(
                "select count(*) from sessions where id = ?",
                (imported_session_id,),
            ).fetchone()[0]
        assert matching_sessions == 1

        replayed = client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": created.token},
        )
        assert replayed.status_code == 200
        assert replayed.json()["created"] is False
        assert replayed.json()["session"]["id"] == imported_session_id
    finally:
        client.close()


def test_import_api_maps_uniform_and_retryable_store_states(
    tmp_path, monkeypatch
) -> None:
    app_mod, auth_db, share_store, targets, _, share_db_path = _load_app(
        tmp_path, monkeypatch
    )
    recipient = _add_formal_user(auth_db, targets, tmp_path, "recipient")
    client = TestClient(app_mod.app)
    _signin(client, app_mod, "recipient")
    valid_token = "A" * 43
    try:
        expired = share_store.create_chat_share(
            owner_user_id="expired-owner",
            source_session_id="expired-source",
            messages=SHARED_MESSAGES,
            now=1,
            db_path=share_db_path,
        )
        revoked = share_store.create_chat_share(
            owner_user_id=recipient.id,
            source_session_id="revoked-source",
            messages=SHARED_MESSAGES,
            now=int(time.time()),
            db_path=share_db_path,
        )
        assert share_store.invalidate_source_session_shares(
            recipient.id,
            "revoked-source",
            db_path=share_db_path,
        ) == 1
        unavailable_responses = [
            client.post(
                "/api/chat-shares/import",
                headers=SHARE_HEADERS,
                json={"token": token},
            )
            for token in ("invalid", valid_token, expired.token, revoked.token)
        ]
        assert {response.status_code for response in unavailable_responses} == {404}
        assert len({str(response.json()["detail"]) for response in unavailable_responses}) == 1

        states = (
            (share_store.CLAIM_STATUS_RECIPIENT_LIMIT, 410, None),
            (share_store.CLAIM_STATUS_TARGET_DELETED, 410, None),
            (share_store.CLAIM_STATUS_RATE_LIMITED, 429, "17"),
            (share_store.CLAIM_STATUS_IN_PROGRESS, 202, "2"),
        )
        for claim_status, expected_status, retry_after in states:
            monkeypatch.setattr(
                app_mod,
                "claim_chat_share_import",
                lambda **kwargs: share_store.ChatShareImportClaim(
                    status=claim_status,
                    retry_after=17,
                ),
            )
            response = client.post(
                "/api/chat-shares/import",
                headers=SHARE_HEADERS,
                json={"token": valid_token},
            )
            assert response.status_code == expected_status, response.text
            if retry_after is not None:
                assert response.headers["retry-after"] == retry_after
            assert response.headers["cache-control"] == "no-store"

        extra = client.post(
            "/api/chat-shares/import",
            headers=SHARE_HEADERS,
            json={"token": valid_token, "recipient": "attacker"},
        )
        assert extra.status_code == 422
    finally:
        client.close()


def test_snapshot_helper_excludes_private_and_non_visible_content(
    tmp_path, monkeypatch
) -> None:
    app_mod, auth_db, _, targets, _, _ = _load_app(tmp_path, monkeypatch)
    record = _add_formal_user(auth_db, targets, tmp_path, "alice")
    target = targets["alice"]
    user = app_mod.CurrentUser(
        id=record.id,
        email=record.email,
        username=record.username,
        name=record.name,
        role=record.role,
        mapping_username=record.mapping_username,
        target=target,
    )
    context = (
        "logical-1",
        {
            "id": "logical-1",
            "source": "tui",
            "title": f"Analysis {target.home_dir}/private.csv",
            "message_count": 4,
            "started_at": 1,
        },
        "logical-1",
        {
            "id": "logical-1",
            "source": "tui",
            "title": f"Analysis {target.home_dir}/private.csv",
            "message_count": 4,
            "started_at": 1,
        },
        [],
    )
    display = {
        "messages": [
            {
                "role": "user",
                "content": f"Inspect {target.home_dir}/private.csv",
                "files": [{"path": str(target.home_dir / "private.csv")}],
                "done": True,
            },
            {
                "role": "assistant",
                "content": "Visible answer",
                "reasoningContent": "private reasoning",
                "toolCalls": [{"name": "read_file"}],
                "done": True,
            },
            {"role": "tool", "content": "private tool output", "done": True},
            {"role": "assistant", "content": "partial", "done": False},
        ]
    }
    monkeypatch.setattr(
        app_mod,
        "_load_session_context_sync",
        lambda *args, **kwargs: context,
    )
    monkeypatch.setattr(app_mod, "get_live_session_state", lambda *args: None)
    monkeypatch.setattr(app_mod, "get_display_session_meta", lambda *args: display)

    logical_id, title, messages = app_mod._load_chat_share_snapshot_sync(
        user, "logical-1"
    )
    assert logical_id == "logical-1"
    assert title == "Analysis [private path]"
    assert messages == [
        {"role": "user", "content": "Inspect [private path]"},
        {"role": "assistant", "content": "Visible answer"},
    ]
    serialized = str(messages)
    assert str(target.home_dir) not in serialized
    assert "private reasoning" not in serialized
    assert "private tool output" not in serialized

    renamed_context = (
        context[0],
        {**context[1], "title": "Renamed while sharing"},
        context[2],
        {**context[3], "title": "Renamed while sharing"},
        context[4],
    )
    changing_contexts = iter((context, renamed_context))
    monkeypatch.setattr(
        app_mod,
        "_load_session_context_sync",
        lambda *args, **kwargs: next(changing_contexts),
    )
    with pytest.raises(HTTPException) as renamed:
        app_mod._load_chat_share_snapshot_sync(user, "logical-1")
    assert renamed.value.status_code == 409
    assert renamed.value.detail["code"] == "session_changed"

    monkeypatch.setattr(
        app_mod,
        "_load_session_context_sync",
        lambda *args, **kwargs: context,
    )
    monkeypatch.setattr(
        app_mod,
        "get_live_session_state",
        lambda *args: {"status": "running"},
    )
    with pytest.raises(HTTPException) as active:
        app_mod._load_chat_share_snapshot_sync(user, "logical-1")
    assert active.value.status_code == 409
    assert active.value.detail["code"] == "session_active"


def test_session_delete_invalidates_share_state_before_deleting_history(
    tmp_path, monkeypatch
) -> None:
    app_mod, _, _, _, _, _ = _load_app(tmp_path, monkeypatch)
    events: list[object] = []

    class FakeDB:
        def delete_session(self, session_id: str) -> bool:
            events.append(("delete", session_id))
            return True

        def get_session(self, session_id: str):
            return {"id": session_id}

    fake_db = FakeDB()
    monkeypatch.setattr(
        app_mod,
        "_open_session_db",
        lambda target: contextlib.nullcontext(fake_db),
    )
    monkeypatch.setattr(
        app_mod,
        "_resolve_logical_session_context",
        lambda db, session_id: (
            "logical-1",
            {"id": "logical-1", "source": "tui"},
            "logical-1",
            {"id": "logical-1", "source": "tui"},
        ),
    )
    monkeypatch.setattr(
        app_mod,
        "_collect_compression_lineage_session_ids",
        lambda db, session_id: ["logical-1", "tip-1"],
    )
    monkeypatch.setattr(
        app_mod,
        "_invalidate_chat_share_session_lifecycle_sync",
        lambda **kwargs: events.append(("invalidate", kwargs)) or "claim-1",
    )
    monkeypatch.setattr(
        app_mod,
        "_complete_chat_share_session_lifecycle_sync",
        lambda **kwargs: events.append(("complete", kwargs)),
    )
    monkeypatch.setattr(
        app_mod,
        "_heartbeat_chat_share_session_lifecycle_sync",
        lambda **kwargs: events.append(("heartbeat", kwargs)),
    )

    result = app_mod._delete_session_sync(
        SimpleNamespace(),
        "tip-1",
        owner_user_id="owner-1",
        recipient_user_id="formal:owner-1",
    )
    assert result == "logical-1"
    assert events[0][0] == "invalidate"
    assert [event[0] for event in events] == [
        "invalidate",
        "heartbeat",
        "delete",
        "heartbeat",
        "delete",
        "complete",
    ]

    events.clear()
    monkeypatch.setattr(
        app_mod,
        "_complete_chat_share_session_lifecycle_sync",
        lambda **kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("receipt unavailable")
        ),
    )
    assert (
        app_mod._delete_session_sync(
            SimpleNamespace(),
            "tip-1",
            owner_user_id="owner-1",
            recipient_user_id="formal:owner-1",
        )
        == "logical-1"
    )
    assert [event[0] for event in events] == [
        "invalidate",
        "heartbeat",
        "delete",
        "heartbeat",
        "delete",
    ]

    events.clear()
    monkeypatch.setattr(
        app_mod,
        "_invalidate_chat_share_session_lifecycle_sync",
        lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )
    with pytest.raises(HTTPException) as unavailable:
        app_mod._delete_session_sync(
            SimpleNamespace(),
            "tip-1",
            owner_user_id="owner-1",
            recipient_user_id="formal:owner-1",
        )
    assert unavailable.value.status_code == 503
    assert events == []

from __future__ import annotations

import contextlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from interface.test_chat_sharing_api import _add_formal_user, _load_app


@pytest.fixture(autouse=True)
def restore_interface_imports(monkeypatch):
    original = {
        name: module
        for name, module in sys.modules.items()
        if name == "interface" or name.startswith("interface.")
    }
    yield
    # _load_app isolates module globals. Restore package attributes as well as
    # sys.modules so subsequent suites share the same exception/class objects.
    monkeypatch.undo()
    for name in list(sys.modules):
        if name.startswith("interface.") and name not in original:
            sys.modules.pop(name, None)
    sys.modules.update(original)
    for module in original.values():
        for name, value in list(vars(module).items()):
            if (
                isinstance(value, ModuleType)
                and value.__name__.startswith("interface.")
                and value.__name__ not in original
            ):
                delattr(module, name)
    for name, module in list(sys.modules.items()):
        if name.startswith("interface."):
            parent_name, _, child_name = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None:
                setattr(parent, child_name, module)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    app_mod, auth_db, _, targets, _, _ = _load_app(tmp_path, monkeypatch)
    from potato_hermes_lite.session_visibility import SessionDB
    from interface.display_store import save_live_session_state

    owner = _add_formal_user(auth_db, targets, tmp_path, "alice")
    user = SimpleNamespace(id=owner.id, target=targets["alice"], is_temporary=False)
    app_mod.app.dependency_overrides[app_mod.get_current_user] = lambda: user
    db = SessionDB(user.target.state_db_path)
    db.create_session("parent", "tui")
    db.append_message("parent", "user", "public question")
    db.append_message("parent", "assistant", "public answer")
    db.create_session("internal-child", "tui", parent_session_id="parent")
    db.append_message("internal-child", "user", "PRIVATE_DELEGATION_SENTINEL")
    db.append_message("internal-child", "assistant", "PRIVATE_DELEGATION_RESULT")
    db.create_session("public", "tui")
    db.append_message("public", "user", "visible question")
    db.append_message("public", "assistant", "visible answer")
    app_mod.save_display_messages(
        user.id,
        "internal-child",
        [
            {"id": "old-user", "role": "user", "content": "PRIVATE_CACHE_SENTINEL"},
            {
                "id": "old-assistant",
                "role": "assistant",
                "content": "PRIVATE_CACHE_RESULT",
            },
        ],
    )
    save_live_session_state(
        user.id,
        "internal-child",
        status="completed",
        run_id="old-internal-run",
    )
    client = TestClient(app_mod.app)
    try:
        yield app_mod, client, user, db
    finally:
        client.close()
        db.close()
        app_mod.app.dependency_overrides.clear()


@pytest.mark.parametrize("cleanup", ["none", "delete", "archive", "delete-child"])
def test_internal_transcripts_never_reappear_from_raw_or_cached_history(chat, cleanup):
    app_mod, client, user, db = chat
    if cleanup == "delete":
        assert client.delete("/api/sessions/parent").status_code == 200
    elif cleanup == "archive":
        app_mod._archive_expired_target_sync(user.target, None, cutoff=float("inf"))
    elif cleanup == "delete-child":
        db.delete_session("parent")
        db.delete_session("internal-child")

    listed = client.get("/api/sessions?limit=1")
    assert listed.status_code == 200
    assert all(row["id"] != "internal-child" for row in listed.json()["sessions"])
    assert "PRIVATE_" not in listed.text
    requests = [
        ("GET", "", None),
        ("GET", "/export.md", None),
        ("GET", "/live", None),
        ("PUT", "/display", {"messages": []}),
        ("PUT", "/title", {"title": "should not rename"}),
        ("POST", "/shares", {}),
        ("POST", "/forks", {"fork_cursor": "old-assistant", "request_id": "test-fork"}),
        ("POST", "/turns", {"prompt": "resume", "request_id": "test-turn"}),
        ("POST", "/interrupt", {}),
        ("POST", "/approval", {"choice": "once", "approval_id": "old-approval"}),
        ("DELETE", "", None),
    ]
    for method, suffix, payload in requests:
        response = client.request(
            method,
            f"/api/sessions/internal-child{suffix}",
            json=payload,
            headers={"X-Potato-Request": "1"},
        )
        assert response.status_code == 404, (method, suffix, response.text)
        assert "PRIVATE_" not in response.text

    # The submitted-turn recovery endpoint must not bypass the live guard.
    recovered = client.get("/api/turns/old-internal-run")
    assert recovered.status_code == 404
    assert "PRIVATE_" not in recovered.text


def test_reviewed_orphan_and_proxy_context_are_hidden_without_losing_public_chats(chat):
    app_mod, client, user, db = chat
    from interface.session_db_rpc import execute

    db.create_session("known-orphan", "tui")
    db.append_message("known-orphan", "user", "reviewed internal task")
    db.mark_internal_sessions(["known-orphan"])
    assert execute(
        db,
        "get_logical_session_context",
        {
            "session_id": "known-orphan",
            "include_messages": True,
        },
    ) == {"internal": True}
    assert execute(db, "is_internal_session", {"session_id": "known-orphan"})
    assert "known-orphan" in execute(db, "get_internal_session_ids", {})
    assert client.get("/api/sessions/known-orphan").status_code == 404
    # Prefix resolution is subject to the same visibility rule.
    assert client.get("/api/sessions/known-orph").status_code == 404
    public = client.get("/api/sessions/public")
    assert public.status_code == 200
    assert "visible answer" in public.text
    assert client.get("/api/sessions/public/export.md").status_code == 200
    app_mod.save_display_messages(user.id, "public", public.json()["messages"])
    shared = client.post(
        "/api/sessions/public/shares", json={}, headers={"X-Potato-Request": "1"}
    )
    assert shared.status_code == 200, shared.text


def test_marker_written_during_list_read_blocks_cached_fallback(chat, monkeypatch):
    app_mod, client, user, db = chat
    db.create_session("reviewed-later", "tui")
    app_mod.save_display_messages(
        user.id,
        "reviewed-later",
        [
            {"id": "cached", "role": "user", "content": "PRIVATE_REVIEW_SENTINEL"},
        ],
    )
    original_list = db.list_sessions_rich

    def mark_and_list(*args, **kwargs):
        db.mark_internal_sessions(["reviewed-later"])
        return original_list(*args, **kwargs)

    monkeypatch.setattr(db, "list_sessions_rich", mark_and_list)
    monkeypatch.setattr(
        app_mod, "_open_session_db", lambda target: contextlib.nullcontext(db)
    )
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert "reviewed-later" not in response.text
    assert "PRIVATE_" not in response.text

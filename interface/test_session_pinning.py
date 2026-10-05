from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from types import SimpleNamespace

import pytest

from interface.test_internal_session_visibility import chat, restore_interface_imports  # noqa: F401


def pin(client, session_id, pinned=True):
    response = client.put(f"/api/sessions/{session_id}/pin", json={"pinned": pinned})
    assert response.status_code == 200, response.text
    return response.json()


def ids(client, **params):
    response = client.get("/api/sessions", params=params)
    assert response.status_code == 200, response.text
    return [s["id"] for s in response.json()["sessions"]]


def test_pin_order_idempotence_and_activity_preserved(chat):
    app, client, user, db = chat
    app.save_display_messages(user.id, "parent", [{"id": "m", "role": "user", "content": "question"}])
    before = client.get("/api/sessions/parent").json()["session"]
    before_meta = app.get_display_session_meta(user.id, "parent")
    normal_order = ids(client)
    first = pin(client, "parent")
    second = pin(client, "public")
    assert second["pin_order"] > first["pin_order"] > 0
    assert pin(client, "parent") == first
    assert ids(client)[:2] == ["public", "parent"]
    renamed = client.put("/api/sessions/parent/title", json={"title": "Renamed pinned chat"})
    assert renamed.status_code == 200
    assert renamed.json()["session"]["pin_order"] == first["pin_order"]
    unpinned = pin(client, "parent", False)
    assert unpinned["pin_revision"] > second["pin_revision"]
    assert unpinned["pin_order"] == 0 and not unpinned["pinned"]
    pin(client, "public", False)
    assert ids(client) == normal_order
    after = client.get("/api/sessions/parent").json()["session"]
    assert (after["last_active"], after["started_at"]) == (before["last_active"], before["started_at"])
    assert app.get_display_session_meta(user.id, "parent")["updated_at"] == before_meta["updated_at"]
    assert pin(client, "parent")["pin_order"] > unpinned["pin_revision"]


@pytest.mark.parametrize("status", ["queued", "starting", "running", "awaiting_approval"])
def test_priority_candidates_cross_pages_and_unpin_running(chat, status):
    app, client, user, db = chat
    from interface.display_store import save_live_session_state

    for i in range(135):
        db.create_session(f"history-{i:03d}", "tui")
    with sqlite3.connect(user.target.state_db_path) as conn:
        for i in range(135):
            conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (1000 + i, f"history-{i:03d}"))
    save_live_session_state(user.id, "history-001", status=status, run_id="run")
    pin(client, "history-000")
    pin(client, "history-002")
    assert ids(client, limit=4)[:3] == ["history-002", "history-000", "history-001"]
    seen = []
    offset = 0
    while True:
        page = client.get("/api/sessions", params={"limit": 17, "offset": offset}).json()
        seen.extend(s["id"] for s in page["sessions"])
        offset = page["next_offset"]
        if not page["has_more"]:
            break
    assert len(seen) == len(set(seen)) == 137
    assert set(seen) == {"parent", "public"} | {f"history-{i:03d}" for i in range(135)}
    pin(client, "history-001")
    assert ids(client, limit=1) == ["history-001"]
    pin(client, "history-001", False)
    assert ids(client, limit=3) == ["history-002", "history-000", "history-001"]
    save_live_session_state(user.id, "history-001", status="completed", background_pending=True)
    assert "history-001" not in ids(client, limit=5)


def test_pin_validation_visibility_and_user_isolation(chat, tmp_path):
    app, client, user, db = chat
    from interface.test_chat_sharing_api import _target

    for session_id in ["missing", "internal-child", "internal-ch"]:
        assert client.put(f"/api/sessions/{session_id}/pin", json={"pinned": True}).status_code == 404
    for value in ["true", 1, None]:
        assert client.put("/api/sessions/public/pin", json={"pinned": value}).status_code == 422
    first = pin(client, "public")
    other = SimpleNamespace(id="bob", target=_target(tmp_path, "bob"), is_temporary=False)
    app.app.dependency_overrides[app.get_current_user] = lambda: other
    assert client.put("/api/sessions/public/pin", json={"pinned": True}).status_code == 404
    assert app.get_session_pin_state(other.id, "public")["pinned"] is False
    app.app.dependency_overrides[app.get_current_user] = lambda: user
    assert client.get("/api/sessions/public").json()["session"]["pin_revision"] == first["pin_revision"]
    app.app.dependency_overrides.clear()
    assert client.put("/api/sessions/public/pin", json={"pinned": True}).status_code == 401


def test_cache_only_history_can_be_pinned_but_pin_rows_cannot_create_history(chat):
    app, client, user, db = chat
    app.save_display_messages(user.id, "cached", [{"id": "m", "role": "user", "content": "Saved"}])
    pin(client, "cached")
    assert ids(client, limit=1) == ["cached"]
    app.set_session_pinned(user.id, "nonexistent", True)
    app.set_session_pinned(user.id, "internal-child", True)
    assert ids(client, limit=1) == ["cached"]
    assert "nonexistent" not in ids(client)
    assert "internal-child" not in ids(client)


def test_compression_root_survives_tip_changes_and_cleanup(chat):
    app, client, user, db = chat
    first = pin(client, "public")
    db.end_session("public", "compression")
    db.create_session("continuation", "tui", parent_session_id="public")
    assert pin(client, "continuation") == first
    assert client.get("/api/sessions/continuation").json()["session"]["pin_order"] == first["pin_order"]
    assert ids(client, limit=1) == ["public"]
    assert client.delete("/api/sessions/continuation").status_code == 200
    assert "public" not in app.list_session_pin_states(user.id)
    pin(client, "parent")
    app._archive_expired_target_sync(user.target, user, cutoff=float("inf"))
    assert "parent" not in app.list_session_pin_states(user.id)


def test_pin_store_migration_concurrency_and_user_cleanup(tmp_path):
    from interface import auth_db, display_store

    path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(path)
    user = auth_db.upsert_user(username="alice", email="a@example.com", password="Password123!", mapping_username="alice", name="Alice", db_path=path)
    display_store.ensure_display_store(path)
    display_store.ensure_display_store(path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda i: display_store.set_session_pinned(user.id, f"s{i}", True, db_path=path), range(20)))
    assert len({r["pin_order"] for r in results}) == 20
    with ThreadPoolExecutor(max_workers=8) as executor:
        repeated = list(executor.map(lambda _: display_store.set_session_pinned(user.id, "same", True, db_path=path), range(20)))
    assert len({r["pin_revision"] for r in repeated}) == 1
    state = display_store.set_session_pinned(user.id, "same", False, db_path=path)
    assert not state["pinned"] and state["pin_revision"] > repeated[0]["pin_revision"]
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user.id,))
    assert display_store.list_session_pin_states(user.id, db_path=path) == {}
    display_store.set_session_pinned("temporary", "session", True, db_path=path)
    assert display_store.delete_display_user_data("temporary", db_path=path)["session_pins"] == 1


def test_same_second_history_has_stable_pagination(chat):
    app, client, user, db = chat
    # Old rows deliberately have lexically larger IDs than newer rows.
    for i in range(140):
        db.create_session(f"same-second-{139-i:03d}", "tui")
    with sqlite3.connect(user.target.state_db_path) as conn:
        for i in range(140):
            conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (1000 + i / 1000, f"same-second-{139-i:03d}"))
    seen = []
    for offset in range(0, 150, 17):
        seen.extend(ids(client, limit=17, offset=offset))
    expected = ["public", "parent"] + [f"same-second-{i:03d}" for i in reversed(range(140))]
    assert seen == expected

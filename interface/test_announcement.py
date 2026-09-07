from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interface import admin_api, announcement_api, announcement_store as store, auth_db


@pytest.fixture
def api(tmp_path, monkeypatch):
    db_path = tmp_path / "interface.db"
    monkeypatch.setattr(auth_db, "DEFAULT_AUTH_DB_PATH", db_path)
    monkeypatch.setattr(admin_api, "ADMIN_AUTH_DB_PATH", db_path)
    monkeypatch.setattr(admin_api, "ADMIN_COOKIE_SECURE", False)
    monkeypatch.setattr(admin_api, "ADMIN_SESSION_SECRET", "announcement-test-session-secret-32")
    user = auth_db.upsert_user(
        username="announcer", email="announcer@example.com", password="Password1!",
        mapping_username="announcer", db_path=db_path,
    )
    user = auth_db.set_user_role(user.username, "admin", db_path=db_path)
    app = FastAPI()
    app.include_router(announcement_api.router)
    client = TestClient(app)
    client.cookies.set(admin_api.ADMIN_COOKIE_NAME, admin_api._create_admin_token(user), path="/admin")
    yield client, db_path
    client.close()


def test_store_lifecycle_boundaries_and_replacement(tmp_path):
    db_path = tmp_path / "interface.db"
    assert store.get_announcement(db_path=db_path) is None
    first = store.save_announcement(message=" first ", now=100, db_path=db_path)
    assert first.message == "first"
    assert first.starts_at == 100
    assert first.status(100) == "active"
    assert first.status(10**9) == "active"

    scheduled = store.save_announcement(
        message="scheduled", starts_at=200, ends_at=300, now=150, db_path=db_path,
    )
    assert scheduled.id != first.id
    assert scheduled.status(199.999) == "scheduled"
    assert scheduled.status(200) == "active"
    assert scheduled.status(299.999) == "active"
    assert scheduled.status(300) == "ended"
    assert store.get_announcement(db_path=db_path) == scheduled
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from site_announcement").fetchone()[0] == 1


def test_edit_withdraw_republish_and_stale_edits(tmp_path):
    db_path = tmp_path / "interface.db"
    record = store.save_announcement(message="first", now=100, db_path=db_path)
    edited = store.save_announcement(
        message="edited", starts_at=record.starts_at, ends_at=400,
        existing_id=record.id, now=200, db_path=db_path,
    )
    assert edited.id == record.id
    assert edited.updated_at == 200
    withdrawn = store.withdraw_announcement(record.id, now=250, db_path=db_path)
    assert withdrawn.status(300) == "withdrawn"
    edited = store.save_announcement(
        message="saved while withdrawn", existing_id=record.id, now=300, db_path=db_path,
    )
    assert edited.withdrawn
    republished = store.save_announcement(message=edited.message, now=301, db_path=db_path)
    assert republished.id != record.id
    assert republished.status(301) == "active"
    for action in (
        lambda: store.save_announcement(message="stale", existing_id=record.id, db_path=db_path),
        lambda: store.withdraw_announcement(record.id, db_path=db_path),
    ):
        with pytest.raises(LookupError):
            action()
    assert store.get_announcement(db_path=db_path) == republished


def test_restart_and_existing_database_migration(tmp_path, monkeypatch):
    db_path = tmp_path / "interface.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table unrelated_data (value text)")
        conn.execute("insert into unrelated_data values ('preserved')")
    record = store.save_announcement(message="persistent", now=100, db_path=db_path)
    monkeypatch.setattr(auth_db, "_AUTH_DB_IDENTITIES", {})
    reloaded = importlib.reload(store)
    assert reloaded.get_announcement(db_path=db_path).admin_payload(200) == record.admin_payload(200)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select value from unrelated_data").fetchone()[0] == "preserved"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "insert into site_announcement values (2, 'other', 'text', 100, null, 0, 100)"
            )


@pytest.mark.parametrize("content", [
    {"message": ""}, {"message": " \n\t "}, {"message": "x" * 501},
    {"message": "null\x00text"}, {"message": None}, {"message": 1},
    {"message": "ok", "starts_at": "bad"},
    {"message": "ok", "starts_at": "2026-09-06T08:00:00"},
    {"message": "ok", "starts_at": 100},
    {"message": "ok", "starts_at": "2026-09-06T08:00:00+08:00", "ends_at": "2026-09-06T00:00:00Z"},
    {"message": "ok", "starts_at": "2026-09-06T08:00:00+08:00", "ends_at": "2026-09-05T00:00:00Z"},
    {"message": "ok", "ends_at": "2020-01-01T00:00:00Z"},
])
def test_invalid_publish_does_not_replace_record(api, content):
    client, db_path = api
    first = store.save_announcement(message="valid", db_path=db_path)
    response = client.post("/admin/api/announcement/publish", json=content)
    assert response.status_code == 422, response.text
    assert store.get_announcement(db_path=db_path) == first


def test_public_api_and_admin_actions(api, monkeypatch):
    client, _ = api
    assert client.get("/api/announcement").json()["announcement"] is None
    first_response = client.post("/admin/api/announcement/publish", json={"message": "<b>plain text</b>"})
    assert first_response.status_code == 200
    first = first_response.json()["announcement"]
    assert first["status"] == "active"
    public = client.get("/api/announcement")
    assert public.json()["announcement"]["message"] == first["message"]
    assert "withdrawn" not in public.json()["announcement"]
    assert "updated_at" not in public.json()["announcement"]
    assert public.headers["cache-control"] == "no-store"
    assert public.headers["pragma"] == "no-cache"
    assert public.json()["server_time"].endswith("Z")

    content = {"id": first["id"], "message": "edited", "starts_at": first["starts_at"]}
    assert client.put("/admin/api/announcement", json=content).json()["announcement"]["id"] == first["id"]
    assert client.post("/admin/api/announcement/withdraw", json={"id": first["id"]}).status_code == 200
    assert client.get("/api/announcement").json()["announcement"] is None
    assert client.get("/admin/api/announcement").json()["announcement"]["status"] == "withdrawn"
    next_record = client.post("/admin/api/announcement/publish", json={"message": "new"}).json()["announcement"]
    assert next_record["id"] != first["id"]
    assert client.put("/admin/api/announcement", json=content).status_code == 409
    assert client.post("/admin/api/announcement/withdraw", json={"id": first["id"]}).status_code == 409

    scheduled = client.post("/admin/api/announcement/publish", json={
        "message": "future", "starts_at": "2099-01-01T08:00:00+08:00", "ends_at": "2099-01-01T09:00:00+08:00",
    }).json()["announcement"]
    assert scheduled["starts_at"] == "2099-01-01T00:00:00Z"
    assert scheduled["status"] == "scheduled"
    assert client.get("/api/announcement").json()["announcement"] is None
    start = 4070908800
    for now, visible in ((start - .001, False), (start, True), (start + 3599.999, True), (start + 3600, False)):
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(announcement_api.time, "time", lambda: now)
            assert bool(client.get("/api/announcement").json()["announcement"]) is visible


@pytest.mark.parametrize("method,path,content", [
    ("GET", "/admin/api/announcement", None),
    ("POST", "/admin/api/announcement/publish", {"message": "test"}),
    ("PUT", "/admin/api/announcement", {"id": "test", "message": "test"}),
    ("POST", "/admin/api/announcement/withdraw", {"id": "test"}),
])
def test_admin_required_even_with_regular_user_cookie(api, method, path, content):
    client, db_path = api
    client.cookies.clear()
    assert client.get("/api/announcement").status_code == 200
    assert client.request(method, path, json=content).status_code == 401
    ordinary = auth_db.upsert_user(
        username="ordinary", email="ordinary@example.com", password="Password1!",
        mapping_username="ordinary", db_path=db_path,
    )
    client.cookies.set(admin_api.ADMIN_COOKIE_NAME, admin_api._create_admin_token(ordinary), path="/admin")
    assert client.request(method, path, json=content).status_code == 401


@pytest.mark.parametrize("headers", [{"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}])
def test_cross_site_writes_rejected(api, headers):
    client, db_path = api
    record = store.save_announcement(message="untouched", db_path=db_path)
    for method, path, content in (
        ("POST", "/publish", {"message": "bad"}),
        ("PUT", "", {"id": record.id, "message": "bad"}),
        ("POST", "/withdraw", {"id": record.id}),
    ):
        assert client.request(method, "/admin/api/announcement" + path, json=content, headers=headers).status_code == 403
    assert store.get_announcement(db_path=db_path) == record


def test_polling_does_not_resolve_user_or_refresh_activity(api, monkeypatch):
    app_module = importlib.import_module("interface.app")

    def forbidden(*args, **kwargs):
        pytest.fail("Announcement polling touched authenticated activity")

    monkeypatch.setattr(app_module, "_resolve_current_user", forbidden)
    monkeypatch.setattr(app_module, "mark_foreground_activity", forbidden)
    with_client = TestClient(app_module.app)
    try:
        with_client.cookies.set("potato_interface_token", "authenticated-cookie")
        for path in ("/api/announcement", "/api/announcement/"):
            response = with_client.get(path)
            assert response.status_code == 200
            assert "set-cookie" not in response.headers
    finally:
        with_client.close()


def test_page_coverage_excludes_versioned_legal_document():
    static = Path(__file__).parent / "static"
    pages = list(static.glob("*/index.html")) + [static / "lite/high-resolution-required.html"]
    assert len(pages) == 10
    for page in pages:
        html = page.read_text()
        assert html.count('/static/shared/announcement.js?') == 1, page
        assert html.count('/static/shared/announcement.css?') == 1, page
    for page in (static / "legal").glob("*.html"):
        assert "announcement" not in page.read_text()

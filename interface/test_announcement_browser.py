"""Run with POTATO_ANNOUNCEMENT_BROWSER_TESTS=1 and Playwright installed.

POTATO_ANNOUNCEMENT_SCREENSHOTS selects a persistent screenshot directory.
POTATO_PLAYWRIGHT_EXECUTABLE optionally selects an existing Chromium binary.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from interface import admin_api, announcement_api, announcement_store, auth_db


pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_ANNOUNCEMENT_BROWSER_TESTS") != "1",
    reason="Opt-in browser suite; requires Playwright and Chromium",
)
STATIC = Path(__file__).parent / "static"
LONG_MESSAGE = ("系统维护通知：请及时保存当前工作。服务将在维护完成后恢复。\n" * 20)[:500].strip()


def preview_app() -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.include_router(admin_api.router)
    app.include_router(announcement_api.router)
    pages = {
        "": "lite/index.html", "lite": "lite/index.html", "about": "about/index.html",
        "genes": "genes/index.html", "genomes": "genomes/index.html",
        "genome-browser": "genome_browser/index.html", "spatial": "spatial/index.html",
        "wgcna": "wgcna/index.html", "bulk-rnaseq": "bulk_rnaseq/index.html",
        "favicon.ico": "favicon.png",
    }

    @app.get("/{page:path}")
    def public_page(page: str):
        if page not in pages:
            raise HTTPException(404)
        return FileResponse(STATIC / pages[page])

    return app


@pytest.fixture
def site(tmp_path, monkeypatch):
    import uvicorn

    db = tmp_path / "interface.db"
    monkeypatch.setattr(auth_db, "DEFAULT_AUTH_DB_PATH", db)
    monkeypatch.setattr(admin_api, "ADMIN_AUTH_DB_PATH", db)
    monkeypatch.setattr(admin_api, "ADMIN_COOKIE_SECURE", False)
    user = auth_db.upsert_user(
        username="preview", email="preview@example.com", password="Preview123!",
        mapping_username="preview", db_path=db,
    )
    user = auth_db.set_user_role(user.username, "admin", db_path=db)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(preview_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    yield url, db, admin_api._create_admin_token(user)
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
    assert not thread.is_alive()


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch(
            executable_path=os.getenv("POTATO_PLAYWRIGHT_EXECUTABLE") or None,
            args=["--no-sandbox"],
        )
        yield instance
        instance.close()


def mock_tool_apis(page, *, workspace=False):
    user = {"id": "preview", "name": "Preview", "username": "preview", "role": "user"}

    def handle(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/admin/api/announcement") or path == "/api/announcement" or path.startswith("/admin/api/auth/"):
            route.continue_()
            return
        responses = {
            "/api/auth/session": {"authenticated": workspace, "user": user if workspace else None},
            "/api/runtime/start": {"user": user},
            "/api/models": {"models": [], "active_model_id": ""},
            "/api/sessions": {"sessions": []},
            "/api/files/tree": {"entries": [], "files": []},
            "/api/files/config": {"root_label": "Files"},
            "/api/daily-updates": {"items": [], "has_more": False},
        }
        route.fulfill(json=responses.get(path, {}))

    page.route("**/api/**", handle)


def refresh(page):
    page.evaluate("window.dispatchEvent(new Event('online'))")


def screenshot(page, name, tmp_path):
    directory = Path(os.getenv("POTATO_ANNOUNCEMENT_SCREENSHOTS", str(tmp_path / "screenshots")))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / f"{name}.png"), full_page=True)


def test_dismissal_survives_reload_navigation_tabs_and_republication(site, browser):
    from playwright.sync_api import expect

    url, db, _ = site
    record = announcement_store.save_announcement(message="<img src=x onerror=alert(1)> literal", db_path=db)
    with browser.new_context() as context:
        first, second = context.new_page(), context.new_page()
        for page in (first, second):
            mock_tool_apis(page)
            page.goto(url + "/about")
            expect(page.locator(".site-announcement-message")).to_have_text(record.message)
            assert page.locator("#site-announcement img").count() == 0
        first.locator(".site-announcement-close").focus()
        first.keyboard.press("Enter")
        expect(first.locator("#site-announcement")).to_be_hidden()
        expect(second.locator("#site-announcement")).to_be_hidden()
        first.reload()
        expect(first.locator("#site-announcement")).to_be_hidden()
        first.goto(url + "/genes")
        expect(first.locator("#site-announcement")).to_be_hidden()
        first.close()
        reopened = context.new_page()
        mock_tool_apis(reopened)
        reopened.goto(url + "/")
        expect(reopened.locator("#site-announcement")).to_be_hidden()
        announcement_store.save_announcement(message="edited", existing_id=record.id, db_path=db)
        refresh(second)
        expect(second.locator("#site-announcement")).to_be_hidden()
        next_record = announcement_store.save_announcement(message="new publication", db_path=db)
        refresh(second)
        expect(second.locator(".site-announcement-message")).to_have_text(next_record.message)
        expect(second.locator("#site-announcement")).to_be_visible()


def test_failure_expiry_recovery_and_storage_denied(site, browser):
    from playwright.sync_api import expect

    url, db, _ = site
    record = announcement_store.save_announcement(message="expires", ends_at=time.time() + 3, db_path=db)
    with browser.new_context() as context:
        page = context.new_page()
        mock_tool_apis(page)
        page.goto(url + "/about")
        expect(page.locator("#site-announcement")).to_be_visible()
        page.route("**/api/announcement", lambda route: route.abort())
        refresh(page)
        expect(page.locator("#site-announcement")).to_be_visible()
        expect(page.locator("#site-announcement")).to_be_hidden(timeout=6000)
        page.unroute("**/api/announcement")
        record = announcement_store.save_announcement(message="recovered", db_path=db)
        refresh(page)
        expect(page.locator(".site-announcement-message")).to_have_text(record.message)
        announcement_store.withdraw_announcement(record.id, db_path=db)
        refresh(page)
        expect(page.locator("#site-announcement")).to_be_hidden()

    with browser.new_context() as context:
        page = context.new_page()
        page.add_init_script("Object.defineProperty(window, 'localStorage', { get() { throw new Error('Storage denied'); } });")
        mock_tool_apis(page)
        announcement_store.save_announcement(message="storage unavailable", db_path=db)
        page.goto(url + "/about")
        expect(page.locator("#site-announcement")).to_be_visible()
        page.locator(".site-announcement-close").click()
        refresh(page)
        expect(page.locator("#site-announcement")).to_be_hidden()
        announcement_store.save_announcement(message="new with no storage", db_path=db)
        refresh(page)
        expect(page.locator("#site-announcement")).to_be_visible()


def test_polling_visibility_restore_deduplication_and_server_clock(site, browser):
    from playwright.sync_api import expect

    url, _, _ = site
    with browser.new_context() as context:
        page = context.new_page()
        mock_tool_apis(page)
        page.clock.install()
        requests = []
        payload = {
            "server_time": "2030-01-01T00:00:00Z",
            "announcement": {"id": "clock", "message": "server time", "ends_at": "2030-01-01T00:00:05Z"},
        }

        def query(route):
            requests.append(route)
            route.fulfill(json=payload)

        page.route("**/api/announcement", query)
        page.goto(url + "/about")
        expect(page.locator("#site-announcement")).to_be_visible()
        page.clock.run_for(5001)
        expect(page.locator("#site-announcement")).to_be_hidden()
        count = len(requests)
        page.evaluate("Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })")
        page.clock.run_for(60000)
        assert len(requests) == count
        page.evaluate("Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator("#site-announcement")).to_be_visible()
        assert len(requests) == count + 1
        with page.expect_response("**/api/announcement"):
            page.clock.run_for(30000)
        assert len(requests) > count + 1
        held = []
        page.route("**/api/announcement", lambda route: held.append(route))
        page.evaluate("for (let i = 0; i < 5; i++) { window.dispatchEvent(new Event('online')); window.dispatchEvent(new Event('pageshow')); }")
        page.wait_for_timeout(100)
        assert len(held) == 1
        page.clock.run_for(6000)
        held[0].fulfill(json=payload)
        expect(page.locator("#site-announcement")).to_be_hidden()


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844), (320, 640)])
def test_long_banner_workspace_drawers_spatial_and_admin_layout(site, browser, tmp_path, width, height):
    from playwright.sync_api import expect

    url, db, token = site
    announcement_store.save_announcement(message=LONG_MESSAGE, db_path=db)
    with browser.new_context(viewport={"width": width, "height": height}, timezone_id="America/Los_Angeles") as context:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        mock_tool_apis(page, workspace=True)
        page.goto(url + "/")
        expect(page.locator("#workspace-view")).to_be_visible()
        expect(page.locator("#composer-form")).to_be_visible()
        banner = page.locator("#site-announcement").bounding_box()
        workspace = page.locator("#workspace-view").bounding_box()
        composer = page.locator("#composer-form").bounding_box()
        assert workspace["y"] >= banner["height"]
        assert composer["y"] >= banner["height"]
        assert composer["y"] + composer["height"] <= height + 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot(page, f"workspace-{width}", tmp_path)
        if width < 1180:
            for button, panel, name in (("#mobile-chats-button", ".sidebar", "chats"), ("#mobile-files-button", ".files-panel", "files")):
                page.locator(button).click()
                drawer = page.locator(f"#workspace-view {panel}")
                expect(drawer).to_have_css("transform", "none")
                box = drawer.bounding_box()
                assert box["y"] >= banner["height"]
                assert box["y"] + box["height"] <= height + 1
                assert box["x"] >= 0 and box["x"] + box["width"] <= width
                screenshot(page, f"drawer-{name}-{width}", tmp_path)
                page.keyboard.press("Escape")
                drawer.evaluate("element => Promise.all(element.getAnimations().map(animation => animation.finished))")
        page.locator(".site-announcement-close").click()
        expect(page.locator("#site-announcement")).to_be_hidden()
        assert page.locator("#workspace-view").bounding_box()["height"] == height
        announcement_store.save_announcement(message=LONG_MESSAGE, db_path=db)
        page.goto(url + "/spatial")
        expect(page.locator("#site-announcement")).to_be_visible()
        spatial = page.locator(".spatial-app").bounding_box()
        assert spatial["y"] >= page.locator("#site-announcement").bounding_box()["height"]
        if width > 860:
            assert spatial["y"] + spatial["height"] <= height + 1
        assert page.locator("#plot").bounding_box()["width"] > 0
        page.locator("#zoomIn").click()
        screenshot(page, f"spatial-{width}", tmp_path)
        context.add_cookies([{"name": admin_api.ADMIN_COOKIE_NAME, "value": token, "url": url + "/admin"}])
        page.goto(url + "/admin")
        expect(page.locator("#announcement-message")).to_have_value(LONG_MESSAGE)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        for element in ("#announcement-message", "#announcement-start", "#announcement-end", "#announcement-publish"):
            box = page.locator(element).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width
        screenshot(page, f"admin-{width}", tmp_path)
        assert not errors
        home = context.new_page()
        mock_tool_apis(home)
        home.goto(url + "/")
        expect(home.locator("#site-announcement")).to_be_visible()
        expect(home.locator("#login-view")).to_be_visible()
        screenshot(home, f"home-{width}", tmp_path)


def test_admin_beijing_schedule_edit_republish_withdraw_and_signout(site, browser):
    from playwright.sync_api import expect

    url, db, token = site
    with browser.new_context(timezone_id="America/Los_Angeles") as context:
        context.add_cookies([{"name": admin_api.ADMIN_COOKIE_NAME, "value": token, "url": url + "/admin"}])
        page = context.new_page()
        mock_tool_apis(page)
        page.goto(url + "/admin")
        expect(page.locator("#announcement-state")).to_have_text("未发布")
        page.locator("#announcement-message").fill("scheduled text")
        page.locator('input[value="scheduled"]').check()
        page.locator("#announcement-start").fill("2099-01-01T08:00")
        page.locator("#announcement-end").fill("2099-01-01T09:00")
        page.locator("#announcement-publish").click()
        expect(page.locator("#announcement-state")).to_have_text("待生效")
        record = announcement_store.get_announcement(db_path=db)
        assert record.public_payload()["starts_at"] == "2099-01-01T00:00:00Z"
        page.locator("#announcement-message").fill("edited text")
        page.locator("#announcement-save").click()
        expect(page.locator("#announcement-result")).to_have_text("修改已保存。")
        assert announcement_store.get_announcement(db_path=db).id == record.id
        page.locator('input[value="immediate"]').check()
        page.locator("#announcement-end").fill("")
        page.locator("#announcement-save").click()
        expect(page.locator("#announcement-state")).to_have_text("展示中")
        assert announcement_store.get_announcement(db_path=db).id == record.id
        page.locator("#announcement-publish").click()
        expect(page.locator("#announcement-state")).to_have_text("展示中")
        expect(page.locator("#site-announcement")).to_be_visible()
        assert announcement_store.get_announcement(db_path=db).id != record.id
        page.locator(".site-announcement-close").click()
        page.locator("#announcement-message").fill("saved without reminder")
        page.locator("#announcement-save").click()
        expect(page.locator("#announcement-result")).to_have_text("修改已保存。")
        expect(page.locator("#site-announcement")).to_be_hidden()
        page.locator("#announcement-publish").click()
        expect(page.locator("#site-announcement")).to_be_visible()
        page.locator("#announcement-withdraw").click()
        expect(page.locator("#announcement-state")).to_have_text("已撤下")
        expect(page.locator("#site-announcement")).to_be_hidden()
        page.locator("#announcement-publish").click()
        expect(page.locator("#site-announcement")).to_be_visible()
        page.locator("#signout").click()
        expect(page.locator("#login-view")).to_be_visible()
        expect(page.locator("#site-announcement")).to_be_visible()

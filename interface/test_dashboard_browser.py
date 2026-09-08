"""Opt-in: POTATO_DASHBOARD_BROWSER_TESTS=1, with Playwright and Chromium.

POTATO_DASHBOARD_SCREENSHOTS selects a persistent screenshot directory.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from interface import admin_api, dashboard_api


pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_DASHBOARD_BROWSER_TESTS") != "1",
    reason="Opt-in browser suite; requires Playwright and Chromium",
)
STATIC = Path(__file__).parent / "static"


def sample_usage():
    days = []
    for index in range(30):
        base = 17000 + ((index * 17) % 31) * 6200 + index * 3900
        tokens = dict(zip(dashboard_api.TOKEN_FIELDS, (base, base // 4, base * 3, base // 8)))
        days.append({"date": (date(2026, 8, 8) + timedelta(days=index)).isoformat(),
                     **tokens, "total_tokens": sum(tokens.values())})
    return {"status": "available", "time_zone": "Asia/Shanghai", "start_date": "2026-08-08",
            "through_date": "2026-09-06", "days": days}


def preview_app():
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.include_router(dashboard_api.router)
    app.include_router(admin_api.router)

    class PreviewCache:
        async def usage(self):
            return sample_usage()

    app.state.dashboard_cache = PreviewCache()

    @app.get("/api/announcement")
    def announcement():
        return {"announcement": None, "server_time": "2026-09-07T00:00:00Z"}

    @app.get("/{page:path}")
    def public_page(page: str):
        pages = {"lite": "lite", "about": "about", "genes": "genes", "genomes": "genomes",
                 "genomes/browser": "genome_browser", "spatial": "spatial", "wgcna": "wgcna", "bulk-rnaseq": "bulk_rnaseq"}
        if page not in pages:
            raise HTTPException(404)
        return FileResponse(STATIC / pages[page] / "index.html")

    return app


@pytest.fixture
def site():
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(preview_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
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


def _screenshot(page, name, tmp_path):
    directory = Path(os.getenv("POTATO_DASHBOARD_SCREENSHOTS", str(tmp_path / "screenshots")))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / name), full_page=True)


@pytest.mark.parametrize("width,height", [(1440, 1000), (768, 1024), (390, 844), (320, 740)])
def test_dashboard_period_chart_history_and_layout(site, browser, tmp_path, width, height):
    from playwright.sync_api import expect

    context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=width < 700,
                                  has_touch=width < 700, timezone_id="Asia/Shanghai")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    resource_requests = []
    page.on("request", lambda request: resource_requests.append(request.url) if '/api/dashboard/resources' in request.url else None)
    page.goto(site + "/dashboard")
    expect(page.locator('.day-bar')).to_have_count(30)
    expect(page.locator('#agent-history > li:visible')).to_have_count(3)
    expect(page.locator('#omics-history > li:visible')).to_have_count(3)
    total = sum(day["total_tokens"] for day in sample_usage()["days"])
    expect(page.locator('[data-token="total_tokens"]')).to_have_attribute("title", f"{total:,} tokens")
    expect(page.locator('#usage-period')).to_contain_text("Sep 6, 2026")
    expect(page.locator('[data-token]')).to_have_count(4)
    for text in ('PLATFORM OVERVIEW', 'Cache write', 'Public resources', 'DATA & EXPLORATION', 'WORKSPACE & TOOLS', 'Recorded usage from successful model responses.', 'Potato Agent / PotatoOmics', 'About the platform'):
        assert text not in page.locator('main').inner_text()
    heading_sizes = page.evaluate("""() => ['.dashboard-heading h2', '#updates-title', '#omics-title', '#omics-history h5'].map(selector => parseFloat(getComputedStyle(document.querySelector(selector)).fontSize))""")
    assert all(parent > child for parent, child in zip(heading_sizes, heading_sizes[1:]))
    _screenshot(page, f"dashboard-{width}.png", tmp_path)

    page.get_by_label("7 days", exact=True).check()
    expect(page.locator('.day-bar')).to_have_count(7)
    total = sum(day["total_tokens"] for day in sample_usage()["days"][-7:])
    expect(page.locator('[data-token="total_tokens"]')).to_have_attribute("title", f"{total:,} tokens")
    bar = page.locator('.day-bar').first
    if width < 700:
        bar.tap()
    else:
        bar.hover()
    expect(page.locator('#detail-date')).to_have_text("Aug 31, 2026")
    bar.focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator('#detail-date')).to_have_text("Sep 1, 2026")
    page.keyboard.press("End")
    expect(page.locator('#detail-date')).to_have_text("Sep 6, 2026")
    page.keyboard.press("Home")
    expect(page.locator('#detail-date')).to_have_text("Aug 31, 2026")
    day = sample_usage()["days"][-7]
    expect(page.locator('#detail-total')).to_have_text(f'{day["total_tokens"]:,}')
    expect(page.locator('#detail-tokens dd')).to_have_count(3)
    for index, field in enumerate(('input_tokens', 'output_tokens', 'cache_read_tokens')):
        expect(page.locator('#detail-tokens dd').nth(index)).to_have_text(f'{day[field]:,}')

    page_height = page.evaluate('document.documentElement.scrollHeight')
    page.locator('#omics-updates .more-updates').click()
    omics_updates = json.loads((STATIC / 'dashboard/potato-omics-updates.json').read_text())['updates']
    expect(page.locator('#omics-history > li:visible')).to_have_count(len(omics_updates))
    expect(page.locator('#agent-history > li:visible')).to_have_count(3)
    page.locator('#agent-updates .more-updates').click()
    agent_updates = json.loads((STATIC / "lite/update-notes.json").read_text())["updates"]
    expect(page.locator('#agent-history > li:visible')).to_have_count(len(agent_updates))
    assert page.evaluate('document.documentElement.scrollHeight') == page_height
    expect(page.locator('#agent-history')).to_be_focused()
    assert page.locator('#agent-history').evaluate('e => e.scrollHeight > e.clientHeight')
    page.locator('#agent-history').evaluate('e => { e.scrollTop = e.scrollHeight; }')
    assert page.locator('#agent-history').evaluate('e => e.scrollTop > 0')
    assert page.locator('#omics-history').evaluate('e => e.scrollTop === 0')
    assert "b132ae9" not in page.locator('main').inner_text()
    assert not page.evaluate("() => /[\\u4e00-\\u9fff]/.test(document.querySelector('main').innerText)")
    page.locator('#agent-updates .more-updates').click()
    expect(page.locator('#agent-history > li:visible')).to_have_count(3)
    page.get_by_label("30 days", exact=True).check()
    expect(page.locator('.day-bar')).to_have_count(30)

    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(page.locator('#portal-title')).to_have_text('PotatoOmics: An AI-powered multi-omics database for intelligent Q&A and skills-based informatics analyses')
    expect(page.locator('.dashboard-title-row .admin-link')).to_be_visible()
    assert page.locator('.bar-fill').evaluate_all("bars => bars.every(bar => bar.getBoundingClientRect().height > 0)")
    assert page.evaluate("""() => [...document.querySelectorAll('main h2, main h3, main h4, main h5, main dd')].filter(e => e.getClientRects().length).every(e => e.scrollWidth <= e.clientWidth + 1)""")
    if width < 700:
        assert page.locator('#agent-updates').bounding_box()["y"] > page.locator('#omics-updates').bounding_box()["y"]
    else:
        assert page.locator('#agent-updates').bounding_box()["y"] == page.locator('#omics-updates').bounding_box()["y"]
    page.locator('.admin-link').click()
    expect(page).to_have_url(site + "/admin")
    expect(page.locator('#login-form')).to_be_visible()
    assert not errors
    assert not resource_requests
    context.close()


def test_dashboard_failures_and_plain_text_updates_are_independent(site, browser, tmp_path):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": 390, "height": 844}) as context:
        page = context.new_page()
        page.route('**/api/dashboard/usage', lambda route: route.fulfill(status=503, json={"detail": "private error"}))
        page.route('**/static/lite/update-notes.json', lambda route: route.abort())
        page.goto(site + '/dashboard')
        expect(page.locator('[data-token="total_tokens"]')).to_have_text('Unavailable')
        expect(page.locator('#chart-status')).to_have_text('Unavailable')
        expect(page.locator('#omics-history > li:visible')).to_have_count(3)
        expect(page.locator('#agent-updates .updates-status')).to_have_text('Unavailable')
        _screenshot(page, 'dashboard-unavailable-mobile.png', tmp_path)
        page.get_by_label('7 days', exact=True).check()
        expect(page.locator('[data-token="total_tokens"]')).to_have_text('Unavailable')
        assert 'private error' not in page.locator('body').inner_text()
        page.unroute('**/static/lite/update-notes.json')
        page.route('**/static/dashboard/potato-omics-updates.json', lambda route: route.abort())
        page.reload()
        expect(page.locator('#agent-history > li:visible')).to_have_count(3)
        expect(page.locator('#omics-updates .updates-status')).to_have_text('Unavailable')
        page.unroute('**/static/dashboard/potato-omics-updates.json')
        payload = {"updates": [
            {"version": "2026-08-24-001", "date": "August 24, 2026", "title": "Older", "summary": "", "items": []},
            {"version": "2026-08-24-002", "date": "August 24, 2026", "title": "<img src=x onerror=alert(1)>", "summary": "<b>Literal</b>", "items": ["<script>bad</script>"]},
        ]}
        page.route('**/static/dashboard/potato-omics-updates.json', lambda route: route.fulfill(json=payload))
        page.reload()
        expect(page.locator('#omics-history h5').first).to_have_text('<img src=x onerror=alert(1)>')
        assert page.locator('#omics-history img, #omics-history script, #omics-history b').count() == 0


def test_public_navigation_and_shared_entries(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        page = context.new_page()
        for route in ('dashboard', 'about', 'genes', 'genomes', 'genomes/browser', 'bulk-rnaseq', 'wgcna', 'spatial', 'lite'):
            page.goto(site + '/' + route)
            expect(page.locator('.portal-nav a[href="/dashboard"]')).to_have_text('Dashboard')
            assert page.locator('.portal-nav a[href="/dashboard"]').evaluate("e => e.nextElementSibling.getAttribute('href') === '/about'")
        page.goto(site + '/dashboard')
        assert page.locator('#site-announcement').count() == 1
        expect(page.locator('.feedback-trigger')).to_be_visible()
        assert page.locator('.feedback-trigger').evaluate('e => getComputedStyle(e).position') == 'fixed'


def test_empty_usage_announcement_and_feedback(site, browser, tmp_path):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": 390, "height": 844}) as context:
        page = context.new_page()
        empty = sample_usage()
        for day in empty["days"]:
            day.update(dict.fromkeys((*dashboard_api.TOKEN_FIELDS, "total_tokens"), 0))
        page.route('**/api/dashboard/usage', lambda route: route.fulfill(json=empty))
        page.route('**/api/announcement', lambda route: route.fulfill(json={
            "announcement": {"id": "test", "message": "New public datasets are available.", "ends_at": None},
            "server_time": "2026-09-07T00:00:00Z",
        }))
        page.goto(site + '/dashboard')
        expect(page.locator('[data-token="total_tokens"]')).to_have_text('0')
        expect(page.locator('.day-bar.is-zero')).to_have_count(30)
        expect(page.locator('#site-announcement')).to_be_visible()
        banner = page.locator('#site-announcement').bounding_box()
        assert page.locator('.portal-header').bounding_box()["y"] >= banner["y"] + banner["height"]
        _screenshot(page, 'dashboard-empty-announcement-mobile.png', tmp_path)
        page.locator('.feedback-trigger').click()
        expect(page.locator('#feedback-modal')).to_be_visible()
        expect(page.locator('#feedback-message')).to_be_focused()
        page.keyboard.press('Escape')
        expect(page.locator('#feedback-modal')).to_be_hidden()
        expect(page.locator('.feedback-trigger')).to_be_focused()

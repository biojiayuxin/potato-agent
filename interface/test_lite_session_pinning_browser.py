"""Pinning regressions exercise the real Lite page with mocked HTTP only."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from interface.test_agent_examples import mock_api, site  # noqa: F401
from interface.test_chat_workspace_browser import browser, ready, screenshot  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_WORKSPACE_BROWSER_TESTS") != "1", reason="Opt-in Playwright workspace suite",
)


@pytest.fixture
def context(browser):
    with browser.new_context(viewport={"width": 1440, "height": 1000}) as context:
        source = (Path(__file__).parent / "static/lite/app.js").read_text() + """
window.workspaceTest = {state, workspace, busySessionIds, renderChatList, setSessionBusy,
  setChatPinned, refreshSessions, loadMoreSessions, updateSessionSnapshot,
  normalizeSessionSnapshot, sessionPinStatesById, pinningSessionIds,
  showDraftChat, resetWorkspaceState, applyLiveStateToSession, getCurrentChatEntries};
"""
        context.route("**/static/lite/app.js*", lambda route: route.fulfill(body=source, content_type="text/javascript"))
        context.route_web_socket("**/api/tui/ws", lambda ws: ws.close())
        yield context


def history(context, count=65):
    mock_api(context)
    rows = {
        f"chat-{i:03d}": {"id": f"chat-{i:03d}", "title": f"Research conversation {i:03d}",
                         "last_active": 1000 + i, "started_at": 1000 + i, "message_count": 1,
                         "pinned": False, "pin_order": 0, "pin_revision": 0}
        for i in range(count)
    }
    control = {"revision": 0, "fail_pin": False, "fail_list": False, "calls": []}

    def respond(route):
        request = route.request
        url = urlsplit(request.url)
        control["calls"].append((request.method, url.path, url.query))
        if url.path == "/api/sessions":
            if control["fail_list"]:
                route.fulfill(status=503, json={"detail": "History unavailable"})
                return
            query = parse_qs(url.query)
            offset = int(query.get("offset", [0])[0])
            limit = min(200, int(query.get("limit", [50])[0]))
            ordered = sorted(rows.values(), key=lambda s: (s["pinned"], s["pin_order"], s.get("live", {}).get("status") in {"queued", "starting", "running", "awaiting_approval"}, s["last_active"], s["id"]), reverse=True)
            selected = ordered[offset:offset+limit]
            route.fulfill(json={"sessions": selected, "next_offset": offset + len(selected), "has_more": len(ordered) > offset + limit})
            return
        session_id = url.path.split("/")[3]
        row = rows.get(session_id)
        if row is None:
            route.fulfill(status=404, json={"detail": "Session not found"})
            return
        if url.path.endswith("/pin"):
            if control["fail_pin"]:
                route.fulfill(status=503, json={"detail": "Pin unavailable"})
                return
            pinned = request.post_data_json["pinned"]
            if row["pinned"] != pinned:
                control["revision"] += 1
                row.update(pinned=pinned, pin_order=control["revision"] if pinned else 0, pin_revision=control["revision"])
            route.fulfill(json={"ok": True, "session_id": session_id, **{k: row[k] for k in ("pinned", "pin_order", "pin_revision")}})
            return
        route.fulfill(json={"session": row, "session_id": session_id, "live": row.get("live"),
                            "messages": [{"id": "question", "role": "user", "content": "Existing question", "done": True}]})

    context.route("**/api/sessions**", respond)
    return rows, control


def order(page):
    return page.locator("#chat-list .chat-item-shell").evaluate_all("rows => rows.map(row => row.dataset.sessionId)")


def toggle(page, session_id):
    shell = page.locator(f'.chat-item-shell[data-session-id="{session_id}"]')
    shell.hover()
    shell.locator(".chat-pin-button").click()
    page.wait_for_function("!workspaceTest.pinningSessionIds.size")


def test_pin_order_persists_without_switching_chat_or_losing_draft(context, site):
    from playwright.sync_api import expect

    rows, control = history(context)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site + "/chat")
    ready(page)
    page.evaluate("async () => { while (workspaceTest.state.sessionsHasMore) await workspaceTest.loadMoreSessions(); }")
    page.locator("#prompt-input").fill("Keep my unsent message")
    selected = page.evaluate("workspaceTest.state.activeSessionId")
    toggle(page, "chat-000")
    toggle(page, "chat-001")
    assert order(page)[:2] == ["chat-001", "chat-000"]
    assert page.evaluate("workspaceTest.state.activeSessionId") == selected
    expect(page.locator("#prompt-input")).to_have_value("Keep my unsent message")
    page.evaluate("workspaceTest.setSessionBusy('chat-002', true)")
    assert order(page)[:3] == ["chat-001", "chat-000", "chat-002"]
    page.evaluate("workspaceTest.setSessionBusy('chat-000', true)")
    assert order(page)[:2] == ["chat-001", "chat-000"]
    page.evaluate("workspaceTest.showDraftChat({focusPrompt: false})")
    assert order(page)[:3] == ["chat-001", "chat-000", "chat-002"]
    assert page.locator('.chat-item-shell').nth(3).locator('.chat-pin-button').is_hidden()
    page.locator("#chat-list").evaluate("el => { el.scrollTop = 0; }")
    page.locator('.chat-item-shell[data-session-id="chat-001"]').hover()
    screenshot(page, "session-pins-desktop")
    page.reload()
    ready(page)
    assert order(page)[:2] == ["chat-001", "chat-000"]
    assert page.evaluate("workspaceTest.state.activeSessionId") == "chat-001"
    toggle(page, "chat-001")
    assert order(page)[0] == "chat-000"
    assert order(page).index("chat-001") > order(page).index("chat-064")
    assert not errors
    assert not any(path.endswith(("/turns", "/interrupt", "/approval")) for _, path, _ in control["calls"])


def test_old_detail_cannot_repin_chat_that_left_loaded_prefix(context, site):
    rows, control = history(context)
    rows["chat-000"].update(pinned=True, pin_order=1, pin_revision=1)
    control["revision"] = 1
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    page.locator('.chat-item-shell[data-session-id="chat-064"] .chat-item').click()
    page.wait_for_function("workspaceTest.state.activeSessionId === 'chat-064'")
    old = dict(rows["chat-000"])
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = (url, options) => String(url) === '/api/sessions/chat-000'
        ? new Promise(resolve => { window.releaseOldDetail = body => resolve(new Response(JSON.stringify(body), {status: 200})); })
        : original(url, options);
      window.oldDetail = workspaceTest.updateSessionSnapshot('chat-000');
    }""")
    toggle(page, "chat-000")
    assert "chat-000" not in order(page)
    page.evaluate("body => releaseOldDetail(body)", {"session": old, "messages": []})
    page.evaluate("window.oldDetail")
    assert "chat-000" in order(page)
    assert order(page)[0] == "chat-064"
    assert page.evaluate("workspaceTest.state.sessions.find(s => s.id === 'chat-000').pinned") is False


def test_large_prefix_and_refresh_failure_preserve_saved_pin(context, site):
    rows, control = history(context, count=270)
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    page.evaluate("async () => { while (workspaceTest.state.sessionsNextOffset < 250) await workspaceTest.loadMoreSessions(); }")
    assert page.evaluate("workspaceTest.state.sessionsNextOffset") == 250
    toggle(page, "chat-020")
    assert len(order(page)) == 250
    assert order(page)[0] == "chat-020"
    assert any(query == "limit=200&offset=0" for _, path, query in control["calls"] if path == "/api/sessions")
    control["fail_list"] = True
    toggle(page, "chat-021")
    assert rows["chat-021"]["pinned"]
    assert order(page)[0] == "chat-021"
    assert page.locator(".chat-load-more-button").inner_text() == "Refresh sessions"
    control["fail_list"] = False
    page.evaluate("async () => { while (workspaceTest.state.sessionsHasMore) await workspaceTest.loadMoreSessions(); }")
    assert len(order(page)) == 270
    assert len(set(order(page))) == 270
    control["fail_pin"] = True
    toggle(page, "chat-021")
    assert rows["chat-021"]["pinned"]
    assert order(page)[0] == "chat-021"


def test_old_load_more_and_retired_account_responses_are_ignored(context, site):
    rows, control = history(context)
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = (url, options) => String(url).includes('offset=50')
        ? new Promise(resolve => { window.releaseOldPage = body => resolve(new Response(JSON.stringify(body), {status: 200})); })
        : original(url, options);
      window.oldPage = workspaceTest.loadMoreSessions();
    }""")
    toggle(page, "chat-030")
    page.evaluate("releaseOldPage({sessions: [{id: 'stale', title: 'Stale response'}], next_offset: 99, has_more: false})")
    page.evaluate("window.oldPage")
    assert "stale" not in order(page)
    assert page.evaluate("workspaceTest.state.sessionsNextOffset") == 50
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = (url, options) => String(url).endsWith('/pin')
        ? new Promise(resolve => { window.releasePin = body => resolve(new Response(JSON.stringify(body), {status: 200})); })
        : original(url, options);
      window.pendingPin = workspaceTest.setChatPinned('chat-031', true);
    }""")
    page.evaluate("workspaceTest.resetWorkspaceState()")
    page.evaluate("releasePin({session_id:'chat-031',pinned:true,pin_order:100,pin_revision:100})")
    page.evaluate("window.pendingPin")
    assert page.evaluate("workspaceTest.sessionPinStatesById.size") == 0
    assert page.evaluate("workspaceTest.state.sessions.length") == 0


def test_pin_is_keyboard_accessible_and_touch_controls_fit(browser, site):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": 1280, "height": 900}, has_touch=True) as context:
        rows, control = history(context, count=3)
        rows["chat-000"]["title"] = "A very long conversation title " * 10
        source = (Path(__file__).parent / "static/lite/app.js").read_text() + "\nwindow.workspaceTest = {state, workspace, pinningSessionIds};"
        context.route("**/static/lite/app.js*", lambda route: route.fulfill(body=source, content_type="text/javascript"))
        page = context.new_page()
        page.goto(site + "/chat")
        ready(page)
        shell = page.locator('.chat-item-shell[data-session-id="chat-000"]')
        button = shell.locator('.chat-pin-button')
        expect(button).to_be_visible()
        assert shell.locator('.chat-item-actions').evaluate("el => getComputedStyle(el).opacity") == "1"
        button.focus()
        page.keyboard.press('Enter')
        page.wait_for_function("!workspaceTest.pinningSessionIds.size")
        expect(button).to_have_attribute('aria-pressed', 'true')
        expect(button).to_be_focused()
        assert shell.evaluate("el => el.scrollWidth <= el.clientWidth")
        screenshot(page, "session-pins-touch")

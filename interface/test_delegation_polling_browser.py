"""Mock HTTP-only delegation lifecycle in the real Lite page."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from interface.test_agent_examples import mock_api, site  # noqa: F401
from interface.test_chat_workspace_browser import browser, ready, screenshot  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_WORKSPACE_BROWSER_TESTS") != "1", reason="Opt-in Playwright workspace suite",
)


@pytest.mark.parametrize("instant_summary", [False, True])
def test_http_polling_follows_children_into_summary_without_another_prompt(browser, site, instant_summary):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": 1440, "height": 1000}) as context:
        source = (Path(__file__).parent / "static/lite/app.js").read_text() + """
window.workspaceTest = {state, workspace, busySessionIds, liveSessionPollingSessionIds};
"""
        context.route("**/static/lite/app.js*", lambda route: route.fulfill(body=source, content_type="text/javascript"))
        context.route_web_socket("**/api/tui/ws", lambda ws: ws.close())
        session = {"id": "research", "title": "Background delegation test", "message_count": 3}
        live = {"run_id": "parent", "status": "running", "last_event_seq": 1,
                "live_session_id": "live-parent", "assistant_message_id": "first", "background_pending": False}
        messages = [
            {"id": "question", "role": "user", "content": "Run three child tasks", "done": True},
            {"id": "first", "role": "assistant", "content": "", "done": False},
        ]
        calls = mock_api(context, sessions=[{**session, "live": live}])
        polls = []

        def reply(route):
            if urlsplit(route.request.url).path.endswith("/live"):
                polls.append((live["run_id"], live["status"], live["background_pending"]))
            route.fulfill(json={"session_id": "research", "session": {**session, "live": live},
                                "live": live, "messages": messages})

        context.route("**/api/sessions/research**", reply)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(site + "/chat")
        ready(page)
        expect(page.locator("#chat-title")).to_have_text(session["title"])
        status_label = page.locator("#chat-list .chat-item-meta")
        expect(status_label).to_contain_text("Responding…")

        live.update(status="completed", background_pending=True, last_event_seq=2)
        messages[1].update(content="Three children dispatched.", done=True)
        expect(page.locator("#messages")).to_contain_text("Three children dispatched.")
        expect(status_label).to_contain_text("Subagents working")
        expect(status_label).not_to_contain_text("Responding")
        assert not page.evaluate("workspaceTest.busySessionIds.has('research')")
        # More than one polling interval passes with an idle parent. No extra
        # input or WebSocket event may be required to keep this alive.
        before = len(polls)
        page.wait_for_function("workspaceTest.liveSessionPollingSessionIds.has('research')")
        page.wait_for_timeout(4500)
        assert len(polls) >= before + 2
        screenshot(page, "delegation-subagents-working")
        page.reload()
        ready(page)
        page.wait_for_function("workspaceTest.liveSessionPollingSessionIds.has('research')")
        expect(status_label).to_contain_text("Subagents working")

        live.update(run_id="summary", status="completed" if instant_summary else "running",
                    assistant_message_id="second", background_pending=False, last_event_seq=3)
        messages.append({"id": "second", "role": "assistant", "content": "All three children succeeded." if instant_summary else "Summarizing child results…",
                         "done": instant_summary})
        if not instant_summary:
            expect(page.locator("#messages")).to_contain_text("Summarizing child results…")
            expect(status_label).to_contain_text("Responding…")
            expect(status_label).not_to_contain_text("Subagents working")
            assert page.evaluate("workspaceTest.liveSessionPollingSessionIds.has('research')")
            assert page.evaluate("workspaceTest.busySessionIds.has('research')")
            live.update(status="completed", last_event_seq=4)
            messages[-1].update(content="All three children succeeded.", done=True)
        expect(page.locator("#messages")).to_contain_text("All three children succeeded.")
        page.wait_for_function("!workspaceTest.liveSessionPollingSessionIds.has('research')")
        expect(status_label).not_to_contain_text("Responding")
        expect(status_label).not_to_contain_text("Subagents working")
        expect(page.locator("#messages .message.assistant")).to_have_count(2)
        assert not page.evaluate("workspaceTest.busySessionIds.has('research')")
        assert not any(path.endswith("/turns") for _, path, _ in calls)
        assert not errors
        screenshot(page, "delegation-http-instant" if instant_summary else "delegation-http-summary")

"""HTTP-only approval recovery with the real page, run manager and SQLite store."""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from interface.display_store import get_live_session_state, save_live_session_state
from interface.session_run_manager import SessionRunManager
from interface.test_agent_examples import mock_api, site  # noqa: F401
from interface.test_chat_workspace_browser import browser, ready  # noqa: F401
from interface.test_delegation_events import event, seed
from interface.test_session_run_manager_plan_mode import _SubmitBridge
from interface.tui_gateway_bridge import TuiGatewayBridgeError

pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_WORKSPACE_BROWSER_TESTS") != "1", reason="Opt-in Playwright workspace suite",
)


@pytest.fixture
def recovery_backend(tmp_path):
    db_path = tmp_path / "interface.db"
    seed(db_path)
    manager = SessionRunManager(db_path=db_path)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    class Bridge(_SubmitBridge):
        async def rpc(self, method, params):
            if method == "session.interrupt":
                self.late = asyncio.create_task(manager.handle_bridge_event(
                    self, event("approval.request", subagent_id="stopped-child", approval_id="stopped-token")
                ))
                await asyncio.sleep(0.02)
                return {"status": "interrupted"}
            assert method == "approval.respond"
            raise TuiGatewayBridgeError("session not found")

    bridge = Bridge()

    def call(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=10)

    def live():
        return get_live_session_state("user-1", "session-1", db_path=db_path)

    try:
        yield SimpleNamespace(manager=manager, bridge=bridge, db_path=db_path, call=call, live=live)
    finally:
        call(manager.shutdown())
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


@pytest.mark.parametrize("recovery", ["late_stop", "gateway_exit", "missing_session_deny", "missing_session_once"])
def test_http_approval_recovery_does_not_reopen_dialog(browser, site, recovery_backend, recovery):
    from playwright.sync_api import expect

    backend = recovery_backend
    if recovery == "late_stop":
        backend.call(backend.manager.handle_bridge_event(backend.bridge, event("delegation.state", active=1)))
    else:
        # Represent a stale approval saved by a previous version; no active
        # delegation count or foreground run context survives for this row.
        save_live_session_state(
            "user-1", "session-1", status="awaiting_approval", last_event_seq=50,
            pending_approval={"approval_id": "stale", "subagent_id": "child", "resume_status": "interrupted",
                              "command": "python3 -c 'print(123)'"}, db_path=backend.db_path,
        )

    with browser.new_context(viewport={"width": 1440, "height": 1000}) as context:
        source = (Path(__file__).parent / "static/lite/app.js").read_text() + """
window.workspaceTest = {state, workspace, liveSessionPollingSessionIds};
"""
        context.route("**/static/lite/app.js*", lambda route: route.fulfill(body=source, content_type="text/javascript"))
        context.route_web_socket("**/api/tui/ws", lambda ws: ws.close())
        session = {"id": "session-1", "title": "Stopped approval recovery", "message_count": 2}
        mock_api(context, sessions=[{**session, "live": backend.live()}])
        messages = [{"id": "q", "role": "user", "content": "Run a child", "done": True},
                    {"id": "old-assistant", "role": "assistant", "content": "Child started", "done": True}]
        responses = []

        def reply(route):
            if urlsplit(route.request.url).path.endswith("/approval"):
                payload = route.request.post_data_json
                try:
                    backend.call(backend.manager.respond_to_approval(
                        bridge=backend.bridge, user_id="user-1", session_id="session-1",
                        choice=payload["choice"], approval_id=payload["approval_id"],
                    ))
                except TuiGatewayBridgeError as exc:
                    responses.append(str(exc))
                    route.fulfill(status=409, json={"detail": str(exc)})
                    return
                raise AssertionError("The unavailable gateway must not approve a command")
            live = backend.live()
            route.fulfill(json={"session_id": "session-1", "session": {**session, "live": live},
                                "live": live, "messages": messages})

        context.route("**/api/sessions/session-1**", reply)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(site + "/chat")
        ready(page)
        modal = page.locator("#approval-modal")
        if recovery == "late_stop":
            expect(modal).to_be_hidden()

            async def stop():
                await backend.manager.interrupt_run(bridge=backend.bridge, user_id="user-1", session_id="session-1")
                await backend.bridge.late
                await backend.manager.handle_bridge_event(backend.bridge, event("delegation.state", active=0, pending=0))
                await backend.manager.handle_bridge_event(backend.bridge, {"type": "gateway.exit"})

            backend.call(stop())
        else:
            expect(modal).to_be_visible()
            if recovery == "gateway_exit":
                backend.call(backend.manager.handle_bridge_event(backend.bridge, {"type": "gateway.exit"}))
            else:
                button = "#approval-deny" if recovery.endswith("deny") else "#approval-allow-once"
                page.locator(button).click()
                assert responses == ["approval request is no longer pending"]

        expect(modal).to_be_hidden(timeout=10000)
        page.wait_for_function("!workspaceTest.liveSessionPollingSessionIds.has('session-1')")
        assert backend.live()["status"] == "interrupted"
        assert backend.live()["pending_approval"] is None
        assert not backend.live()["background_pending"]
        page.wait_for_timeout(4500)
        expect(modal).to_be_hidden()
        assert not page.evaluate("workspaceTest.state.approvalSubmitting")
        page.reload()
        ready(page)
        expect(modal).to_be_hidden()
        assert not errors

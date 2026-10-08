"""Conversation model isolation on the real Lite page with mocked HTTP/WS."""
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

FAST = "fast"
DEEP = "deep"
MODELS = [
    {"id": DEEP, "name": "deep", "display_name": "Deep", "model": "gpt-6.1-sol"},
    {"id": FAST, "name": FAST, "display_name": "Fast", "model": "gpt-6.1-sol"},
    {"id": "deep-backup", "name": "deep-backup", "display_name": "Deep-backup", "model": "gpt-5.6-sol"},
]


@pytest.fixture
def context(browser):
    with browser.new_context(viewport={"width": 1440, "height": 1000}) as context:
        source = (Path(__file__).parent / "static/lite/app.js").read_text() + """
window.workspaceTest = {state, workspace, busySessionIds, pendingApprovalsBySessionId,
  renderWorkspace, openSession, showDraftChat, fetchModels, switchActiveModel,
  sessionModelRequestsById, sessionModelStatesById, sessionModelErrorsById,
  normalizeSessionSnapshot, applyLiveSessionSnapshot, resetWorkspaceState,
  submitPromptViaTuiBridge, composerSubmissionsBySessionId, handleTuiBridgeEvent};
"""
        context.route("**/static/lite/app.js*", lambda route: route.fulfill(body=source, content_type="text/javascript"))
        context.add_init_script("""
window.closedBridges = 0;
const originalClose = WebSocket.prototype.close;
WebSocket.prototype.close = function(...args) {
  window.closedBridges += 1; return originalClose.apply(this, args);
};
""")
        context.route_web_socket("**/api/tui/ws", lambda ws: ws.send('{"type":"gateway.ready"}'))
        yield context


def history(context, *, status="idle"):
    calls = mock_api(context)
    rows = {
        "a": {"id": "a", "title": "Deep conversation", "last_active": 20, "message_count": 1,
              "model_id": DEEP, "model_revision": 1, "live": {"status": status, "run_id": "run-a"}},
        "b": {"id": "b", "title": "Fast conversation", "last_active": 10, "message_count": 1,
              "model_id": FAST, "model_revision": 1},
    }
    if status == "background":
        rows["a"]["live"].update(status="idle", background_pending=True)
    elif status == "awaiting_approval":
        rows["a"]["live"]["pending_approval"] = {"approval_id": "approval-a", "command": "inspect results"}
    requests = []
    context.route("**/api/models", lambda route: route.fulfill(json={
        "data": MODELS, "default_id": FAST,
    }))

    def respond(route):
        request = route.request
        path = urlsplit(request.url).path
        requests.append((request.method, path, request.post_data_json if request.post_data else None))
        if path == "/api/sessions":
            route.fulfill(json={"sessions": list(rows.values()), "has_more": False})
            return
        session_id = path.split("/")[3]
        row = rows.get(session_id)
        if row is None:
            route.fulfill(status=404, json={"detail": "Session not found"})
            return
        if path.endswith("/model"):
            row.update(model_id=request.post_data_json["id"], model_revision=row["model_revision"] + 1)
            route.fulfill(json={"ok": True, "session_id": session_id,
                                "model_id": row["model_id"], "model_revision": row["model_revision"]})
            return
        body = {"session": row, "session_id": session_id, "live": row.get("live"),
                "model_id": row["model_id"], "model_revision": row["model_revision"],
                "messages": [{"id": "question", "role": "user", "content": "Existing question", "done": True}]}
        route.fulfill(json=body)

    context.route("**/api/sessions**", respond)
    return rows, requests, calls


def open_chat(page, session_id):
    page.evaluate("id => workspaceTest.openSession(id)", session_id)


def click_chat(page, title):
    if page.locator("#mobile-chats-button").is_visible():
        page.locator("#mobile-chats-button").click()
    page.get_by_text(title, exact=True).click()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("status", ["running", "awaiting_approval", "background"])
def test_other_conversation_can_switch_without_disrupting_active_work(context, site, status, width):
    from playwright.sync_api import expect

    # A background approval must not block B. An approval already displayed in
    # the active conversation requires a decision before sidebar navigation.
    initial_status = "running" if status == "awaiting_approval" else status
    rows, requests, calls = history(context, status=initial_status)
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(site + "/chat")
    ready(page)
    expect(page.locator("#model-select")).to_have_value(DEEP)
    expect(page.locator("#model-select")).to_be_disabled()
    closed = page.evaluate("closedBridges")
    click_chat(page, "Fast conversation")
    if status == "awaiting_approval":
        rows["a"]["live"].update(
            status="awaiting_approval",
            pending_approval={"approval_id": "approval-a", "command": "inspect results"},
        )
        page.evaluate("""live => {
            workspaceTest.applyLiveSessionSnapshot('a', {session_id: 'a', live});
            workspaceTest.renderWorkspace();
        }""", rows["a"]["live"])
        expect(page.locator("#approval-modal")).to_be_hidden()
        assert page.evaluate("workspaceTest.pendingApprovalsBySessionId.has('a')")
    expect(page.locator("#model-select")).to_have_value(FAST)
    expect(page.locator("#model-select")).to_be_enabled()
    page.locator("#model-select").select_option(DEEP)
    page.wait_for_function("!workspaceTest.sessionModelRequestsById.size")
    assert rows["b"]["model_id"] == DEEP
    assert rows["a"]["model_id"] == DEEP
    assert page.evaluate("closedBridges") == closed
    page.locator("#model-select").select_option(FAST)
    page.wait_for_function("!workspaceTest.sessionModelRequestsById.size")
    screenshot(page, f"session-models-{status}-{width}")
    click_chat(page, "Deep conversation")
    expect(page.locator("#model-select")).to_be_disabled()
    if status == "awaiting_approval":
        expect(page.locator("#approval-modal")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#approval-modal")).to_be_visible()
        screenshot(page, f"session-models-approval-dialog-{width}")
    elif status == "running":
        expect(page.locator("#send-button")).to_have_attribute("title", "Stop response")
    page.reload()
    ready(page)
    if status == "awaiting_approval":
        expect(page.locator("#approval-modal")).to_be_visible()
        expect(page.locator("#model-select")).to_be_disabled()
    else:
        click_chat(page, "Fast conversation")
        expect(page.locator("#model-select")).to_have_value(FAST)
    assert not any(path.endswith(("/interrupt", "/approval", "/turns")) for _, path, _ in requests)
    assert not any(path == "/api/models/active" for _, path, _ in calls)


@pytest.mark.parametrize("success", [True, False])
def test_pending_save_and_late_result_stay_with_the_target_chat(context, site, success):
    from playwright.sync_api import expect

    rows, requests, _ = history(context)
    pending = []
    context.route("**/api/sessions/b/model", lambda route: pending.append(route))
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    open_chat(page, "b")
    with page.expect_request("**/api/sessions/b/model"):
        page.locator("#model-select").select_option(DEEP)
    expect(page.locator("#model-select")).to_be_disabled()
    expect(page.locator("#send-button")).to_be_disabled()
    page.locator("#prompt-input").fill("Do not submit before saving")
    page.locator("#composer-form").evaluate("form => form.dispatchEvent(new Event('submit', {cancelable:true}))")
    assert not any(path.endswith("/turns") for _, path, _ in requests)
    open_chat(page, "a")
    expect(page.locator("#send-button")).to_be_enabled()
    if success:
        rows["b"].update(model_id=DEEP, model_revision=2)
        pending[0].fulfill(json={"ok": True, "session_id": "b", "model_id": DEEP, "model_revision": 2})
    else:
        # A history refresh can observe the saved selection while gateway
        # synchronization is still pending, before the server compensates it.
        page.evaluate("""() => workspaceTest.normalizeSessionSnapshot({
          id:'b', model_id:'deep', model_revision:2,
        })""")
        rows["b"].update(model_id=FAST, model_revision=3)
        pending[0].fulfill(status=409, json={"detail": {
            "message": "This conversation is responding", "session_id": "b",
            "model_id": FAST, "model_revision": 3,
        }})
    page.wait_for_function("!workspaceTest.sessionModelRequestsById.size")
    expect(page.locator("#chat-title")).to_have_text("Deep conversation")
    expect(page.locator("#model-error")).to_be_hidden()
    open_chat(page, "b")
    expect(page.locator("#model-select")).to_have_value(DEEP if success else FAST)
    if not success:
        expect(page.locator("#model-error")).to_contain_text("This conversation is responding")
        assert page.evaluate("workspaceTest.state.activeSession.model_revision") == 3
    else:
        page.evaluate("""() => {
          const t = workspaceTest;
          t.state.activeSession = t.normalizeSessionSnapshot({id:'b', model_id:'fast', model_revision:1});
          t.renderWorkspace();
        }""")
        expect(page.locator("#model-select")).to_have_value(DEEP)
        page.evaluate("""() => {
          workspaceTest.applyLiveSessionSnapshot('b', {
            session_id:'b', model_id:'fast', model_revision:3, live:null,
          });
          workspaceTest.renderWorkspace();
        }""")
        expect(page.locator("#model-select")).to_have_value(FAST)


def test_draft_model_survives_handoff_and_each_new_draft_starts_fast(context, site):
    from playwright.sync_api import expect

    history(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.evaluate("workspaceTest.showDraftChat()")
    expect(owner.locator("#model-select")).to_have_value(FAST)
    owner.locator("#model-select").select_option(DEEP)
    owner.locator("#prompt-input").fill("Keep this draft and model")
    draft_id = owner.evaluate("workspaceTest.state.activeSessionId")
    current = context.new_page()
    current.goto(site + "/chat")
    ready(current)
    expect(current.locator("#model-select")).to_have_value(DEEP)
    expect(current.locator("#prompt-input")).to_have_value("Keep this draft and model")
    assert current.evaluate("workspaceTest.state.activeSessionId") == draft_id
    current.evaluate("workspaceTest.showDraftChat()")
    expect(current.locator("#model-select")).to_have_value(FAST)


def test_removed_model_can_be_replaced_with_the_only_available_model(context, site):
    from playwright.sync_api import expect

    rows, _, _ = history(context)
    context.route("**/api/models", lambda route: route.fulfill(json={"data": [MODELS[1]], "default_id": FAST}))
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    expect(page.locator("#model-select")).to_have_value(DEEP)
    expect(page.locator("#model-select")).to_be_enabled()
    page.locator("#model-select").select_option(FAST)
    page.wait_for_function("!workspaceTest.sessionModelRequestsById.size")
    expect(page.locator("#model-select")).to_have_value(FAST)
    assert rows["a"]["model_id"] == FAST


def test_late_draft_turn_receipt_preserves_the_other_draft_and_its_model(context, site):
    from playwright.sync_api import expect

    rows, _, _ = history(context)
    pending = []
    context.route("**/api/sessions/draft/turns", lambda route: pending.append(route))
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    page.evaluate("workspaceTest.showDraftChat()")
    page.locator("#model-select").select_option(DEEP)
    page.locator("#prompt-input").fill("First draft")
    with page.expect_request("**/api/sessions/draft/turns"):
        page.locator("#send-button").click()
    assert pending[0].request.post_data_json["model_id"] == DEEP
    page.evaluate("workspaceTest.showDraftChat()")
    second_id = page.evaluate("workspaceTest.state.activeSessionId")
    expect(page.locator("#model-select")).to_have_value(FAST)
    page.locator("#prompt-input").fill("Second draft")
    with page.expect_request("**/api/sessions/draft/turns"):
        page.locator("#send-button").click()
    assert pending[1].request.post_data_json["model_id"] == FAST
    first = {"id": "sent-first", "title": "First draft", "model_id": DEEP, "model_revision": 1}
    rows[first["id"]] = first
    pending[0].fulfill(json={"session": first, "messages": [{"role": "user", "content": "First draft"}]})
    page.wait_for_function("workspaceTest.state.sessions.some(s => s.id === 'sent-first')")
    assert page.evaluate("workspaceTest.state.activeSessionId") == second_id
    assert page.evaluate("workspaceTest.state.draftSession.id") == second_id
    expect(page.locator("#model-select")).to_have_value(FAST)
    second = {"id": "sent-second", "title": "Second draft", "model_id": FAST, "model_revision": 1}
    rows[second["id"]] = second
    pending[1].fulfill(json={"session": second, "messages": [{"role": "user", "content": "Second draft"}]})
    page.wait_for_function("workspaceTest.state.activeSessionId === 'sent-second'")
    expect(page.locator("#model-select")).to_have_value(FAST)
    page.locator("#prompt-input").fill("Continue second")
    with page.expect_request("**/api/sessions/sent-second/turns") as request:
        page.locator("#send-button").click()
    assert "model_id" not in request.value.post_data_json
    open_chat(page, "sent-first")
    expect(page.locator("#model-select")).to_have_value(DEEP)


@pytest.mark.parametrize('choice', ['once', 'session', 'always', 'deny'])
def test_real_approval_buttons(context, site, choice):
    from playwright.sync_api import expect
    rows, requests, _ = history(context, status='awaiting_approval')
    decisions = []
    def respond(route):
        decisions.append(route.request.post_data_json)
        rows['a']['live'] = {'status':'running','run_id':'run-a', 'last_event_seq':2}
        route.fulfill(json={'ok':True, 'live':rows['a']['live']})
    context.route('**/api/sessions/a/approval', respond)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.goto(site + '/chat')
    ready(page)
    expect(page.locator('#approval-modal')).to_be_visible()
    button = '#approval-deny' if choice == 'deny' else '#approval-allow-' + choice
    page.locator(button).click()
    expect(page.locator('#approval-modal')).to_be_hidden()
    assert decisions == [{'choice':choice, 'approval_id':'approval-a'}]
    expect(page.locator('#model-select')).to_be_disabled()
    rows['a']['live'] = {'status':'completed','last_event_seq':3}
    page.evaluate("() => { workspaceTest.applyLiveSessionSnapshot('a', {session_id:'a', model_id:'deep', model_revision:1, live:{status:'completed',last_event_seq:3}}); workspaceTest.renderWorkspace(); }")
    expect(page.locator('#model-select')).to_be_enabled()
    assert not errors
    screenshot(page, 'approval-' + choice + '-completed')


def test_compression_continuation_frontend(context, site):
    from playwright.sync_api import expect
    rows, requests, _ = history(context, status='running')
    rows['a']['resume_session_id'] = 'physical-before'
    page = context.new_page()
    errors=[]
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.goto(site + '/chat')
    ready(page)
    rows['a']['resume_session_id'] = 'physical-after'
    rows['a']['live'] = {'status':'completed','last_event_seq':20,'tip_session_id':'physical-after'}
    page.evaluate("snapshot => {workspaceTest.applyLiveSessionSnapshot('a', snapshot); workspaceTest.renderWorkspace();}", {'session_id':'a', 'session':rows['a'], 'live':rows['a']['live'], 'messages':[{'id':'q','role':'user','content':'Prior context remains visible','done':True},{'id':'ans','role':'assistant','content':'Context compressed and continuation complete','done':True}]})
    expect(page.locator('#chat-title')).to_have_text('Deep conversation')
    expect(page.locator('#model-select')).to_have_value('deep')
    expect(page.locator('#model-select')).to_be_enabled()
    assert page.evaluate('workspaceTest.state.activeSessionId') == 'a'
    assert page.evaluate('workspaceTest.state.activeSession.resume_session_id') == 'physical-after'
    page.locator('#prompt-input').fill('Continue after compression')
    with page.expect_request('**/api/sessions/a/turns') as request:
        page.locator('#send-button').click()
    assert 'model_id' not in request.value.post_data_json
    assert request.value.post_data_json['prompt'] == 'Continue after compression'
    screenshot(page, 'compression-continuation')
    page.reload()
    ready(page)
    expect(page.locator('#model-select')).to_have_value('deep')
    assert page.evaluate('workspaceTest.state.activeSession.resume_session_id') == 'physical-after'
    assert not errors


def test_approval_expiry_clears_only_current_token(context, site):
    from playwright.sync_api import expect
    rows, _, _ = history(context, status='awaiting_approval')
    page=context.new_page()
    errors=[]
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.goto(site+'/chat')
    ready(page)
    expect(page.locator('#approval-modal')).to_be_visible()
    page.evaluate("workspaceTest.handleTuiBridgeEvent({type:'approval.expired',persistent_session_id:'a',payload:{approval_id:'older-token'}})")
    expect(page.locator('#approval-modal')).to_be_visible()
    rows['a']['live']={'status':'completed','pending_approval':None,'last_event_seq':21}
    page.evaluate("workspaceTest.handleTuiBridgeEvent({type:'approval.expired',persistent_session_id:'a',payload:{approval_id:'approval-a'}})")
    expect(page.locator('#approval-modal')).to_be_hidden()
    screenshot(page, 'approval-expired')
    page.reload()
    ready(page)
    expect(page.locator('#approval-modal')).to_be_hidden()
    assert not errors

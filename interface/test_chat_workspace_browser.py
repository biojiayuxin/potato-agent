"""Mock-only tests: POTATO_WORKSPACE_BROWSER_TESTS=1 python -m pytest this_file.

POTATO_WORKSPACE_SCREENSHOTS retains review screenshots. No production API is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from interface.test_agent_examples import site as static_site, mock_api  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_WORKSPACE_BROWSER_TESTS") != "1", reason="Opt-in Playwright workspace suite",
)
STATIC = Path(__file__).parent / "static"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        instance = driver.chromium.launch(
            executable_path=os.getenv("POTATO_PLAYWRIGHT_EXECUTABLE") or None,
            args=["--no-sandbox", "--no-proxy-server", "--host-resolver-rules=MAP workspace.test 127.0.0.1"],
        )
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def site(static_site):
    return static_site


@pytest.fixture
def context(browser):
    with browser.new_context(viewport={"width": 1440, "height": 1000}) as context:
        source = (STATIC / "lite/app.js").read_text() + """
window.workspaceTest = {state, workspace, busySessionIds, pendingApprovalsBySessionId,
  renderWorkspace, renderShareImportDialog, pollAuthSession, startWorkspaceRuntime, yieldWorkspace,
  openDirectory, openFilePreview, api};
"""
        context.route("**/static/lite/app.js*", lambda route: route.fulfill(body=source, content_type="text/javascript"))
        yield context


def entry_url(site, *, kind="example", request_id="test-request", text="Incoming research question"):
    entry = {"id": request_id, "kind": kind}
    if kind == "example":
        entry["example"] = {"page": "genes", "text": text}
    elif kind == "share":
        entry["token"] = "a" * 48
    return site + "/chat#entry=" + quote(json.dumps(entry))


def starts(calls):
    return sum(path == "/api/runtime/start" for _, path, _ in calls)


def ready(page, timeout=20000):
    from playwright.sync_api import expect
    expect(page.locator("#workspace-view")).to_be_visible(timeout=timeout)
    page.wait_for_function("window.workspaceTest?.workspace.ready", timeout=timeout)


def retired(page, site):
    from playwright.sync_api import expect
    expect(page).to_have_url(site + "/lite")
    expect(page.locator("#workspace-view")).to_be_hidden()
    expect(page.locator("#portal-account-name")).to_be_visible()
    expect(page.locator("#enter-chat-button")).to_have_text("Enter chat")
    expect(page.locator("#entry-home-link")).to_have_count(0)
    assert not page.evaluate("workspaceTest.workspace.owned")


def screenshot(page, name):
    directory = os.getenv("POTATO_WORKSPACE_SCREENSHOTS")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        mode = "http" if urlsplit(page.url).hostname == "workspace.test" else "native"
        page.screenshot(path=str(Path(directory) / f"{mode}-{name}.png"))


@pytest.mark.parametrize("auth", ["signin", "temporary"])
def test_login_and_example_entry_work_with_available_lock_mode(context, site, auth):
    from playwright.sync_api import expect

    calls = mock_api(context, authenticated=False)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site + "/genes")
    if "workspace.test" in site:
        assert page.evaluate("!isSecureContext && !navigator.locks && !crypto.randomUUID")
    else:
        assert page.evaluate("isSecureContext && !!navigator.locks")
    page.locator("#ask-potato-agent").click()
    expect(page).to_have_url(site + "/chat")
    page.reload()
    if auth == "signin":
        page.locator("#show-login-button").click()
        page.locator("#email").fill("research@example.com")
        page.locator("#password").fill("Example123!")
        page.locator('#login-form button[type="submit"]').click()
    else:
        page.locator("#show-temporary-button").click()
        page.locator("#temporary-agreement-checkbox").check()
        page.locator("#temporary-confirm-start").click()
    ready(page)
    expect(page.locator("#prompt-input")).to_have_value(page.evaluate("PotatoAgentExamples.build('genes')"))
    assert starts(calls) == 1
    assert not any(path.endswith("/turns") for _, path, _ in calls)
    page.locator("#send-button").click()
    expect(page.locator("#messages")).to_contain_text("Summarize the functional annotations")
    assert sum(path.endswith("/turns") for _, path, _ in calls) == 1
    assert not errors


@pytest.mark.parametrize("path", ["/", "/lite"])
def test_authenticated_portal_only_starts_chat_after_click(context, site, path):
    from playwright.sync_api import expect
    calls = mock_api(context)
    page = context.new_page()
    page.goto(site + path)
    expect(page.locator("#portal-account-name")).to_have_text("Research workspace")
    expect(page.locator(".portal-nav")).to_be_visible()
    expect(page.locator("#daily-updates-panel")).to_be_visible()
    assert not any(path.startswith(("/api/runtime", "/api/models", "/api/sessions", "/api/files")) for _, path, _ in calls)
    page.locator("#enter-chat-button").click()
    ready(page)
    expect(page).to_have_url(site + "/chat")
    assert starts(calls) == 1


@pytest.mark.parametrize("module", ["genes", "genomes", None])
def test_new_tab_takes_over_draft_attachments_and_composer_mode(context, site, module):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Original unsent draft")
    owner.evaluate("""() => {
      window.focus = () => { throw new Error('Tab focusing must not be used'); };
      const t = workspaceTest;
      t.state.pendingAttachments = [{itemId:'file-1', id:'upload-1', name:'evidence.tsv', status:'uploaded', localPath:'evidence.tsv', size:12}];
      t.state.composerMode = 'plan'; t.renderWorkspace();
    }""")
    original = owner.evaluate("JSON.stringify({session:workspaceTest.state.activeSessionId, attachments:workspaceTest.state.pendingAttachments})")
    with context.expect_page() as popup:
        owner.locator("#chat-home-button").click()
    portal = popup.value
    expect(portal.locator("#portal-account-name")).to_be_visible()
    assert starts(calls) == 1
    if module:
        portal.locator(f'.portal-nav-desktop a[data-module="{module}"]').click()
        portal.locator('.portal-nav-desktop a[data-module="lite"]').click()
    else:
        portal.locator("#enter-chat-button").click()
    ready(portal)
    retired(owner, site)
    expect(portal.locator("#prompt-input")).to_have_value("Original unsent draft")
    expect(portal.locator("#attachment-list")).to_contain_text("evidence.tsv")
    assert portal.evaluate("JSON.stringify({session:workspaceTest.state.activeSessionId, attachments:workspaceTest.state.pendingAttachments})") == original
    assert portal.evaluate("workspaceTest.state.composerMode") == "plan"
    assert starts(calls) == 2
    owner.bring_to_front()
    owner.reload()
    retired(owner, site)
    assert starts(calls) == 2
    owner.locator("#enter-chat-button").click()
    ready(owner)
    retired(portal, site)
    expect(owner.locator("#prompt-input")).to_have_value("Original unsent draft")


@pytest.mark.parametrize("status", ["running", "awaiting_approval", "interrupted"])
def test_saved_session_and_live_state_are_reloaded_without_resubmitting(context, site, status):
    from playwright.sync_api import expect
    session = {"id": "research", "title": "Selected research", "message_count": 1}
    live = {"status": status, "session_id": "research", "run_id": "run-1"}
    if status == "awaiting_approval":
        live["pending_approval"] = {"approval_id": "approval-1", "command": "inspect evidence.tsv"}
    calls = mock_api(context, sessions=[{**session, "live": live}])
    context.route("**/api/sessions/research**", lambda route: route.fulfill(json={
        "session": session, "live": live, "messages": [{"role": "user", "content": "Existing research question"}],
    }))
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    other = context.new_page()
    other.goto(site + "/chat")
    ready(other)
    retired(owner, site)
    expect(other.locator("#chat-title")).to_have_text("Selected research")
    expect(other.locator("#messages")).to_contain_text("Existing research question")
    if status == "awaiting_approval":
        expect(other.locator("#approval-modal")).to_be_visible()
        expect(owner.locator("#approval-modal")).to_be_hidden()
    assert other.evaluate("workspaceTest.busySessionIds.has('research')") == (status != "interrupted")
    assert not any(path.endswith(("/turns", "/interrupt", "/approval")) for _, path, _ in calls)


@pytest.mark.parametrize("kind", ["example", "share"])
def test_incoming_request_confirms_in_current_tab_and_deduplicates(context, site, kind):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Preserve my draft")
    current = context.new_page()
    current.goto(entry_url(site, kind=kind))
    ready(current)
    retired(owner, site)
    dialog = current.locator("#workspace-entry-dialog")
    expect(dialog).to_be_visible()
    expect(owner.locator("#workspace-entry-dialog")).to_be_hidden()
    third = context.new_page()
    third.goto(entry_url(site, request_id="second-request", kind=kind))
    expect(third.locator("#workspace-entry-status")).to_contain_text("retry shortly")
    expect(third.locator("#workspace-view")).to_be_hidden()
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(current.locator("#prompt-input")).to_have_value("Preserve my draft")
    third.locator("#enter-chat-button").click()
    ready(third)
    retired(current, site)
    third.locator("#workspace-entry-dialog").get_by_role("button", name="Open request").click()
    third.wait_for_function("!workspaceTest.workspace.pending")
    if kind == "example":
        expect(third.locator("#prompt-input")).to_have_value("Incoming research question")
    else:
        expect(third.locator("#chat-title")).to_have_text("Shared research")
    third.locator("#prompt-input").fill("Do not overwrite after receipt")
    current.goto(entry_url(site, kind=kind, request_id="second-request"))
    ready(current)
    retired(third, site)
    expect(current.locator("#workspace-entry-dialog")).to_be_hidden()
    expect(current.locator("#prompt-input")).to_have_value("Do not overwrite after receipt")
    assert not any(path.endswith('/turns') for _, path, _ in calls)
    if kind == "share":
        assert sum(path == '/api/chat-shares/import' for _, path, _ in calls) == 1


def test_same_tab_entry_keeps_lock_and_does_not_replace_pending_request(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Same-tab draft")
    owner.goto(entry_url(site))
    dialog = owner.locator("#workspace-entry-dialog")
    expect(dialog).to_be_visible()
    owner.goto(entry_url(site, request_id="conflict", text="Must not replace pending request"))
    expect(owner.locator("#share-toast")).to_contain_text("retry shortly")
    expect(owner.locator("#workspace-entry-preview")).to_have_text("Incoming research question")
    dialog.get_by_role("button", name="Open request").click()
    expect(owner.locator("#prompt-input")).to_have_value("Incoming research question")
    owner.wait_for_function("sessionStorage.getItem('potato-chat-entry-v1') === null")
    assert starts(calls) == 1


def test_upload_finishes_before_handoff_and_second_contender_is_rejected(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    uploads = []
    owner.route("**/api/files/upload", lambda route: uploads.append(route))
    owner.locator("#file-input").set_input_files({"name": "evidence.tsv", "mimeType": "text/plain", "buffer": b"gene\tvalue\nA\t1\n"})
    expect(owner.locator("#attachment-list")).to_contain_text("Uploading")
    current = context.new_page()
    current.goto(site + "/chat")
    expect(current.locator("#workspace-entry-status")).to_contain_text("finish uploading")
    expect(owner.locator("#workspace-view")).to_be_visible()
    third = context.new_page()
    third.goto(site + "/chat")
    expect(third.locator("#workspace-entry-status")).to_contain_text("retry shortly")
    assert starts(calls) == 1
    uploads[0].fulfill(json={"id": "uploaded-1", "name": "evidence.tsv", "size": 15, "path": "evidence.tsv"})
    ready(current)
    retired(owner, site)
    expect(current.locator("#attachment-list")).to_contain_text("evidence.tsv")
    assert current.evaluate("workspaceTest.state.pendingAttachments[0].id") == "uploaded-1"
    assert starts(calls) == 2


def test_unresponsive_owner_is_never_stolen_and_can_resume_handoff(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Frozen draft")
    other = context.new_page()
    other.goto(site + "/")
    other.bring_to_front()
    # Headless Chromium can thaw on navigation; withhold delivery until explicit wake-up.
    owner.evaluate("() => { window.savedReceiver = workspaceTest.workspace.channel.onmessage; workspaceTest.workspace.channel.onmessage = null; }")
    cdp = context.new_cdp_session(owner)
    cdp.send("Page.setWebLifecycleState", {"state": "frozen"})
    other.goto(site + "/chat")
    expect(other.locator("#workspace-entry-status")).to_contain_text("Moving your chat")
    expect(other.locator("#workspace-view")).to_be_hidden()
    assert starts(calls) == 1
    cdp.send("Page.setWebLifecycleState", {"state": "active"})
    owner.evaluate("() => { workspaceTest.workspace.channel.onmessage = window.savedReceiver; }")
    ready(other)
    retired(owner, site)
    expect(other.locator("#prompt-input")).to_have_value("Frozen draft")


@pytest.mark.parametrize("action", ["turns", "approval"])
def test_pending_submission_finishes_before_transfer(context, site, action):
    from playwright.sync_api import expect
    session = {"id": "research", "title": "Selected research", "message_count": 1}
    live = {"status": "idle", "session_id": "research"}
    if action == "approval":
        live.update(status="awaiting_approval", pending_approval={"approval_id": "approval-1", "command": "inspect evidence.tsv"})
    calls = mock_api(context, sessions=[{**session, "live": live}])
    context.route("**/api/sessions/research**", lambda route: route.fulfill(json={
        "session": session, "live": live, "messages": [{"role": "user", "content": "Existing question"}],
    }))
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    pending = []
    owner.route(f"**/api/sessions/research/{action}", lambda route: pending.append(route))
    with owner.expect_request(f"**/api/sessions/research/{action}"):
        if action == "turns":
            owner.locator("#prompt-input").fill("One submission only")
            owner.locator("#send-button").click()
        else:
            owner.locator("#approval-allow-once").click()
    current = context.new_page()
    current.goto(site + "/chat")
    expect(current.locator("#workspace-entry-status")).to_contain_text("Moving your chat")
    expect(owner.locator("#workspace-view")).to_be_visible()
    assert starts(calls) == 1
    live.clear()
    live.update(status="running", session_id="research", run_id="run-1")
    pending[0].fulfill(json={"session": session, "live": live, "messages": []})
    ready(current)
    retired(owner, site)
    assert current.evaluate("workspaceTest.busySessionIds.has('research')")
    expect(current.locator("#approval-modal")).to_be_hidden()
    assert len(pending) == 1
    assert not any(path.endswith(("/turns", "/approval")) for _, path, _ in calls)


def test_file_browser_location_and_preview_are_restored(context, site):
    from playwright.sync_api import expect
    mock_api(context)
    context.route("**/api/files/open?**", lambda route: route.fulfill(json={
        "root": "/research", "path": "/results/", "entries": [{"name": "evidence.tsv", "type": "file", "size": 12}],
    }))
    context.route("**/api/files/tree?**", lambda route: route.fulfill(json={
        "entries": [{"name": "evidence.tsv", "type": "file", "size": 12}]
        if parse_qs(urlsplit(route.request.url).query).get("path") == ["results"] else [],
    }))
    context.route("**/api/files/preview/meta?**", lambda route: route.fulfill(json={
        "filename": "evidence.tsv", "size": 12, "preview_type": "text",
    }))
    context.route("**/api/files/preview/text?**", lambda route: route.fulfill(json={"content": "gene\tvalue\nA\t1"}))
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.evaluate("workspaceTest.openDirectory('/research/results')")
    owner.evaluate("workspaceTest.openFilePreview('/results/evidence.tsv')")
    expect(owner.locator("#chat-title")).to_have_text("evidence.tsv")
    owner.wait_for_function("workspaceTest.state.filePreviewTabs[0]?.content.includes('gene')")
    current = context.new_page()
    current.goto(site + "/chat")
    ready(current)
    retired(owner, site)
    expect(current.locator("#chat-title")).to_have_text("evidence.tsv")
    current.wait_for_function("workspaceTest.state.filePreviewTabs[0]?.content.includes('gene')")
    assert current.evaluate("workspaceTest.state.currentPath") == "/results/"
    assert current.evaluate("workspaceTest.state.workspaceRoot") == "/research"
    assert current.evaluate("[...workspaceTest.state.expandedPaths]") == ["/results/"]


def test_storage_failure_keeps_original_draft_and_workspace(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Must survive failed transfer")
    owner.evaluate("() => { indexedDB.open = () => { throw new Error('Storage unavailable'); }; }")
    other = context.new_page()
    other.goto(site + "/chat")
    expect(other.locator("#workspace-entry-status")).to_contain_text("Please retry")
    expect(owner.locator("#workspace-view")).to_be_visible()
    expect(owner.locator("#prompt-input")).to_have_value("Must survive failed transfer")
    assert not owner.locator("#workspace-view").evaluate("el => el.inert")
    assert starts(calls) == 1


def test_unsaved_failed_first_turn_is_preserved_during_transfer(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.route("**/api/sessions/draft/turns", lambda route: route.fulfill(status=400, json={"detail": "The selected model is unavailable"}))
    owner.locator("#prompt-input").fill("Keep the failed research question")
    owner.locator("#send-button").click()
    expect(owner.locator("#messages")).to_contain_text("The selected model is unavailable")
    current = context.new_page()
    current.goto(site + "/chat")
    ready(current)
    retired(owner, site)
    expect(current.locator("#messages")).to_contain_text("Keep the failed research question")
    expect(current.locator("#messages")).to_contain_text("The selected model is unavailable")
    assert current.evaluate("workspaceTest.state.activeSession.isDraft")
    assert not any(path.endswith('/turns') for _, path, _ in calls)


@pytest.mark.parametrize("failure", ["closed", "runtime-error"])
def test_saved_handoff_survives_recipient_failure(context, site, failure):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Recover the saved handoff")
    original_session = owner.evaluate("workspaceTest.state.activeSessionId")
    recipient = context.new_page()
    pending = []
    recipient.route("**/api/runtime/start", lambda route: pending.append(route))
    with recipient.expect_request("**/api/runtime/start"):
        recipient.goto(site + "/chat")
    retired(owner, site)
    if failure == "closed":
        recipient.close()
        current = context.new_page()
        current.goto(site + "/chat")
    else:
        pending[0].fulfill(status=503, json={"detail": "Runtime temporarily unavailable"})
        expect(recipient.locator("#runtime-start-error")).to_contain_text("temporarily unavailable")
        recipient.unroute("**/api/runtime/start")
        recipient.locator("#runtime-start-retry-button").click()
        current = recipient
    ready(current)
    expect(current.locator("#prompt-input")).to_have_value("Recover the saved handoff")
    assert current.evaluate("workspaceTest.state.activeSessionId") == original_session
    assert starts(calls) == 2
    assert current.evaluate("navigator.locks.query().then(q => q.held.filter(l => l.name === 'potato-chat-workspace-v1').length)") == 1


def test_timeout_cancels_queued_takeover_until_user_retries(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Keep this draft through timeout")
    owner.evaluate("() => { window.savedReceiver = workspaceTest.workspace.channel.onmessage; workspaceTest.workspace.channel.onmessage = null; }")
    other = context.new_page()
    other.goto(site + "/chat")
    expect(other.locator("#workspace-entry-status")).to_contain_text("not responded", timeout=25000)
    assert other.evaluate("navigator.locks.query().then(q => q.pending.length)") == 0
    owner.evaluate("() => { workspaceTest.workspace.channel.onmessage = window.savedReceiver; }")
    ready(owner)
    expect(owner.locator("#prompt-input")).to_have_value("Keep this draft through timeout")
    assert starts(calls) == 1
    other.locator("#enter-chat-button").click()
    ready(other)
    retired(owner, site)
    expect(other.locator("#prompt-input")).to_have_value("Keep this draft through timeout")


def test_simultaneous_fresh_tabs_only_initialize_with_exclusive_ownership(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    portal = context.new_page()
    portal.goto(site + "/")
    expect(portal.locator("#enter-chat-button")).to_be_visible()
    ownership_at_start = []

    def start(route):
        ownership_at_start.append([
            page.evaluate("Boolean(window.workspaceTest?.workspace.owned)") for page in context.pages
        ])
        route.fallback()

    context.route("**/api/runtime/start", start)
    tabs = []
    context.on("page", lambda page: tabs.append(page))
    with context.expect_page(predicate=lambda _page: len(tabs) == 2):
        portal.evaluate("() => { window.open('/chat'); window.open('/chat'); }")
    assert len(tabs) == 2
    for page in tabs:
        page.wait_for_function("window.workspaceTest && (workspaceTest.workspace.ready || location.pathname === '/lite' || document.getElementById('workspace-entry-status').textContent.includes('retry shortly'))")
    assert sum(page.evaluate("workspaceTest.workspace.owned") for page in tabs) == 1
    assert all(sum(owners) == 1 for owners in ownership_at_start)
    assert starts(calls) == len(ownership_at_start)


def test_takeover_refresh_close_and_history_do_not_automatically_reclaim(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/")
    owner.locator("#enter-chat-button").click()
    ready(owner)
    owner.locator("#prompt-input").fill("History draft")
    owner.go_back()
    expect(owner.locator(".portal-nav")).to_be_visible()
    owner.go_forward()
    ready(owner)
    expect(owner.locator("#prompt-input")).to_have_value("History draft")
    other = context.new_page()
    other.goto(site + "/chat")
    ready(other)
    retired(owner, site)
    count = starts(calls)
    owner.go_back()
    owner.go_forward()
    retired(owner, site)
    owner.evaluate("dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    retired(owner, site)
    assert starts(calls) == count
    other.reload()
    ready(other)
    other.close()
    owner.locator("#enter-chat-button").click()
    ready(owner)


def test_back_during_startup_cannot_reopen_chat_from_late_response(context, site):
    from playwright.sync_api import expect
    mock_api(context)
    pending = []
    context.route("**/api/runtime/start", lambda route: pending.append(route))
    page = context.new_page()
    page.goto(site + "/")
    with page.expect_request("**/api/runtime/start"):
        page.locator("#enter-chat-button").click()
    page.go_back()
    expect(page.locator("#enter-chat-button")).to_be_visible()
    assert not page.evaluate("workspaceTest.workspace.owned")
    pending[0].fulfill(json={"user": {"id": "example-test"}})
    expect(page.locator("#workspace-view")).to_be_hidden()
    context.unroute("**/api/runtime/start")
    page.locator("#enter-chat-button").click()
    ready(page)


def test_composer_is_disabled_until_handoff_has_been_restored(context, site):
    from playwright.sync_api import expect
    mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Preserve until fully restored")
    current = context.new_page()
    pending = []
    current.route("**/api/models", lambda route: pending.append(route))
    with current.expect_request("**/api/models"):
        current.goto(site + "/chat")
    expect(current.locator("#workspace-view")).to_be_visible()
    assert current.locator("#workspace-view").evaluate("e => e.inert")
    assert not current.evaluate("workspaceTest.workspace.ready")
    pending[0].fulfill(json={"data": [{"id": "test-model"}]})
    ready(current)
    assert not current.locator("#workspace-view").evaluate("e => e.inert")
    expect(current.locator("#prompt-input")).to_have_value("Preserve until fully restored")


def test_full_page_back_forward_returns_to_portal_when_other_tab_owns_chat(context, site):
    mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.goto(site + "/genes")
    other = context.new_page()
    other.goto(site + "/chat")
    ready(other)
    owner.go_back()
    retired(owner, site)
    assert other.evaluate("workspaceTest.workspace.owned")


def test_crashed_owner_releases_browser_lock(context, site):
    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    with owner.expect_event("crash"):
        try:
            owner.goto("chrome://crash", timeout=2000)
        except Exception:
            pass
    other = context.new_page()
    other.goto(site + "/chat")
    ready(other)
    assert starts(calls) == 2


def test_pending_share_import_moves_and_retries_in_current_tab(context, site):
    from playwright.sync_api import expect
    calls = mock_api(context)
    context.route("**/api/chat-shares/import", lambda route: route.fulfill(status=202, headers={"Retry-After": "60"}, json={}))
    owner = context.new_page()
    owner.goto(entry_url(site, kind="share"))
    ready(owner)
    expect(owner.locator("#share-import-modal")).to_be_visible()
    owner.wait_for_function("!workspaceTest.workspace.pending")
    context.unroute("**/api/chat-shares/import")
    other = context.new_page()
    other.goto(site + "/chat")
    ready(other)
    retired(owner, site)
    expect(other.locator("#chat-title")).to_have_text("Shared research")
    expect(other.locator("#share-import-modal")).to_be_hidden()
    assert sum(path == '/api/chat-shares/import' for _, path, _ in calls) == 1


@pytest.mark.parametrize("capability", ["BroadcastChannel", "indexedDB"])
def test_unsupported_browser_keeps_portal_queries(context, site, capability):
    from playwright.sync_api import expect
    calls = mock_api(context)
    context.add_init_script(f"Object.defineProperty(window, '{capability}', {{value:undefined}})")
    page = context.new_page()
    page.goto(site + "/chat")
    expect(page.locator("#workspace-entry-status")).to_contain_text("Chat requires")
    expect(page.locator("#enter-chat-button")).to_be_disabled()
    page.locator('.portal-nav-desktop a[data-module="genes"]').click()
    expect(page).to_have_url(site + "/genes")
    assert starts(calls) == 0


def test_logout_and_account_change_invalidate_old_workspace(context, site):
    from playwright.sync_api import expect
    mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Old account draft")
    portal = context.new_page()
    portal.goto(site + "/")
    portal.locator("#portal-sign-out-button").click()
    expect(owner.locator("#workspace-view")).to_be_hidden()
    expect(owner.locator("#prompt-input")).to_have_value("")
    mock_api(context, user_id="next-account")
    portal.reload()
    portal.locator("#enter-chat-button").click()
    ready(portal)
    context.route("**/api/auth/session", lambda route: route.fulfill(json={"authenticated": True, "user": {"id": "third-account"}}))
    portal.evaluate("workspaceTest.pollAuthSession()")
    expect(portal.locator("#workspace-view")).to_be_hidden()
    assert not portal.evaluate("workspaceTest.workspace.owned")


def test_session_expiration_during_runtime_start_cannot_reopen_chat(context, site):
    from playwright.sync_api import expect
    mock_api(context)
    pending = []
    context.route("**/api/runtime/start", lambda route: pending.append(route))
    page = context.new_page()
    with page.expect_request("**/api/runtime/start"):
        page.goto(site + "/chat")
    context.route("**/api/auth/session", lambda route: route.fulfill(json={"authenticated": False}))
    page.evaluate("workspaceTest.pollAuthSession()")
    expect(page.locator("#login-form")).to_be_visible()
    pending[0].fulfill(json={"user": {"id": "example-test"}})
    expect(page.locator("#workspace-view")).to_be_hidden()
    assert not page.evaluate("workspaceTest.workspace.owned")


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844), (320, 740)])
def test_takeover_portal_announcement_and_confirmation_layout(context, site, width, height):
    from playwright.sync_api import expect
    mock_api(context)
    context.route("**/api/announcement", lambda route: route.fulfill(json={
        "server_time": "2026-09-10T00:00:00Z",
        "announcement": {"id": "workspace-layout", "message": "Research maintenance notice. " * 30, "ends_at": None},
    }))
    owner = context.new_page()
    owner.set_viewport_size({"width": width, "height": height})
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Draft to preserve")
    owner.evaluate("workspaceTest.state.activeSession.title = 'A very long research title '.repeat(15)")
    current = context.new_page()
    current.set_viewport_size({"width": width, "height": height})
    errors = []
    current.on("pageerror", lambda error: errors.append(str(error)))
    current.goto(entry_url(site, text="LongUnbrokenTitle" * 200))
    ready(current)
    retired(owner, site)
    dialog = current.locator("#workspace-entry-dialog")
    expect(dialog).to_be_visible()
    assert dialog.evaluate("e => { const r=e.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight; }")
    screenshot(current, f"takeover-confirm-{width}")
    screenshot(owner, f"retired-portal-{width}")
    current.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(current.locator("#prompt-input")).to_have_value("Draft to preserve")
    expect(current.locator("#chat-home-button")).to_be_visible()
    assert current.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(current, f"takeover-chat-{width}")
    if width <= 800:
        current.locator("#mobile-chats-button").click()
    current.locator("#chat-home-button").hover()
    screenshot(current, f"takeover-sidebar-{width}")
    assert errors == []

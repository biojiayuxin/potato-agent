"""Mock-only HTTP compatibility regressions, without secure-context overrides."""
from __future__ import annotations

import os

import pytest

from interface.test_agent_examples import mock_api, site as static_site  # noqa: F401
from interface.test_chat_workspace_browser import (  # noqa: F401
    browser,
    context,
    entry_url,
    ready,
    screenshot,
    starts,
    test_login_and_example_entry_work_with_available_lock_mode as check_login,
    test_logout_and_account_change_invalidate_old_workspace as check_logout,
)

pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_WORKSPACE_BROWSER_TESTS") != "1", reason="Opt-in Playwright workspace suite",
)


@pytest.fixture(scope="module")
def site(static_site):
    return static_site.replace("127.0.0.1", "workspace.test")


@pytest.mark.parametrize("auth", ["signin", "temporary"])
def test_http_login_and_examples(context, site, auth):
    check_login(context, site, auth)


def test_http_idle_and_resume_keep_chat_and_draft(context, site):
    from playwright.sync_api import expect

    calls = mock_api(context)
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    assert page.evaluate("!isSecureContext && !navigator.locks")
    page.locator("#prompt-input").fill("Keep my unsent draft after a timer delay")
    page.clock.install()
    page.clock.fast_forward(120000)
    page.evaluate("dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange'))")
    expect(page).to_have_url(site + "/chat")
    ready(page)
    expect(page.locator("#prompt-input")).to_have_value("Keep my unsent draft after a timer delay")
    assert starts(calls) == 1


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_http_home_and_new_chat_leave_existing_workspace_untouched(context, site, width, height):
    from playwright.sync_api import expect

    calls = mock_api(context)
    owner = context.new_page()
    owner.set_viewport_size({"width": width, "height": height})
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("First window draft")
    owner.evaluate("""() => {
      workspaceTest.state.pendingAttachments = [{itemId:'file-1', id:'upload-1', name:'evidence.tsv', status:'uploaded', localPath:'evidence.tsv', size:12}];
      workspaceTest.state.composerMode = 'plan'; workspaceTest.renderWorkspace();
    }""")
    if width < 800:
        owner.locator("#mobile-chats-button").click()
    with context.expect_page() as opened:
        owner.locator("#chat-home-button").click()
    portal = opened.value
    portal.set_viewport_size({"width": width, "height": height})
    expect(portal.locator("#enter-chat-button")).to_be_visible()
    assert starts(calls) == 1
    portal.locator("#enter-chat-button").click()
    ready(portal)
    expect(portal.locator("#prompt-input")).to_have_value("")
    portal.locator("#prompt-input").fill("Second window draft")
    expect(owner).to_have_url(site + "/chat")
    ready(owner)
    expect(owner.locator("#prompt-input")).to_have_value("First window draft")
    expect(owner.locator("#attachment-list")).to_contain_text("evidence.tsv")
    assert owner.evaluate("workspaceTest.state.composerMode") == "plan"
    assert starts(calls) == 2
    screenshot(portal, f"independent-chat-{width}")
    portal.reload()
    ready(portal)
    owner.bring_to_front()
    expect(owner).to_have_url(site + "/chat")
    expect(owner.locator("#prompt-input")).to_have_value("First window draft")
    portal.close()
    assert owner.evaluate("workspaceTest.workspace.ready")


@pytest.mark.parametrize("kind", ["example", "share"])
def test_http_requests_and_receipts_are_independent_per_tab(context, site, kind):
    from playwright.sync_api import expect

    calls = mock_api(context)
    owner = context.new_page()
    owner.goto(site + "/chat")
    ready(owner)
    owner.locator("#prompt-input").fill("Original draft")
    current = context.new_page()
    current.goto(entry_url(site, kind=kind))
    ready(current)
    current.wait_for_function("!workspaceTest.workspace.pending")
    expect(owner.locator("#prompt-input")).to_have_value("Original draft")
    expect(owner).to_have_url(site + "/chat")
    current.locator("#prompt-input").fill("Independent draft")
    owner.goto(entry_url(site, kind=kind))
    dialog = owner.locator("#workspace-entry-dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Open request").click()
    owner.wait_for_function("!workspaceTest.workspace.pending")
    if kind == "example":
        expect(owner.locator("#prompt-input")).to_have_value("Incoming research question")
    else:
        expect(owner.locator("#chat-title")).to_have_text("Shared research")
        assert sum(path == "/api/chat-shares/import" for _, path, _ in calls) == 2
    owner.locator("#prompt-input").fill("Keep after duplicate request")
    owner.goto(entry_url(site, kind=kind))
    expect(dialog).to_be_hidden()
    expect(owner.locator("#prompt-input")).to_have_value("Keep after duplicate request")
    expect(current.locator("#prompt-input")).to_have_value("Independent draft")
    assert not any(path.endswith("/turns") for _, path, _ in calls)


def test_http_chat_does_not_require_indexeddb_or_channel(context, site):
    calls = mock_api(context)
    context.add_init_script("""
      Object.defineProperty(window, 'indexedDB', {value:undefined});
      Object.defineProperty(window, 'BroadcastChannel', {value:undefined});
    """)
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    assert starts(calls) == 1


def test_http_logout_and_account_change(context, site):
    check_logout(context, site)

"""Real Lite UI, mocked APIs; no production reset is performed."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from interface.test_agent_examples import mock_api, site as static_site  # noqa: F401
from interface.test_chat_workspace_browser import browser, ready, screenshot  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv('POTATO_WORKSPACE_BROWSER_TESTS') != '1', reason='Opt-in Playwright suite')


@pytest.fixture(scope='module', params=['loopback', 'http'])
def site(request, static_site):
    # Loopback counts as a secure context in browsers even when served over HTTP.
    # workspace.test resolves to loopback, but has the same API restrictions as
    # the deployed HTTP site.
    return static_site.replace('127.0.0.1', 'workspace.test') if request.param == 'http' else static_site


@pytest.fixture
def context(browser):
    with browser.new_context(viewport={'width': 1440, 'height': 1000}) as context:
        source = (Path(__file__).parent / 'static/lite/app.js').read_text() + '\nwindow.workspaceTest = {state, workspace};'
        context.route('**/static/lite/app.js*', lambda route: route.fulfill(body=source, content_type='text/javascript'))
        context.route_web_socket('**/api/tui/ws*', lambda ws: ws.close())
        yield context


def setup(context, *, enabled=True, active=False):
    mock_api(context)
    job = {'job_id': 'a' * 32, 'generation': 1, 'status': 'running', 'phase': 'stopping', 'error_code': ''}
    user = {'id': 'example-test', 'username': 'example-test', 'name': 'Research workspace',
            'workspace_reset': {'enabled': enabled, 'generation': 0, 'job': job if active else None}}
    context.route('**/api/auth/session', lambda route: route.fulfill(json={'authenticated': True, 'user': user}))
    context.route('**/api/runtime/start', lambda route: route.fulfill(json={'user': user}))
    calls = []
    def reset_api(route):
        path = route.request.url.split('/api/workspace/reset')[1]
        if path == '/prepare':
            route.fulfill(json={'token': 'test-confirmation-token', 'expires_in': 300})
        elif not path:
            calls.append(route.request.post_data_json)
            user['workspace_reset'].update(generation=1, job=job)
            route.fulfill(status=202, json=job)
        else:
            route.fulfill(json=job)
    context.route('**/api/workspace/reset**', reset_api)
    return job, user, calls


def test_explicit_confirmation_cancel_and_double_click(context, site):
    from playwright.sync_api import expect
    job, user, calls = setup(context)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(site + '/chat')
    ready(page)
    if 'workspace.test' in site:
        assert page.evaluate('window.isSecureContext') is False
        assert page.evaluate('typeof crypto.randomUUID') == 'undefined'
    page.locator('.sidebar-settings-button').click()
    page.locator('#sidebar-reset-workspace-button').click()
    expect(page.locator('#workspace-reset-dialog')).to_be_visible()
    expect(page.locator('#workspace-reset-submit')).to_be_disabled()
    expect(page.locator('#workspace-reset-cancel')).to_be_focused()
    screenshot(page, 'workspace-reset-confirmation')
    page.locator('#workspace-reset-confirmation').fill('reset')
    expect(page.locator('#workspace-reset-submit')).to_be_disabled()
    page.locator('#workspace-reset-cancel').click()
    assert calls == []
    page.locator('.sidebar-settings-button').click()
    page.locator('#sidebar-reset-workspace-button').click()
    page.locator('#workspace-reset-confirmation').fill('RESET')
    expect(page.locator('#workspace-reset-submit')).to_be_enabled()
    page.locator('#workspace-reset-submit').click()
    expect(page.locator('#workspace-reset-warning')).to_be_hidden()
    expect(page.locator('#workspace-reset-status')).to_contain_text('Stopping')
    assert len(calls) == 1
    assert calls[0]['confirmation'] == 'RESET'
    assert re.fullmatch(r'[a-zA-Z0-9-]{16,64}', calls[0]['request_id'])
    page.keyboard.press('Escape')
    expect(page.locator('#workspace-reset-dialog')).to_be_visible()
    screenshot(page, 'workspace-reset-progress')
    job.update(status='succeeded', phase='complete')
    user['workspace_reset']['job'] = None
    expect(page.locator('#workspace-reset-dialog')).not_to_be_visible(timeout=10000)
    ready(page)
    expect(page.locator('#prompt-input')).to_have_value('')
    assert errors == []


def test_refresh_resumes_blocked_reset_and_mobile_layout(context, site):
    from playwright.sync_api import expect
    job, _, calls = setup(context, active=True)
    job.update(status='blocked', phase='cleaning', error_code='reset_failed')
    page = context.new_page()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(site + '/chat')
    expect(page.locator('#workspace-reset-dialog')).to_be_visible()
    expect(page.locator('#workspace-reset-status')).to_contain_text('locked')
    expect(page.locator('#workspace-reset-cancel')).to_have_text('Back to home')
    box = page.locator('#workspace-reset-dialog').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 391
    screenshot(page, 'workspace-reset-mobile-blocked')
    page.reload()
    expect(page.locator('#workspace-reset-status')).to_contain_text(job['job_id'])
    assert calls == []


def test_unverified_account_has_no_reset_action(context, site):
    from playwright.sync_api import expect
    setup(context, enabled=False)
    page = context.new_page()
    page.goto(site + '/chat')
    ready(page)
    page.locator('.sidebar-settings-button').click()
    expect(page.locator('#sidebar-reset-workspace-button')).to_be_hidden()

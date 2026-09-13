"""Browser regressions: POTATO_EXAMPLE_BROWSER_TESTS=1 python -m pytest this_file.

Requires Playwright and Chromium. All APIs are mocked; no runtime is started.
POTATO_EXAMPLE_SCREENSHOTS optionally retains desktop and mobile screenshots.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest


STATIC = Path(__file__).parent / "static"
DEFAULT_GENE = "DM8.2_chr05G25210"
INTRO = "Query and analyze PotatoOmics data, or search beyond the database for more information."
PAGES = {
    "/genes": "genes", "/bulk-rnaseq": "bulk_rnaseq", "/wgcna": "wgcna",
    "/spatial": "spatial", "/genomes": "genomes", "/genomes/browser": "genome_browser",
}
browser_test = pytest.mark.skipif(
    os.getenv("POTATO_EXAMPLE_BROWSER_TESTS") != "1",
    reason="Opt-in browser suite; requires Playwright and Chromium",
)


def test_templates_and_one_time_navigation_contract():
    if not shutil.which("node"):
        pytest.skip("Node.js is required")
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = { window: {
  location: {hash:'', pathname:'/lite', search:''},
  history: {replaceState: () => { context.window.location.hash = ''; }},
} };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const e = context.window.PotatoAgentExamples;
for (const page of ['genes', 'bulk_rnaseq', 'wgcna', 'spatial', 'genomes', 'genome_browser']) {
  const text = e.build(page);
  assert.ok(!/[{}]/.test(text));
  assert.ok(!/family/i.test(text));
}
assert.match(e.build('wgcna', {genes:['A', 'B', 'A']}), /each of the query genes A, B/);
assert.match(e.build('wgcna', {genes:['A', 'B']}), /for each query gene/);
assert.match(e.build('bulk_rnaseq', {genes:['A','B']}), /the genes A, B across tissues/);
assert.match(e.build('genes', {genes:[null,'']}), /DM8.2_chr05G25210/);
assert.match(e.build('spatial'), /Dataset: Stolon and tuber \(s1_s2\); sample: Stolon \(S1\)\./);
assert.match(e.build('spatial', {genes:['G'],dataset:'D',sample:'S'}), /for G.*Dataset: D; sample: S\./);
assert.match(e.build('genome_browser', {genes:['OTHER']}), /DM8.2_chr05G25210 from DMv8.2/);
context.window.location.hash = '#example=' + encodeURIComponent(JSON.stringify({page:'genes',text:'Example text'}));
assert.equal(e.takeFromLocation().text, 'Example text');
assert.equal(context.window.location.hash, '');
assert.equal(e.takeFromLocation(), null);
context.window.location.hash = '#example=%invalid'; assert.equal(e.takeFromLocation(), null);
assert.equal(context.window.location.hash, '');
context.window.location.hash = '#share=share-token'; assert.equal(e.takeFromLocation(), null);
assert.equal(context.window.location.hash, '#share=share-token');
"""
    subprocess.run(["node", "-e", script, str(STATIC / "shared/agent-examples.js")], check=True)


class StaticHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in PAGES or path in {"/", "/lite", "/chat"} or path.startswith("/genes/"):
            folder = PAGES.get(path, "genes" if path.startswith("/genes/") else "lite")
            self.path = f"/{folder}/index.html"
        elif path.startswith("/static/"):
            self.path = self.path.removeprefix("/static")
        super().do_GET()

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(StaticHandler, directory=str(STATIC)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        instance = driver.chromium.launch(
            executable_path=os.getenv("POTATO_PLAYWRIGHT_EXECUTABLE") or None,
            args=["--no-sandbox"],
        )
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    with browser.new_context(viewport={"width": 1440, "height": 1000}) as context:
        yield context.new_page()


def mock_api(page, *, authenticated=True, sessions=None, user_id="example-test"):
    user = {"id": user_id, "username": user_id, "name": "Research workspace"}
    calls = []
    sessions = sessions or []

    def handle(route):
        nonlocal authenticated
        request = route.request
        path = urlsplit(request.url).path
        calls.append((request.method, path, request.post_data))
        if path in {"/api/auth/signin", "/api/auth/temporary"}:
            authenticated = True
            route.fulfill(json=user)
            return
        if path == "/api/auth/signout":
            authenticated = False
            route.fulfill(json={})
            return
        responses = {
            "/api/auth/session": {"authenticated": authenticated, "user": user},
            "/api/runtime/start": {"user": user},
            "/api/models": {"data": [{"id": "test-model", "name": "Test model"}]},
            "/api/sessions": {"sessions": sessions},
            "/api/files/tree": {"root": "/", "path": "/", "entries": []},
            "/api/files/config": {"mode": "home_only", "home": "/"},
            "/api/daily-updates": {"items": [], "hasMore": False},
            "/api/legal/agreement": {"version": "test", "url": "/terms", "sha256": "test"},
            "/api/spatial/datasets": {"datasets": []},
            "/api/genome-browser/assemblies": {"assemblies": []},
            "/api/v1/genes/CURRENT": {"gene": {"geneId": "DM8.2_chr01G00060"}},
        }
        if path.endswith("/turns"):
            sessions[:] = [{"id": "sent", "title": "Sent example", "message_count": 1}]
            responses[path] = {
                "session": sessions[0],
                "messages": [{"role": "user", "content": request.post_data_json["prompt"]}],
            }
        elif path == "/api/chat-shares/import":
            responses[path] = {"session": {"id": "shared", "title": "Shared research"}}
        elif path.startswith("/api/sessions/"):
            session_id = path.split("/")[3]
            responses[path] = {
                "session": {"id": session_id, "title": "Existing research", "message_count": 1},
                "messages": [{"role": "user", "content": "Existing question"}],
            }
        route.fulfill(json=responses.get(path, {}))

    page.route("**/api/**", handle)
    return calls


def wait_workspace(page):
    from playwright.sync_api import expect

    expect(page.locator("#workspace-view")).to_be_visible()
    expect(page.locator("#workspace-view")).to_have_attribute("aria-busy", "false")
    expect(page.locator("#chat-title")).to_have_text("New chat")


def snapshot(page, name):
    directory = os.getenv("POTATO_EXAMPLE_SCREENSHOTS")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(directory) / f"{name}.png"), full_page=True)


def assert_fits(page, selector):
    assert page.locator(selector).evaluate("""el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.left >= 0 && r.right <= innerWidth + 1
        && el.scrollWidth <= el.clientWidth + 1;
    }""")


@browser_test
@pytest.mark.parametrize("path,module", PAGES.items())
def test_page_handoff_defaults_and_no_automatic_turn(page, site, path, module):
    from playwright.sync_api import expect

    calls = mock_api(page, sessions=[{"id": "old", "title": "Existing research"}])
    page.goto(site + path)
    button = page.get_by_role("button", name="Ask Potato Agent", exact=True)
    expect(button).to_have_count(1)
    expect(button.locator("img")).to_have_count(0)
    assert_fits(page, "#ask-potato-agent")
    snapshot(page, module + "-desktop")
    expected = page.evaluate("name => PotatoAgentExamples.build(name)", module)
    if path in {"/bulk-rnaseq", "/wgcna"}:
        page.locator("#genes-input").fill("UNSUBMITTED")
    elif path == "/genes":
        page.locator("#gene-search-input").fill("UNSUBMITTED")
    elif path == "/spatial":
        page.locator("#geneInput").fill("UNSUBMITTED")
    button.click()
    wait_workspace(page)
    expect(page.locator("#prompt-input")).to_have_value(expected)
    expect(page.locator("#composer-example-label")).to_be_visible()
    expect(page.locator(".research-example")).to_have_count(0)
    expect(page.locator(".conversation-intro-copy")).to_have_text(INTRO)
    expect(page).to_have_url(site + "/chat")
    assert page.evaluate("Object.keys(sessionStorage).filter(key => key.includes('example') || key.includes('draft'))") == []
    assert not any(path.endswith("/turns") for _, path, _ in calls)
    page.locator("#prompt-input").fill("Edited research question")
    expect(page.locator("#composer-example-label")).to_be_visible()
    page.reload()
    expect(page.locator("#composer-example-label")).to_be_hidden()
    expect(page.locator("#prompt-input")).to_have_value("")


@browser_test
@pytest.mark.parametrize("module", ["genes", "bulk_rnaseq", "wgcna", "spatial"])
def test_loaded_context_ignores_unsubmitted_input(page, site, module):
    from playwright.sync_api import expect

    mock_api(page)
    path = next(path for path, name in PAGES.items() if name == module)
    page.goto(site + ("/genes/CURRENT" if module == "genes" else path))
    if module == "genes":
        expect(page.locator("#gene-detail-content")).to_be_visible()
        expected_gene = "DM8.2_chr01G00060"
    elif module == "bulk_rnaseq":
        page.evaluate("state.payload = {genes: [{geneId:'GENE_A'}, {geneId:'GENE_B'}]}")
        page.locator("#genes-input").fill("UNSUBMITTED")
        expected_gene = "the genes GENE_A, GENE_B"
    elif module == "wgcna":
        page.evaluate("state.lastPayload = {query_genes:['GENE_A','GENE_B'], elements:{nodes:[{data:{gene_id:'NEIGHBOR'}}]}}")
        page.locator("#genes-input").fill("UNSUBMITTED")
        expected_gene = "each of the query genes GENE_A, GENE_B"
    else:
        page.evaluate("""() => {
          state.currentDataset = {id:'loaded',label:'Loaded dataset',defaultSample:'S1',
            samples:[{id:'S1',label:'Sample one'},{id:'S2',label:'Sample two'}]};
          state.currentSample = 'S2'; state.currentGene = 'LOADED_GENE';
          state.samples.S2 = {};
        }""")
        page.locator("#geneInput").fill("UNSUBMITTED")
        expected_gene = "LOADED_GENE"
    page.locator("#ask-potato-agent").click()
    wait_workspace(page)
    text = page.locator("#prompt-input").input_value()
    assert expected_gene in text
    assert "UNSUBMITTED" not in text and "NEIGHBOR" not in text
    if module == "spatial":
        assert "Dataset: Loaded dataset (loaded); sample: Early Swelling Tuber (S2)." in text


@browser_test
@pytest.mark.parametrize("auth", ["signin", "temporary"])
@pytest.mark.parametrize("refresh", [False, True])
def test_initial_login_receives_unacknowledged_example_after_refresh(page, site, auth, refresh):
    from playwright.sync_api import expect

    calls = mock_api(page, authenticated=False)
    page.goto(site + "/genes")
    page.locator("#ask-potato-agent").click()
    expect(page.locator("#login-view")).to_be_visible()
    expect(page).to_have_url(site + "/chat")
    if refresh:
        page.reload()
        expect(page.locator("#login-view")).to_be_visible()
    if auth == "signin":
        page.locator("#show-login-button").click()
        page.locator("#email").fill("research@example.com")
        page.locator("#password").fill("Example123!")
        page.locator('#login-form button[type="submit"]').click()
    else:
        page.locator("#show-temporary-button").click()
        page.locator("#temporary-agreement-checkbox").check()
        page.locator("#temporary-confirm-start").click()
    wait_workspace(page)
    expect(page.locator("#prompt-input")).to_have_value(page.evaluate("PotatoAgentExamples.build('genes')"))
    expect(page.locator("#composer-example-label")).to_be_visible()
    assert not any(path.endswith("/turns") for _, path, _ in calls)


@browser_test
def test_latest_example_replaces_input_and_label_lasts_until_send(page, site):
    from playwright.sync_api import expect

    calls = mock_api(page)
    page.goto(site + "/chat")
    wait_workspace(page)
    expect(page.locator(".conversation-intro-copy")).to_have_text(INTRO)
    expect(page.get_by_text("More examples", exact=True)).to_have_count(0)
    page.locator("#prompt-input").fill("My unsent draft")
    page.goto(site + "/wgcna")
    page.locator("#ask-potato-agent").click()
    wait_workspace(page)
    assert "top 25 co-expression neighbors" in page.locator("#prompt-input").input_value()
    expect(page.locator("#example-conflict")).to_have_count(0)
    expect(page.locator("#send-button")).to_be_enabled()
    page.locator("#prompt-input").fill("Edited network example")
    page.goto(site + "/genes")
    page.locator("#ask-potato-agent").click()
    wait_workspace(page)
    assert page.locator("#prompt-input").input_value().startswith("Summarize the functional annotations")
    page.locator("#prompt-input").fill("")
    expect(page.locator("#composer-example-label")).to_be_visible()
    edited = "Summarize the evidence for " + DEFAULT_GENE + "."
    page.locator("#prompt-input").fill(edited)
    expect(page.locator("#composer-example-label")).to_be_visible()
    page.locator("#send-button").click()
    expect(page.locator("#chat-title")).to_have_text("Sent example")
    expect(page.locator("#composer-example-label")).to_be_hidden()
    page.locator("#prompt-input").fill("A normal follow-up")
    expect(page.locator("#composer-example-label")).to_be_hidden()
    turns = [json.loads(body) for _, path, body in calls if path.endswith("/turns")]
    assert len(turns) == 1 and turns[0]["prompt"] == edited
    assert "Example:" not in turns[0]["prompt"]
    page.reload()
    expect(page.locator("#prompt-input")).to_have_value("")
    expect(page.locator("#composer-example-label")).to_be_hidden()
    page.locator("#new-chat-button").click()
    expect(page.locator(".conversation-intro-copy")).to_have_text(INTRO)


@browser_test
def test_existing_conversation_and_share_import_do_not_show_example(page, site):
    from playwright.sync_api import expect

    mock_api(page, sessions=[{"id": "old", "title": "Existing research"}])
    page.goto(site + "/chat")
    expect(page.locator("#chat-title")).to_have_text("Existing research")
    page.locator("#prompt-input").fill("Follow up on my existing research")
    page.goto(site + "/genes")
    page.locator("#ask-potato-agent").click()
    wait_workspace(page)
    expect(page.locator("#composer-example-label")).to_be_visible()
    page.get_by_text("Existing research", exact=True).click()
    expect(page.locator("#chat-title")).to_have_text("Existing research")
    expect(page.locator("#prompt-input")).to_have_value("")
    expect(page.locator("#composer-example-label")).to_be_hidden()
    page.goto(site + "/lite#share=" + "a" * 48)
    expect(page.locator("#chat-title")).to_have_text("Shared research")
    expect(page.locator("#prompt-input")).to_have_value("")
    expect(page.locator(".research-example")).to_have_count(0)
    page.reload()
    expect(page.locator("#prompt-input")).to_have_value("")
    expect(page.locator("#composer-example-label")).to_be_hidden()


@browser_test
def test_unavailable_model_preserves_current_input_without_caching(page, site):
    from playwright.sync_api import expect

    calls = mock_api(page)
    page.goto(site + "/chat")
    wait_workspace(page)
    expect(page.locator(".conversation-intro-copy")).to_have_text(INTRO)
    expect(page.locator("#composer-example-label")).to_be_hidden()
    page.route("**/api/models", lambda route: route.fulfill(json={"data": []}))
    page.reload()
    wait_workspace(page)
    page.locator("#prompt-input").fill("My research question")
    page.locator("#send-button").click()
    expect(page.locator("#chat-error")).to_contain_text("No model")
    expect(page.locator("#prompt-input")).to_have_value("My research question")
    assert not any(path.endswith("/turns") for _, path, _ in calls)
    page.unroute("**/api/**")
    mock_api(page, user_id="another-account")
    page.reload()
    wait_workspace(page)
    expect(page.locator("#prompt-input")).to_have_value("")


@browser_test
def test_signout_and_relogin_discard_example(page, site):
    from playwright.sync_api import expect

    mock_api(page)
    page.goto(site + "/genes")
    page.locator("#ask-potato-agent").click()
    wait_workspace(page)
    expect(page.locator("#composer-example-label")).to_be_visible()
    page.locator(".sidebar-settings-button").click()
    page.get_by_text("Sign out", exact=True).filter(visible=True).click()
    expect(page.locator("#login-view")).to_be_visible()
    page.locator("#show-login-button").click()
    page.locator("#email").fill("research@example.com")
    page.locator("#password").fill("Example123!")
    page.locator('#login-form button[type="submit"]').click()
    wait_workspace(page)
    expect(page.locator("#prompt-input")).to_have_value("")
    expect(page.locator("#composer-example-label")).to_be_hidden()


@browser_test
@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844), (360, 640)])
def test_responsive_composer_and_genes_button(page, site, width, height):
    from playwright.sync_api import expect

    mock_api(page)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(site + "/genes")
    assert_fits(page, "#ask-potato-agent")
    snapshot(page, f"genes-{width}")
    page.locator("#ask-potato-agent").click()
    wait_workspace(page)
    expect(page.locator("#composer-example-label")).to_be_visible()
    assert page.locator("#composer-example-label").evaluate("""label => {
      const input = document.getElementById('prompt-input');
      const box = document.querySelector('.composer-input-wrap');
      const style = getComputedStyle(label);
      const inputStyle = getComputedStyle(input);
      const textLeft = label.getBoundingClientRect().left + parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth);
      const boxRect = box.getBoundingClientRect();
      const gap = boxRect.top - label.getBoundingClientRect().bottom;
      return Math.abs(textLeft - boxRect.left - 12) < 1 && style.fontSize === inputStyle.fontSize
        && Number(style.fontWeight) >= 700 && Math.abs(gap - 6) < 1;
    }""")
    assert_fits(page, "#prompt-input")
    assert_fits(page, "#send-button")
    assert page.locator("#composer-form").evaluate("el => el.getBoundingClientRect().bottom <= innerHeight + 1")
    snapshot(page, f"example-composer-{width}")
    layout = page.locator(".composer-input-wrap").evaluate("""box => {
      const rect = box.getBoundingClientRect();
      return ['prompt-input', 'attach-button', 'plan-button', 'send-button'].map(id => {
        const el = document.getElementById(id);
        const r = el.getBoundingClientRect();
        return [r.left - rect.left, r.bottom - rect.bottom, r.width];
      });
    }""")
    page.goto(site + "/chat")
    wait_workspace(page)
    page.locator("#prompt-input").fill("Normal research question")
    assert page.locator(".composer-input-wrap").evaluate("""box => {
      const rect = box.getBoundingClientRect();
      return ['prompt-input', 'attach-button', 'plan-button', 'send-button'].map(id => {
        const el = document.getElementById(id);
        const r = el.getBoundingClientRect();
        return [r.left - rect.left, r.bottom - rect.bottom, r.width];
      });
    }""") == layout
    page.locator("#prompt-input").fill("")
    page.goto(site + "/chat")
    wait_workspace(page)
    expect(page.locator(".conversation-intro-copy")).to_have_text(INTRO)
    expect(page.locator(".research-example")).to_have_count(0)
    expect(page.get_by_text("More examples", exact=True)).to_have_count(0)
    assert_fits(page, ".conversation-intro-copy")
    assert "potato-agent-icon.png" in page.locator(".conversation-intro").evaluate(
        "el => getComputedStyle(el, '::before').backgroundImage"
    )
    snapshot(page, f"empty-chat-{width}")
    if width < 800:
        page.goto(site + "/spatial")
        expect(page).to_have_url(site + "/static/lite/high-resolution-required.html?module=spatial")

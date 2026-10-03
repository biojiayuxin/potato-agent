"""Markdown regressions use Node; optional browser tests use mocked APIs only.

POTATO_MARKDOWN_BROWSER_TESTS=1 enables Chromium integration tests.
POTATO_WORKSPACE_SCREENSHOTS optionally retains desktop/mobile screenshots.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from interface.test_chat_workspace_browser import (  # noqa: F401
    browser, context, mock_api, ready, screenshot, site, static_site,
)


STATIC = Path(__file__).parent / "static/lite"
FORMULA = (
    "[\n\t\\text{位点甲基化水平}\n"
    "\t=\\frac{\\text{支持甲基化的reads数}}{\\text{覆盖该位点的有效reads总数}}\n\t]"
)
BROWSER_TEST = pytest.mark.skipif(
    os.getenv("POTATO_MARKDOWN_BROWSER_TESTS") != "1",
    reason="Opt-in Markdown browser tests; requires Playwright and Chromium",
)


def render_sources(sources: list[str]) -> list[str]:
    if not shutil.which("node"):
        pytest.skip("Node.js is required")
    script = r"""
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import fs from 'node:fs';
const root = process.argv[1];
const require = createRequire(pathToFileURL(root + '/markdown.js'));
const {createMarkdownRenderer} = await import(pathToFileURL(root + '/markdown.js'));
const render = createMarkdownRenderer({
  marked: require(root + '/vendor/marked.umd.js'),
  katex: require(root + '/vendor/katex/katex.min.js'),
  sanitize: html => html, // The real DOM sanitizer is exercised in browser tests.
  escapeHtml: text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
});
process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0, 'utf8')).map(render)));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(STATIC.resolve())],
        input=json.dumps(sources), capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(("source", "expected"), [
    ("这是**注意：**正文", "这是<strong>注意：</strong>正文"),
    ("这是**：说明**正文", "这是<strong>：说明</strong>正文"),
    ("这是**—说明—**正文", "这是<strong>—说明—</strong>正文"),
    ("这是**说明——**正文", "这是<strong>说明——</strong>正文"),
    ("**说明：内容**", "<strong>说明：内容</strong>"),
    ("**加粗 *斜体*：**正文", "<strong>加粗 <em>斜体</em>：</strong>正文"),
    ("**说明 `a**b`：**正文", "<strong>说明 <code>a**b</code>：</strong>正文"),
    ("***说明：***正文", "<em><strong>说明：</strong></em>正文"),
    ("**甲：**和**乙—**正文", "<strong>甲：</strong>和<strong>乙—</strong>正文"),
    ("[**报告：**](https://example.com)", '<a href="https://example.com"><strong>报告：</strong></a>'),
])
def test_cjk_emphasis_preserves_nested_markdown(source, expected):
    assert render_sources([source]) == [f"<p>{expected}</p>\n"]


@pytest.mark.parametrize("source", [
    FORMULA,
    FORMULA.replace("[", "\\[", 1).replace("]", "\\]"),
    "$$\\frac{a_i}{b_j}$$",
    r"行内 \(x_i^2 + y_i^2\) 公式",
    r"行内 $x_i^2$ 公式",
    r"**公式：\(x_i^2\)**正文",
    "公式如下：\n" + FORMULA + "\n结束。",
    "> \\[\n> \\frac{1}{2}\n> \\]",
    "- \\[\n  \\frac{1}{2}\n  \\]",
    r"\[\begin{aligned} a_i &= \frac{b_i}{c_i} \\ d &= \sqrt{x} \end{aligned}\]",
    r"\(\text{括号 ) 与美元 \$} + x\)",
])
def test_math_is_parsed_before_markdown_escapes_and_emphasis(source):
    html = render_sources([source])[0]
    assert 'class="katex"' in html
    assert '<annotation encoding="application/x-tex">' in html
    assert "potato-math-" not in html
    if "位点甲基化水平" in source:
        assert "<mtext>位点甲基化水平</mtext>" in html
        assert "<mfrac>" in html


@pytest.mark.parametrize("source", [
    r"`**注意：** \(x_i\) $x$`",
    "```tex\n" + FORMULA + "\n**注意：**\n```",
    "    \\[x^2\\]\n    **注意：**",
    r"<code>\(x_i\) $x$</code>",
    "<pre>\\[x^2\\]</pre>",
    r"\$x$ and \\[ordinary brackets\\]",
    "Prices: $5 and $10; escaped \\$20.",
    "[\n1, 2, 3\n]",
    "[report]: https://example.com\n\n[report]",
    "$$unfinished",
    r"\[\frac{1}{",
])
def test_code_currency_brackets_and_partial_streams_stay_literal(source):
    html = render_sources([source])[0]
    assert 'class="katex"' not in html
    assert "potato-math-" not in html
    if "**注意：**" in source:
        assert "**注意：**" in html
        assert "<strong>" not in html


def test_invalid_math_falls_back_and_later_formulas_still_render():
    source = r"\(\unknowncommand{<img src=x onerror=alert(1)>}\) then \(x^2\)"
    html = render_sources([source])[0]
    assert r"\unknowncommand" in html
    assert "&lt;img" in html
    assert "<img" not in html
    assert html.count('class="katex"') == 1


def test_code_block_blank_lines_are_preserved():
    assert "a\n\n\nb" in render_sources(["```\na\n\n\nb\n```"])[0]


def set_messages(page, content, *, streaming=False):
    page.evaluate("""({content, streaming}) => {
      const t = workspaceTest;
      t.state.messages = [{id: 'markdown-regression', role: 'assistant', content, streaming}];
      t.renderWorkspace();
    }""", {"content": content, "streaming": streaming})


@BROWSER_TEST
@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "mobile"])
def test_chat_renders_cjk_and_formula_with_local_assets(context, site, mobile):
    from playwright.sync_api import expect

    mock_api(context)
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844} if mobile else {"width": 1440, "height": 1000})
    errors, failed_assets = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("response", lambda response: failed_assets.append(response.url) if response.status >= 400 and "/static/lite/" in response.url else None)
    page.goto(site + "/chat")
    ready(page)
    content = "\n\n".join([
        "这是**注意：**正文；这是**—说明—**正文。",
        "**加粗 *斜体*：**正文；**说明 `a**b`：**正文。",
        FORMULA,
        r"行内公式：\(x_i^2 + y_i^2\)，以及 **比例：$\frac{a}{b}$**。",
        "```tex\n\\[x_i^2\\]\n```",
    ])
    set_messages(page, content)
    message = page.locator("#messages .message-content")
    expect(message.locator("strong").first).to_have_text("注意：")
    expect(message.locator("strong em")).to_have_text("斜体")
    expect(message.locator(".katex")).to_have_count(3)
    expect(message.locator(".katex-display mfrac")).to_have_count(1)
    expect(message.locator("pre code")).to_have_text(r"\[x_i^2\]" + "\n")
    page.evaluate("document.fonts.ready")
    expect(message.locator(".katex-html .cjk_fallback").first).to_be_visible()
    assert page.evaluate("[...document.fonts].every(font => font.status !== 'error')")
    assert not failed_assets
    assert not errors
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert message.locator(".markdown-math-display").evaluate("node => node.clientWidth <= node.parentElement.clientWidth")
    screenshot(page, f"markdown-{'mobile' if mobile else 'desktop'}")
    if mobile:
        # A wide single fraction must scroll from its beginning to its end,
        # without centering its left edge outside the scrollable area.
        set_messages(page, r"\[\frac{\text{很长的分子内容用来验证手机屏幕上的横向滚动显示}}{\text{分母}}\]")
        display = message.locator(".markdown-math-display")
        assert display.evaluate("""node => {
          const math = node.querySelector('.katex-html');
          const start = math.getBoundingClientRect().left;
          const left = node.getBoundingClientRect().left;
          if (start < left - 1 || node.scrollWidth <= node.clientWidth) return false;
          node.scrollLeft = node.scrollWidth;
          return math.getBoundingClientRect().right <= node.getBoundingClientRect().right + 1;
        }""")


@BROWSER_TEST
def test_stream_updates_replace_cached_partial_markdown(context, site):
    from playwright.sync_api import expect

    mock_api(context)
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    set_messages(page, r"**注意：**正文 \(\frac{a", streaming=True)
    message = page.locator("#messages .message-content")
    expect(message.locator("strong")).to_have_text("注意：")
    expect(message.locator(".katex")).to_have_count(0)
    page.evaluate(r"""() => {
      const t = workspaceTest;
      t.state.messages[0].content += String.raw`}{b}\)`;
      t.state.messages[0].streaming = false;
      t.renderWorkspace();
    }""")
    expect(message.locator(".katex mfrac")).to_have_count(1)


@BROWSER_TEST
def test_math_keeps_html_url_and_workspace_link_safety(context, site):
    from playwright.sync_api import expect

    mock_api(context)
    page = context.new_page()
    page.goto(site + "/chat")
    ready(page)
    attacks = [
        '<script>window.markdownAttack = true</script>',
        '<img src=x onerror="window.markdownAttack = true">',
        '[bad](javascript:alert(1))',
        r"\(\href{javascript:alert(1)}{click}\)",
        r"\(\htmlClass{forged}{x}\)",
        r"\(\includegraphics{https://example.com/track.png}\)",
        r"\(\unknown{<img src=x onerror=alert(1)>}\)",
        r"\(\text{/mnt/data/formula.txt}\)",
        '[**报告：**](sandbox:/mnt/data/report.txt)',
        '[external](https://example.com/report)',
    ]
    set_messages(page, "\n\n".join(attacks))
    message = page.locator("#messages .message-content")
    expect(message.locator("script, img, .forged, [onclick], [onerror], a[href^='javascript:']")).to_have_count(0)
    assert not page.evaluate("Boolean(window.markdownAttack)")
    expect(message.locator("a[data-workspace-path]")).to_have_count(1)
    expect(message.locator("a[data-workspace-path]")).to_have_attribute("data-workspace-path", "/mnt/data/report.txt")
    expect(message.get_by_text("external", exact=True)).to_have_attribute("rel", "noopener noreferrer")
    expect(message.locator(".markdown-math a")).to_have_count(0)

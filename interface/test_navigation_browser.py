"""Run with POTATO_NAVIGATION_BROWSER_TESTS=1 and Playwright Chromium installed.

POTATO_NAVIGATION_SCREENSHOTS sets the directory for review screenshots.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from interface.test_dashboard_browser import browser, site  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv('POTATO_NAVIGATION_BROWSER_TESTS') != '1',
    reason='Opt-in Playwright navigation suite',
)

PAGES = {
    'lite': ('lite', 'Potato Agent', False),
    'genes': ('genes', 'Genes', False),
    'genomes': ('genomes', 'Genomes', True),
    'genomes/browser': ('genome_browser', 'Genome Browser', True),
    'bulk-rnaseq': ('bulk_rnaseq', 'Gene Expression', True),
    'wgcna': ('wgcna', 'WGCNA Network', True),
    'spatial': ('spatial', 'Spatial Expression', True),
    'dashboard': ('dashboard', 'Dashboard', False),
    'about': ('about', 'About', False),
}
LABELS = ['Potato Agent', 'Genomes', 'Genes', 'Gene Expression', 'WGCNA Network',
          'Spatial Expression', 'Dashboard', 'About']


def test_public_pages_at_all_breakpoints(site, browser, tmp_path):
    from playwright.sync_api import expect

    screenshots = Path(os.getenv('POTATO_NAVIGATION_SCREENSHOTS', str(tmp_path)))
    screenshots.mkdir(parents=True, exist_ok=True)
    with browser.new_context() as context:
        page = context.new_page()
        for width in (320, 390, 768, 800, 801, 960, 961, 1440):
            page.set_viewport_size({'width': width, 'height': 900})
            for route, (module, label, restricted) in PAGES.items():
                page.goto(f'{site}/{route}')
                nav = page.locator('.portal-nav')
                if restricted and width <= 800:
                    expect(page).to_have_url(f'{site}/static/lite/high-resolution-required.html?module={module}')
                    expect(page.locator('.high-resolution-message .muted')).to_contain_text(label)
                else:
                    expect(nav).to_have_attribute('data-portal-module', module)
                expect(nav).to_be_visible()
                assert nav.evaluate('e => e.getBoundingClientRect().right <= innerWidth')
                if width <= 960:
                    expect(page.locator('.portal-nav-caption')).to_have_text(label)
                    toggle = page.get_by_role('button', name='Module navigation', exact=True)
                    toggle.click()
                    panel = page.locator('.portal-nav-panel')
                    expect(panel).to_be_visible()
                    assert panel.locator('a').evaluate_all(
                        "nodes => nodes.map(e => e.childNodes[0].textContent)"
                    ) == LABELS
                    expect(panel.locator('[aria-current="page"]')).to_have_count(1)
                    for item in panel.locator('a').all():
                        item.scroll_into_view_if_needed()
                        assert item.evaluate('e => e.scrollWidth <= e.clientWidth')
                    page.keyboard.press('Escape')
                    expect(toggle).to_be_focused()
                    expect(panel).to_be_hidden()
                else:
                    expect(page.locator('.portal-nav-toggle')).to_be_hidden()
                    assert nav.locator('a').evaluate_all(
                        'nodes => nodes.map(e => e.dataset.module)'
                    ) == ['lite', 'genomes', 'genes', 'dashboard', 'about']
                    assert page.locator('.portal-nav-desktop > *').evaluate_all('''nodes =>
                        nodes.slice(1).every((node, index) => {
                            const gap = node.getBoundingClientRect().left - nodes[index].getBoundingClientRect().right;
                            return gap >= 7 && gap <= 9;
                        })''')
                    assert page.locator('.portal-nav-desktop').evaluate('''nav => {
                        const first = nav.firstElementChild.getBoundingClientRect();
                        const last = nav.lastElementChild.getBoundingClientRect();
                        const bounds = nav.getBoundingClientRect();
                        return Math.abs((first.left + last.right) - (bounds.left + bounds.right)) < 2;
                    }''')
                    if module in ('bulk_rnaseq', 'wgcna', 'spatial'):
                        expression = page.get_by_role('button', name='Expression', exact=True)
                        expect(expression).to_have_class('portal-nav-item active')
                        expression.click()
                        expect(page.locator('.portal-nav-panel [aria-current="page"]')).to_contain_text(label)
                        if width == 1440:
                            page.screenshot(path=str(screenshots / f'{module}-dropdown.png'))
                        page.keyboard.press('Escape')
                    else:
                        expected = 'Genomes' if module == 'genome_browser' else label
                        expect(nav.locator('[aria-current="page"]')).to_contain_text(expected)
                if width in (390, 1440):
                    page.screenshot(path=str(screenshots / f'{module}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (route, width)


def test_dropdown_keyboard_device_links_and_layers(site, browser, tmp_path):
    from playwright.sync_api import expect

    with browser.new_context(viewport={'width': 1440, 'height': 900}) as context:
        page = context.new_page()
        page.goto(site + '/genes')
        expression = page.get_by_role('button', name='Expression', exact=True)
        expression.focus()
        page.keyboard.press('Enter')
        page.keyboard.press('ArrowDown')
        expect(page.locator('.portal-nav-panel a').first).to_be_focused()
        page.keyboard.press('End')
        expect(page.locator('.portal-nav-panel a').last).to_be_focused()
        page.keyboard.press('Escape')
        expect(expression).to_be_focused()
        page.keyboard.press('Enter')
        page.keyboard.press('ArrowUp')
        expect(page.locator('.portal-nav-panel a').last).to_be_focused()
        page.keyboard.press('Escape')
        upcoming = page.get_by_role('button', name='Coming soon', exact=True)
        expect(upcoming).to_have_text('Coming soon')
        expect(page.locator('.portal-icon-monitor, .portal-icon-ellipsis')).to_have_count(0)
        upcoming.click()
        expect(page.locator('.portal-nav-panel button')).to_have_count(2)
        expect(page.locator('.portal-nav-panel button')).to_have_text(['Variants', 'Germplasm'])
        for item in page.locator('.portal-nav-panel button').all():
            expect(item).to_be_disabled()
        screenshots = Path(os.getenv('POTATO_NAVIGATION_SCREENSHOTS', str(tmp_path)))
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / 'coming-soon-dropdown.png'))
        page.locator('.portal-hero').click()
        expect(page.locator('.portal-nav-panel')).to_be_hidden()
        page.set_viewport_size({'width': 390, 'height': 844})
        toggle = page.get_by_role('button', name='Module navigation', exact=True)
        toggle.click()
        page.locator('.portal-nav-panel a[data-module="genomes"]').click()
        expect(page).to_have_url(site + '/static/lite/high-resolution-required.html?module=genomes')
        page.goto(site + '/static/lite/high-resolution-required.html?module=%3Cscript%3E')
        expect(page.locator('.portal-nav-caption')).to_have_text('High-resolution device required')
        page.route('**/api/announcement', lambda route: route.fulfill(json={
            'server_time': '2026-09-08T00:00:00Z',
            'announcement': {'id': 'long-navigation-test', 'message': 'Long announcement. ' * 120, 'ends_at': None},
        }))
        page.goto(site + '/genes')
        expect(page.locator('.site-announcement')).to_be_visible()
        toggle.click()
        panel = page.locator('.portal-nav-panel')
        expect(panel).to_be_visible()
        assert panel.evaluate('e => e.getBoundingClientRect().top >= document.querySelector(".portal-nav").getBoundingClientRect().bottom')
        page.locator('.portal-nav-panel a[data-module="about"]').scroll_into_view_if_needed()
        assert panel.evaluate('e => e.getBoundingClientRect().bottom <= innerHeight')
        screenshots = Path(os.getenv('POTATO_NAVIGATION_SCREENSHOTS', str(tmp_path)))
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / 'menu-long-announcement-390.png'))
        page.keyboard.press('Escape')
        page.set_viewport_size({'width': 844, 'height': 390})
        toggle.click()
        assert panel.evaluate('e => e.getBoundingClientRect().bottom <= innerHeight')
        page.screenshot(path=str(screenshots / 'menu-landscape.png'))
        page.keyboard.press('Escape')
        page.locator('#feedback-button').click()
        expect(page.locator('#feedback-modal')).to_be_visible()
        expect(panel).to_be_hidden()
        page.goto(site + '/genomes')
        page.set_viewport_size({'width': 800, 'height': 900})
        expect(page).to_have_url(site + '/static/lite/high-resolution-required.html?module=genomes')

"""Run with POTATO_NAVIGATION_BROWSER_TESTS=1 and Playwright Chromium installed.

POTATO_NAVIGATION_SCREENSHOTS sets the directory for review screenshots.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from interface.test_dashboard_browser import browser, site  # noqa: F401
from interface.test_agent_examples import mock_api

pytestmark = pytest.mark.skipif(
    os.getenv('POTATO_NAVIGATION_BROWSER_TESTS') != '1',
    reason='Opt-in Playwright navigation suite',
)

PAGES = {
    'lite': ('lite', 'Potato Agent', False),
    'genes': ('genes', 'Genes', False),
    'pan-genome': ('pan_genome', 'Pan-genome', True),
    'genome-browser': ('genome_browser', 'Genome Browser', True),
    'bulk-rnaseq': ('bulk_rnaseq', 'Gene Expression', True),
    'efp': ('efp', 'Tissue Expression Map', False),
    'wgcna': ('wgcna', 'WGCNA Network', True),
    'spatial': ('spatial', 'Spatial Expression', True),
    'dashboard': ('dashboard', 'Dashboard', False),
    'about': ('about', 'About', False),
}
LABELS = ['Potato Agent', 'Pan-genome', 'Genome Browser', 'Genes', 'Gene Expression', 'Tissue Expression Map', 'WGCNA Network',
          'Spatial Expression', 'Dashboard', 'About']


@pytest.mark.parametrize('width', [390, 1440])
@pytest.mark.parametrize('authenticated', [False, True])
def test_potato_agent_navigation_returns_to_portal(site, browser, width, authenticated):
    from playwright.sync_api import expect

    with browser.new_context(viewport={'width': width, 'height': 900}) as context:
        calls = mock_api(context, authenticated=authenticated)
        page = context.new_page()
        for route in PAGES:
            if route == 'lite':
                continue
            page.goto(f'{site}/{route}')
            if width <= 960:
                page.get_by_role('button', name='Module navigation', exact=True).click()
            link = page.get_by_role('link', name='Potato Agent', exact=True)
            expect(link).to_have_attribute('href', '/lite')
            link.click()
            expect(page).to_have_url(site + '/lite')
            expect(page.locator('#workspace-view')).to_be_hidden()
            if authenticated:
                expect(page.locator('#portal-account-name')).to_have_text('Research workspace')
            else:
                expect(page.locator('#show-login-button')).to_be_visible()

        page.goto(site + '/static/lite/high-resolution-required.html?module=genomes')
        page.get_by_role('link', name='Back to Potato Agent', exact=True).click()
        expect(page).to_have_url(site + '/lite')
        expect(page.locator('#workspace-view')).to_be_hidden()
        assert not any(path.startswith('/api/runtime') for _, path, _ in calls)


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
                    expect(panel.locator('.portal-nav-group-label')).to_have_text(
                        ['Genomes', 'Expression', 'Coming soon']
                    )
                    expect(panel.locator('[aria-current="page"]')).to_have_count(1)
                    expect(panel.locator('[aria-current="page"]')).to_have_attribute('data-module', module)
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
                    ) == ['lite', 'genes', 'dashboard', 'about']
                    expect(page.locator('.portal-nav-desktop > *')).to_have_text(
                        ['Potato Agent', 'Genomes', 'Genes', 'Expression', 'Coming soon', 'Dashboard', 'About']
                    )
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
                    group = ('Genomes' if module in ('pan_genome', 'genome_browser') else
                             'Expression' if module in ('bulk_rnaseq', 'efp', 'wgcna', 'spatial') else None)
                    if group:
                        control = page.get_by_role('button', name=group, exact=True)
                        expect(control).to_have_class('portal-nav-item active')
                        expect(nav.locator('[aria-current="page"]')).to_have_count(0)
                        control.click()
                        active = page.locator('.portal-nav-panel [aria-current="page"]')
                        expect(active).to_have_count(1)
                        expect(active).to_have_text(label)
                        expect(active).to_have_attribute('data-module', module)
                        if width == 1440:
                            page.screenshot(path=str(screenshots / f'{module}-dropdown.png'))
                        page.keyboard.press('Escape')
                    else:
                        expect(nav.locator('[aria-current="page"]')).to_have_text(label)
                if width in (390, 1440):
                    page.screenshot(path=str(screenshots / f'{module}-{width}.png'))
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (route, width)


def test_dropdown_keyboard_device_links_and_layers(site, browser, tmp_path):
    from playwright.sync_api import expect

    with browser.new_context(viewport={'width': 1440, 'height': 900}) as context:
        page = context.new_page()
        page.goto(site + '/genes')
        panel = page.locator('.portal-nav-panel')
        for group, labels in [('Genomes', ['Pan-genome', 'Genome Browser']),
                              ('Expression', LABELS[4:8])]:
            control = page.get_by_role('button', name=group, exact=True)
            control.focus()
            page.keyboard.press('Enter')
            expect(control).to_have_attribute('aria-expanded', 'true')
            expect(panel.locator('a')).to_have_text(labels)
            page.keyboard.press('ArrowDown')
            expect(panel.locator('a').first).to_be_focused()
            page.keyboard.press('End')
            expect(panel.locator('a').last).to_be_focused()
            page.keyboard.press('Home')
            expect(panel.locator('a').first).to_be_focused()
            page.keyboard.press('Escape')
            expect(control).to_be_focused()
            expect(control).to_have_attribute('aria-expanded', 'false')
            page.keyboard.press('Enter')
            page.keyboard.press('ArrowUp')
            expect(panel.locator('a').last).to_be_focused()
            page.keyboard.press('ArrowDown')
            expect(panel.locator('a').first).to_be_focused()
            page.locator('.portal-hero').click()
            expect(panel).to_be_hidden()
            expect(control).to_have_attribute('aria-expanded', 'false')

        genomes = page.get_by_role('button', name='Genomes', exact=True)
        genomes.click()
        expect(panel.locator('a').first).to_have_attribute('href', '/pan-genome')
        expect(panel.locator('a').last).to_have_attribute('href', '/genome-browser')
        page.get_by_role('button', name='Expression', exact=True).click()
        expect(genomes).to_have_attribute('aria-expanded', 'false')
        expect(panel.locator('a')).to_have_text(LABELS[4:8])
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
        page.locator('.portal-nav-panel a[data-module="pan_genome"]').click()
        expect(page).to_have_url(site + '/static/lite/high-resolution-required.html?module=pan_genome')
        toggle.click()
        page.locator('.portal-nav-panel a[data-module="genome_browser"]').click()
        expect(page).to_have_url(site + '/static/lite/high-resolution-required.html?module=genome_browser')
        page.goto(site + '/static/lite/high-resolution-required.html?module=genomes')
        expect(page.locator('.portal-nav-caption')).to_have_text('Pan-genome')
        expect(page.locator('.high-resolution-message .muted')).to_contain_text('Pan-genome')
        toggle.click()
        expect(panel.locator('[aria-current="page"]')).to_have_attribute('data-module', 'pan_genome')
        page.keyboard.press('Escape')
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
        page.goto(site + '/pan-genome')
        page.set_viewport_size({'width': 800, 'height': 900})
        expect(page).to_have_url(site + '/static/lite/high-resolution-required.html?module=pan_genome')


def test_pan_genome_overview_image_and_peer_navigation(site, browser, tmp_path):
    from playwright.sync_api import expect

    screenshots = Path(os.getenv('POTATO_NAVIGATION_SCREENSHOTS', str(tmp_path)))
    screenshots.mkdir(parents=True, exist_ok=True)
    with browser.new_context(viewport={'width': 1440, 'height': 1000}) as context:
        page = context.new_page()
        page.route('**/api/genome-browser/assemblies', lambda route: route.fulfill(json={
            'assemblies': [
                {'id': 'phased_tetraploid/Test-C', 'sample': 'Test C', 'ploidy': 'phased_tetraploid', 'doi': ''},
                {'id': 'monoploid/Test-A', 'sample': 'Test A', 'ploidy': 'monoploid', 'doi': '10.1234/test.a'},
                {'id': 'phased_diploid/Test-B', 'sample': 'Test B', 'ploidy': 'phased_diploid', 'doi': '10.1234/test.b'},
            ],
        }))
        page.goto(site + '/pan-genome')
        expect(page).to_have_title('Pan-genome | Potato Research')
        expect(page.get_by_role('heading', name='Pan-genome', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Ask Potato Agent', exact=True)).to_be_visible()
        expect(page.locator('main').get_by_role('link', name='Genome Browser', exact=True)).to_have_count(0)
        overview = page.locator('img[src="/static/pan_genome/assets/pan-genome.png"]')
        expect(overview).to_be_visible()
        assert overview.evaluate('''image => {
            const bounds = image.getBoundingClientRect();
            return image.complete && image.naturalWidth > 0 && image.naturalHeight > 0
                && Math.abs(bounds.width / bounds.height - image.naturalWidth / image.naturalHeight) < 0.01;
        }''')
        rows = page.locator('#accession-table-body tr')
        expect(rows).to_have_count(3)
        expect(page.locator('#catalog-status')).to_have_text('Showing 3 of 3 accessions')
        expect(rows.locator('.accession-link')).to_have_text(['Test A', 'Test B', 'Test C'])
        page.screenshot(path=str(screenshots / 'pan-genome-overview-full.png'), full_page=True)
        page.locator('#accession-search').fill('test.b')
        expect(rows).to_have_count(1)
        expect(rows.locator('.accession-link')).to_have_text('Test B')
        page.locator('#accession-search').fill('')
        page.locator('#ploidy-filter').select_option('phased_tetraploid')
        expect(rows).to_have_count(1)
        expect(rows.locator('.accession-link')).to_have_text('Test C')
        page.locator('#ploidy-filter').select_option('all')
        page.locator('#accession-search').fill('no matching accession')
        expect(rows).to_have_text(['No matching genome accessions'])
        page.locator('#accession-search').fill('')
        assembly_link = rows.locator('.accession-link').first
        expect(assembly_link).to_have_attribute('href', '/genome-browser?assembly=monoploid%2FTest-A')
        assembly_link.click()
        expect(page).to_have_url(site + '/genome-browser?assembly=monoploid%2FTest-A')
        for route, label in [('genome-browser', 'Genome Browser'), ('pan-genome', 'Pan-genome')]:
            page.get_by_role('button', name='Genomes', exact=True).click()
            page.locator('.portal-nav-panel').get_by_role('link', name=label, exact=True).click()
            expect(page).to_have_url(site + '/' + route)
            expect(page).to_have_title(label + ' | Potato Research')
        page.goto(site + '/genome-browser')
        expect(page.get_by_role('link', name='Back to Genomes', exact=True)).to_have_count(0)
        page.set_viewport_size({'width': 390, 'height': 900})
        expect(page).to_have_url(site + '/static/lite/high-resolution-required.html?module=genome_browser')
        page.get_by_role('button', name='Module navigation', exact=True).click()
        expect(page.locator('.portal-nav-panel [aria-current="page"]')).to_have_text('Genome Browser')
        page.screenshot(path=str(screenshots / 'genomes-compact-menu.png'))

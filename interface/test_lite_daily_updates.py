from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_INDEX_PATH = REPO_ROOT / "interface/static/lite/index.html"
LITE_STYLES_PATH = REPO_ROOT / "interface/static/lite/styles.css"
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"


def test_daily_updates_layout_keeps_login_first_on_small_screens() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    styles = LITE_STYLES_PATH.read_text(encoding="utf-8")

    assert index.index('id="daily-updates-panel"') < index.index('class="login-card-shell"')
    assert "grid-template-columns: minmax(0, 1fr) minmax(400px, 480px);" in styles
    login_view = styles[styles.index(".login-view,") : styles.index(".login-view::before")]
    assert "grid-template-columns: minmax(0, 1fr);" in login_view
    portal_header = styles[styles.index(".portal-header {") : styles.index(".portal-hero {")]
    assert "min-width: 0;" in portal_header
    assert "max-width: 100%;" in portal_header
    login_stage = styles[styles.index(".login-stage {") : styles.index(".high-resolution-view")]
    assert "min-width: 0;" in login_stage
    assert "max-width: 100%;" in login_stage
    mobile = styles[styles.index("@media (max-width: 800px)") :]
    assert ".login-card-shell" in mobile
    assert "order: 1;" in mobile
    assert ".daily-updates-panel" in mobile
    assert "order: 2;" in mobile
    assert ".high-resolution-view .login-stage" in styles
    assert "styles.css?v=20260825-research-preview-terms" in index
    assert "app.js?v=20260825-research-preview-terms" in index


def test_daily_updates_hides_redundant_pubmed_metadata_and_uses_larger_type() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    styles = LITE_STYLES_PATH.read_text(encoding="utf-8")
    copy = source[source.index("const DAILY_UPDATES_COPY =") : source.index("const getDailyUpdatesCopy =")]
    article = source[source.index("const createDailyUpdateArticle =") : source.index("const getDailyUpdateDateKey =")]

    assert "daily-updates-kicker" not in index
    assert "pubmedDate:" not in copy
    assert "publicationDate:" not in copy
    assert "PubMed 收录" not in copy
    assert "Published" not in copy
    assert "getDailyUpdatesCopy('pubmedDate')" not in article
    assert "getDailyUpdatesCopy('publicationDate')" not in article
    assert ".daily-updates-heading h2 {\n  margin: 0;\n  color: #12203b;\n  font-size: 24px;" in styles
    expected_sizes = {
        ".daily-updates-language-button": "13px",
        ".daily-updates-run-status": "13px",
        ".daily-updates-date-group": "13px",
        ".daily-update-title": "17px",
        ".daily-update-meta": "15px",
        ".daily-update-summary": "15px",
        ".daily-update-translation-pending": "12px",
        ".daily-update-link": "13px",
        ".daily-updates-feedback p": "15px",
        ".daily-updates-retry": "14px",
        ".daily-updates-loading-more": "13px",
    }
    for selector, size in expected_sizes.items():
        rule = styles[styles.index(f"{selector} {{") :]
        rule = rule[: rule.index("}")]
        assert f"font-size: {size};" in rule
    meta_rule = styles[styles.index(".daily-update-meta {") :]
    meta_rule = meta_rule[: meta_rule.index("}")]
    assert "color: rgba(18, 32, 59, 0.6);" in meta_rule


def test_daily_updates_uses_cursor_pagination_and_lifecycle_cancellation() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const DAILY_UPDATES_PAGE_SIZE = 10;" in source
    assert "'/api/daily-updates'" in source
    assert "query.set('cursor', requestedCursor);" in source
    assert "new IntersectionObserver" in source
    assert "seenPmids" in source
    assert "new AbortController()" in source
    assert "startDailyUpdates();" in source[source.index("const showLogin =") :]
    assert "stopDailyUpdates();" in source[source.index("const showWorkspace =") :]
    assert "clearDailyUpdatesRefreshTimer();" in source[source.index("const stopDailyUpdates =") :]


def test_daily_updates_refreshes_while_active_and_after_stale_reentry() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const DAILY_UPDATES_FRESHNESS_MS = 5 * 60 * 1000;" in source
    assert "const DAILY_UPDATES_REFRESH_INTERVAL_MS = 15 * 60 * 1000;" in source
    assert "if (reset || refreshFirstPage) {\n      state.dailyUpdates.lastSuccessfulFetchAt = Date.now();" in source
    assert "await refreshDailyUpdatesIfNeeded({ force: true });" in source
    start = source[source.index("const startDailyUpdates =") : source.index("const stopDailyUpdates =")]
    assert "refreshDailyUpdatesIfNeeded()" in start
    assert "scheduleDailyUpdatesRefresh();" in start
    visibility = source[source.index("document.addEventListener('visibilitychange'") :]
    assert "refreshDailyUpdatesIfNeeded()" in visibility


def test_daily_updates_refresh_rebuilds_the_cursor_chain() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Lite frontend pagination regression test")
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    start = source.index("const replaceDailyUpdatesFirstPage =")
    end = source.index("const loadDailyUpdatesPage =", start)
    helper_source = source[start:end]
    loader = source[end : source.index("const clearDailyUpdatesRefreshTimer =", end)]
    script = f"""
const state = {{
  dailyUpdates: {{
    items: [{{ pmid: 'old-1' }}, {{ pmid: 'old-2' }}],
    seenPmids: new Set(['old-1', 'old-2']),
    nextCursor: 'old-cursor',
    hasMore: false,
  }},
}};
{helper_source}
const pageItems = [{{ pmid: 'new-1' }}, {{ pmid: 'new-2' }}];
replaceDailyUpdatesFirstPage(pageItems, new Set(['new-1', 'new-2']), 'new-cursor', true);
process.stdout.write(JSON.stringify({{
  pmids: state.dailyUpdates.items.map((item) => item.pmid),
  seenPmids: [...state.dailyUpdates.seenPmids],
  nextCursor: state.dailyUpdates.nextCursor,
  hasMore: state.dailyUpdates.hasMore,
}}));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "pmids": ["new-1", "new-2"],
        "seenPmids": ["new-1", "new-2"],
        "nextCursor": "new-cursor",
        "hasMore": True,
    }
    assert "if (reset || refreshFirstPage)" in loader
    assert "replaceDailyUpdatesFirstPage(pageItems, pagePmids, nextCursor, payloadHasMore);" in loader
    assert "...state.dailyUpdates.items.filter" not in loader


def test_daily_updates_pagination_appends_without_rebuilding_existing_articles() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    loader = source[source.index("const loadDailyUpdatesPage =") : source.index("const clearDailyUpdatesRefreshTimer =")]

    assert "renderDailyUpdates({ renderList: false });" in loader
    assert "appendDailyUpdatesList(appendedItems, startIndex);" in loader
    assert "replacementLink?.focus({ preventScroll: true });" in source


def test_daily_updates_language_and_rendering_are_safe() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    start = source.index("const getDailyUpdatesCopy =")
    end = source.index("const startDailyUpdates =")
    daily_updates_source = source[start:end]

    assert "DAILY_UPDATES_LANGUAGE_KEY" in source
    assert "localStorage.setItem(DAILY_UPDATES_LANGUAGE_KEY" in daily_updates_source
    assert "item.titleZh || item.title" in daily_updates_source
    assert "item.summaryZh || item.summary" in daily_updates_source
    assert "textContent" in daily_updates_source
    assert "innerHTML" not in daily_updates_source
    assert "https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(item.pmid)}/" in daily_updates_source
    assert "link.rel = 'noopener noreferrer';" in daily_updates_source
    assert 'role="region" aria-label="Scrollable daily research updates"' in index
    assert 'role="feed" aria-label="Potato research articles"' in index
    assert "dom.dailyUpdatesPanel.lang = 'en';" in daily_updates_source
    assert "dom.dailyUpdatesScroll.lang = contentLanguage;" in daily_updates_source
    assert "article.setAttribute('aria-posinset'" in daily_updates_source
    assert "article.setAttribute('aria-setsize'" in daily_updates_source


def test_daily_updates_date_parser_preserves_pubmed_precision() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Lite frontend date regression test")
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    start = source.index("const DAILY_UPDATE_MONTHS =")
    end = source.index("const getDailyUpdatesRunPresentation =")
    date_source = source[start:end]
    values = ["2026", "2026-02", "2026-Feb", "2026-02-24", "2026-02-31"]
    script = f"""
const state = {{ dailyUpdates: {{ language: 'en' }} }};
{date_source}
const values = {json.dumps(values)};
const result = values.map((value) => {{
  const parsed = parseDailyUpdateDate(value);
  return parsed ? {{
    precision: parsed.precision,
    iso: parsed.date.toISOString(),
    formatted: formatDailyUpdateDate(value),
  }} : null;
}});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result[0] == {
        "precision": "year",
        "iso": "2026-01-01T00:00:00.000Z",
        "formatted": "2026",
    }
    assert result[1] == {
        "precision": "month",
        "iso": "2026-02-01T00:00:00.000Z",
        "formatted": "Feb 2026",
    }
    assert result[2] == result[1]
    assert result[3]["precision"] == "day"
    assert result[3]["formatted"] == "Feb 24, 2026"
    assert result[4] is None


def test_daily_updates_never_run_state_does_not_claim_completion() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "if (!status || status === 'never') return null;" in source

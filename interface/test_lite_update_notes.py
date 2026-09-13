from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"
LITE_INDEX_PATH = REPO_ROOT / "interface/static/lite/index.html"


def _function_block(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end)]


def test_update_notes_seen_version_only_moves_forward() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    mark_seen = _function_block(
        source,
        "const markUpdateNotesSeen =",
        "const getUpdateNotesSortValue =",
    )
    unread = _function_block(
        source,
        "const renderUpdateNotesUnreadState =",
        "const openUpdateNotesPanel =",
    )

    assert "const compareUpdateNotesVersions =" in source
    assert "if (compareUpdateNotesVersions(version, seenVersion) <= 0) return;" in mark_seen
    assert "compareUpdateNotesVersions(version, getSeenUpdateNotesVersion()) > 0" in unread
    assert "getSeenUpdateNotesVersion() !== version" not in unread


def test_update_notes_refresh_before_open_and_reject_stale_content() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    open_panel = _function_block(
        source,
        "const openUpdateNotesPanel =",
        "const closeUpdateNotesPanel =",
    )
    toggle_panel = _function_block(
        source,
        "const toggleUpdateNotesPanel =",
        "const loadUpdateNotes =",
    )
    loader = _function_block(
        source,
        "const loadUpdateNotes =",
        "const initUpdateNotes =",
    )

    assert "compareUpdateNotesVersions(version, seenVersion) < 0" in open_panel
    assert toggle_panel.index("await loadUpdateNotes();") < toggle_panel.index(
        "openUpdateNotesPanel();"
    )
    assert "if (updateNotesLoadPromise) return updateNotesLoadPromise;" in loader
    assert "cache: 'no-store'" in loader
    assert "compareUpdateNotesVersions(updateNotes.version, currentVersion) < 0" in loader
    assert "compareUpdateNotesVersions(updateNotes.version, seenVersion) < 0" in loader
    assert "state.updateNotes = null" not in loader


def test_update_notes_refresh_after_page_restore() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")

    pageshow = _function_block(
        source,
        "window.addEventListener('pageshow'",
        "document.addEventListener('visibilitychange'",
    )
    visibility = source[source.index("document.addEventListener('visibilitychange'") :]

    assert "loadUpdateNotes().catch(() => {});" in pageshow
    assert "loadUpdateNotes().catch(() => {});" in visibility
    assert "app.js?v=20260910-http-tabs" in index


def test_update_notes_more_expands_full_scrollable_history() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    styles = (REPO_ROOT / "interface/static/lite/styles.css").read_text(encoding="utf-8")
    normalizer = _function_block(
        source,
        "const normalizeUpdateNotesPayload =",
        "const renderUpdateNotesContent =",
    )
    renderer = _function_block(
        source,
        "const renderUpdateNotesContent =",
        "const expandUpdateNotes =",
    )
    expand = _function_block(
        source,
        "const expandUpdateNotes =",
        "const renderUpdateNotesUnreadState =",
    )

    assert 'id="update-notes-more"' in index
    assert '>More</button>' in index
    assert 'id="update-notes-scroll"' in index
    assert 'aria-label="Update history"' in index
    assert "title: 'Potato Agent updates'" in normalizer
    assert ".slice(0, UPDATE_NOTES_VISIBLE_LIMIT)" not in normalizer
    assert "allUpdates.slice(0, UPDATE_NOTES_VISIBLE_LIMIT)" in renderer
    assert "state.updateNotesExpanded = true" in expand
    assert "dom.updateNotesScroll.focus({ preventScroll: true })" in expand
    assert ".update-notes-scroll" in styles
    assert "overflow-y: auto" in styles
    assert "margin: 18px -18px 0 0" in styles
    assert "padding-right: 18px" in styles
    assert "styles.css?v=20260910-sidebar-home" in index

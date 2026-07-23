from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"
LITE_INDEX_PATH = REPO_ROOT / "interface/static/lite/index.html"


def test_live_poll_refresh_signal_is_wired_to_file_tree() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "snapshot?.file_tree_refresh" in source
    assert "scheduleFileTreeRefresh('live.workspace-change')" in source
    assert "scheduleFileTreeRefresh('live.terminal')" in source
    assert "api('/api/files/revision'" in source
    assert "scheduleFileTreeRefresh('workspace.revision')" in source


def test_hidden_file_tree_refresh_is_deferred_until_visible() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "fileTreeRefreshDeferred = true" in source
    assert "document.addEventListener('visibilitychange'" in source
    assert "refreshFileTreeAfterFocus()" in source
    assert "pauseFileTreeChangePolling()" in source


def test_file_tree_refresh_uses_request_ownership_tokens() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const refreshToken = Symbol('file-tree-refresh')" in source
    assert "fileTreeRefreshInFlight !== refreshToken" in source
    assert "refreshGeneration !== fileTreeRefreshGeneration" in source
    assert "requestAuthSessionGeneration === authSessionGeneration" in source
    assert "String(state.user?.id || '') === String(userId || '')" in source


def test_file_tree_requests_bypass_browser_cache() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")

    assert "cache: 'no-store'" in source
    assert "app.js?v=20260723-model-labels" in index

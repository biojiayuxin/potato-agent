from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"


def _source_window(source: str, marker: str, *, size: int = 8_000) -> str:
    start = source.index(marker)
    return source[start : start + size]


def test_workspace_path_linkifier_marks_supported_message_paths() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    classifier = _source_window(source, "const isWorkspaceDataPath =", size=1_000)
    decoder = _source_window(source, "const decodeWorkspacePathHref =", size=500)
    bare_path_trimmer = _source_window(source, "const trimBareWorkspacePath =", size=1_500)
    exact_code_path = _source_window(source, "const replaceInlineCodeWorkspacePath =", size=1_000)
    fenced_code_paths = _source_window(
        source,
        "const replaceFencedCodeWorkspacePathLines =",
        size=2_000,
    )
    linkifier = _source_window(source, "const linkifyWorkspacePaths =")

    assert "const isWorkspaceDataPath =" in source
    assert "const WORKSPACE_DATA_PATH_PREFIX = '/mnt/data/';" in source
    assert "WORKSPACE_DATA_PATH_PREFIX" in classifier
    assert "workspacePath = decodeWorkspacePathHref(value);" in source
    assert "decodeURIComponent" in decoder
    assert "data-workspace-path" in source
    assert "['）', '（']" in bare_path_trimmer
    assert "['”', '“']" in bare_path_trimmer
    assert "/[\\r\\n]/u.test(path)" in exact_code_path
    assert "source.split(/(\\r?\\n)/u)" in fenced_code_paths
    assert "replaceFencedCodeWorkspacePathLines(textNode)" in linkifier
    fenced_handler = linkifier.index("replaceFencedCodeWorkspacePathLines(textNode)")
    fenced_skip = linkifier.index("if (parent.closest('pre')")
    bare_handler = linkifier.index("replaceBareWorkspacePaths(textNode)")
    assert fenced_handler < fenced_skip < bare_handler
    assert "CODE" in linkifier
    assert "replaceBareWorkspacePaths(textNode)" in linkifier


def test_only_assistant_message_content_is_linkified() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    render_messages = source[
        source.index("const renderMessages =") : source.index("const updateModelSelectWidth =")
    ]

    assert source.count("linkifyWorkspacePaths(content)") == 1
    assert re.search(
        r"if\s*\([^)]*message\.role\s*===\s*['\"]assistant['\"][^)]*\)"
        r"\s*\{[^{}]*linkifyWorkspacePaths\(content\)",
        render_messages,
    )


def test_workspace_path_click_routes_directories_and_files_through_existing_apis() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    navigation = _source_window(source, "const openWorkspacePathFromMessage =")
    navigation_start = _source_window(source, "const beginWorkspacePathNavigation =", size=1_000)
    navigation_check = _source_window(source, "const isWorkspacePathNavigationCurrent =", size=1_000)

    assert "probeWorkspacePath" in navigation
    assert "fetchOpenedDirectory" in navigation
    assert "revealWorkspaceTreePath" in navigation
    assert "openFilePreview" in navigation
    assert "sessionId: String(state.activeSessionId || '')" in navigation_start
    assert "navigation.sessionId === String(state.activeSessionId || '')" in navigation_check
    assert "invalidateFileTreeRootRequests();" in navigation_start
    assert "/api/files/open?path=" in source
    assert "buildFilePreviewMetaUrl" in source
    assert "data-workspace-path" in source


def test_workspace_tree_reveal_uses_exact_path_markers_and_scrolls_into_view() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    reveal = _source_window(source, "const revealWorkspaceTreePath =")

    assert "data-tree-path" in source
    assert "scrollFileTreePathIntoView" in reveal
    assert "scrollIntoView" in source


def test_manual_file_browser_actions_own_their_async_navigation_results() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    open_directory = source[
        source.index("const openDirectory =") : source.index("const invalidateWorkspacePathNavigation =")
    ]
    tree_node = _source_window(source, "const renderTreeNode =", size=8_000)

    assert "const navigation = beginWorkspacePathNavigation();" in open_directory
    assert open_directory.count("isWorkspacePathNavigationCurrent(navigation)") >= 2
    assert re.search(
        r"await\s+fetchOpenedDirectory\(requestedPath\);[\s\S]{0,300}"
        r"isWorkspacePathNavigationCurrent\(navigation\)",
        open_directory,
    )
    assert tree_node.count("const navigation = beginWorkspacePathNavigation();") >= 2
    assert len(re.findall(
        r"await\s+listDirectory\(nodePath\);[\s\S]{0,300}"
        r"isWorkspacePathNavigationCurrent\(navigation\)",
        tree_node,
    )) >= 2


def test_workspace_tab_actions_invalidate_message_path_navigation() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    tab_button = _source_window(source, "const createWorkspaceTabButton =", size=3_000)

    assert "button.addEventListener('click'" in tab_button
    assert "close.addEventListener('click'" in tab_button
    assert tab_button.count("invalidateWorkspacePathNavigation();") >= 2


def test_file_tree_root_changes_invalidate_stale_requests_and_renders() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    invalidation = _source_window(source, "const invalidateFileTreeRootRequests =", size=1_000)
    set_root = _source_window(source, "const setFileTreeRoot =", size=1_500)
    render_tree = _source_window(source, "const renderFileTree =", size=4_000)

    assert "invalidateFileTreeRootRequests();" in set_root
    assert "fileTreeRefreshGeneration += 1;" in invalidation
    assert "fileTreeRefreshInFlight = null;" in invalidation
    assert "const renderWorkspaceRoot =" in render_tree
    assert "const renderRootPath =" in render_tree
    assert re.search(r"(?:===|!==)\s*renderWorkspaceRoot", render_tree)
    assert re.search(r"(?:===|!==)\s*renderRootPath", render_tree)


def test_workspace_file_initialization_keeps_path_navigation_ownership() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    fetch_workspace_files = source[
        source.index("const fetchWorkspaceFiles =") : source.index("const fetchOpenedDirectory =")
    ]
    generation_check = (
        "requestWorkspacePathNavigationGeneration !== workspacePathNavigationGeneration"
    )
    checks = [
        match.start()
        for match in re.finditer(generation_check, fetch_workspace_files)
    ]
    set_root = fetch_workspace_files.index("await setFileTreeRoot(")
    start_polling = fetch_workspace_files.index("startFileTreeChangePolling();")

    assert (
        "const requestWorkspacePathNavigationGeneration = workspacePathNavigationGeneration;"
        in fetch_workspace_files
    )
    assert len(checks) >= 2
    assert checks[0] < set_root < checks[-1] < start_polling

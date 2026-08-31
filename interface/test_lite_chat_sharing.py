from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_INDEX_PATH = REPO_ROOT / "interface/static/lite/index.html"
LITE_STYLES_PATH = REPO_ROOT / "interface/static/lite/styles.css"
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"


def test_chat_sharing_ui_has_account_gate_and_accessible_dialogs() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    styles = LITE_STYLES_PATH.read_text(encoding="utf-8")

    assert '<meta name="referrer" content="no-referrer"' in index
    assert 'id="share-login-banner"' in index
    assert 'id="share-chat-button"' in index
    assert 'id="share-rules-modal"' in index
    assert 'id="share-result-modal"' in index
    assert 'id="share-import-modal"' in index
    assert 'class="share-dialog share-import-dialog"' in index
    assert 'tabindex="-1"' in index
    assert "!state.user.is_temporary" in source
    assert "state.user?.features?.chat_sharing" not in source
    assert "user?.features?.chat_sharing" in source
    assert "return typeof directFlag === 'boolean' ? directFlag : false" in source
    assert 'class="chat-action-buttons" role="group" aria-label="Chat actions"' in index
    assert ".chat-action-buttons" in styles
    assert ".chat-action-button" in styles
    assert ".workspace-view .chat-tab-panel > .messages {\n  padding-top: 64px;\n}" in styles
    assert ".share-modal[hidden]" in styles

    topbar = index[index.index('<div class="topbar-actions">') : index.index("</header>")]
    chat_panel = index[index.index('id="chat-tab-panel"') : index.index('id="messages"')]
    assert 'id="share-chat-button"' not in topbar
    assert chat_panel.index('id="share-chat-button"') < chat_panel.index('id="export-chat-button"')


def test_chat_sharing_rules_match_the_server_contract() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")

    expected_rules = [
        "Only visible questions and answers are included.",
        "expires after 7 days",
        "up to 100 importing accounts",
        "Sensitive paths, secrets, and private workspace references are removed.",
    ]
    for rule in expected_rules:
        assert rule in index

    removed_copy = [
        "The snapshot is immutable and does not follow later chat changes.",
        "The link cannot be manually revoked.",
        "Deleting the original chat invalidates the link.",
        "Copies already imported into other accounts cannot be recalled.",
    ]
    for sentence in removed_copy:
        assert sentence not in index


def test_chat_share_dialog_copy_and_actions_are_concise() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert '<div class="share-dialog-kicker">Share chat</div>' not in index
    assert '<div class="share-dialog-kicker">Link created</div>' not in index
    assert "Anyone with this link must sign in or use Quick Start to view the conversation." in index
    assert 'id="share-result-done"' not in index
    assert "shareResultDone" not in source
    assert "new Intl.DateTimeFormat('en-US'," in source


def test_chat_share_requests_use_protected_json_post_contract() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const SHARE_REQUEST_HEADERS = { 'X-Potato-Request': '1' };" in source
    create = source[source.index("const createActiveChatShare =") : source.index("const copyCreatedShareLink =")]
    assert "`/api/sessions/${encodeURIComponent(sessionId)}/shares`" in create
    assert "headers: SHARE_REQUEST_HEADERS" in create
    assert "body: JSON.stringify({})" in create

    import_request = source[
        source.index("const requestPendingShareImport =") : source.index("const upsertImportedShareSession =")
    ]
    assert "'/api/chat-shares/import'" in import_request
    assert "body: JSON.stringify({ token })" in import_request
    assert "response.status === 202" in import_request
    assert "status === 404 || status === 410" in import_request
    assert "status === 429" in import_request
    assert "parseShareRetryAfterMs(error?.retryAfter)" in import_request
    assert "signal: abortController.signal" in import_request
    assert "SHARE_IMPORT_REQUEST_TIMEOUT_MS" in import_request
    assert "shareImportGeneration: requestGeneration" in import_request
    assert "schedulePendingShareImport" in import_request
    assert "classList.toggle('single-action', waiting || terminal)" in source
    assert 'aria-live="polite" aria-atomic="true"' in index


def test_share_fragment_is_captured_privately_and_imported_before_default_chat() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const PENDING_SHARE_TOKEN_KEY = 'lite_pending_chat_share_token';" in source
    capture = source[source.index("const capturePendingShareIntent =") : source.index("const clearPendingShareIntent =")]
    assert "window.location.hash" in capture
    assert "#share=" in capture
    assert "removeShareFragmentFromAddressBar();" in capture
    assert "persistPendingShareToken(rawToken, { replaceIntent: true })" in capture
    assert "window.history.replaceState" in source
    assert "sessionStorage.setItem(PENDING_SHARE_TOKEN_KEY" in source
    assert "sessionStorage.removeItem(PENDING_SHARE_TOKEN_KEY)" in source
    assert "normalizedToken !== state.pendingShareToken" in source
    replace_intent = source[
        source.index("const persistPendingShareToken =") : source.index(
            "const removeShareFragmentFromAddressBar ="
        )
    ]
    assert replace_intent.index("clearShareImportRetryTimer();") < replace_intent.index(
        "cancelShareImportRequest();"
    )
    assert "cancelShareImportRequest();" in source
    assert "if (state.pendingShareToken !== token) return null;" in source
    apply_result = source[
        source.index("const applyImportedShareResult =") : source.index("const importPendingSharedChat =")
    ]
    assert "operationIsCurrent" in apply_result
    assert "shouldApply: operationIsCurrent" in apply_result
    assert apply_result.index("clearPendingShareIntent();") > apply_result.index("await openSession")

    initialize = source[source.index("const initializeWorkspaceData =") : source.index("const initResizablePanels =")]
    assert initialize.index("importPendingSharedChat({ duringInitialization: true })") < initialize.index(
        "await refreshSessions()"
    )
    assert initialize.index("await refreshSessions()") < initialize.index("state.sessions[0].id")
    assert initialize.index("if (state.pendingShareToken)") < initialize.index("state.sessions[0].id")
    pending_branch = initialize[
        initialize.rindex("if (state.pendingShareToken)") : initialize.index("if (state.sessions.length > 0)")
    ]
    assert "renderWorkspace();" in pending_branch
    assert "return;" in pending_branch
    assert "window.addEventListener('hashchange'" in source
    assert source.rindex("capturePendingShareIntent();") < source.rindex("bootstrapSession();")


def test_attachment_only_chat_is_not_shareable() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    shareable = source[
        source.index("const hasShareableActiveMessages =") : source.index("const isChatSharingFeatureEnabled =")
    ]

    assert "message?.content" in shareable
    assert "message?.files" not in shareable
    assert "hasShareableActiveMessages()" in source


def test_chat_sharing_assets_are_cache_busted() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")

    assert "styles.css?v=20260831-update-scrollbar" in index
    assert "app.js?v=20260831-update-history" in index

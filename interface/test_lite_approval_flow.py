from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"
LITE_INDEX_PATH = REPO_ROOT / "interface/static/lite/index.html"
LITE_STYLES_PATH = REPO_ROOT / "interface/static/lite/styles.css"


def test_approval_submission_has_timeout_reconcile_and_retry() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "APPROVAL_REQUEST_TIMEOUT_MS = 8 * 1000" in source
    assert "APPROVAL_REQUEST_MAX_ATTEMPTS = 2" in source
    assert "controller.abort()" in source
    assert "approvalRequestWithTimeout(" in source
    assert "reconcileApprovalDecision(" in source
    assert "isRetryableApprovalError(lastError)" in source
    assert "approval_id: approvalId" in source


def test_approval_submission_has_visible_pending_state() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    styles = LITE_STYLES_PATH.read_text(encoding="utf-8")

    assert "activeLabel = 'Submitting...'" in source
    assert "'Retrying...'" in source
    assert "aria-busy" in source
    assert ".approval-actions button:disabled" in styles


def test_approval_submission_is_owned_by_exact_request() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "activeApprovalSubmission === submission" in source
    assert "approvalMatchesSubmission(state.pendingApproval, submission)" in source
    assert "finishApprovalSubmission(submission)" in source
    assert "if (!isActiveApprovalSubmission(submission)) return" in source


def test_expired_approval_clears_only_the_matching_request() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    request_branch = source.split("if (type === 'approval.request') {", 1)[1].split(
        "if (type === 'approval.expired') {", 1
    )[0]
    expired_branch = source.split("if (type === 'approval.expired') {", 1)[1].split(
        "if (type === 'tool.progress') {", 1
    )[0]
    clear_helper = source.split("const clearSessionPendingApproval =", 1)[1].split(
        "const autoResizePromptInput", 1
    )[0]

    assert "if (!sessionNeedsApproval(persistentSessionId))" in request_branch
    assert "message?.payload?.approval_id" in expired_branch
    assert "clearSessionPendingApproval(persistentSessionId, approvalId)" in expired_branch
    assert "expectedApprovalId" in clear_helper
    assert "pendingApprovalsBySessionId.get(normalizedSessionId)" in clear_helper
    assert "currentApproval.approvalId" in clear_helper
    assert "!== normalizedApprovalId" in clear_helper


def test_stale_approval_409_is_an_exact_request_terminal_state() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    submit_branch = source.split("const submitApprovalDecision =", 1)[1].split(
        "const stopAuthPolling", 1
    )[0]

    assert "Number(error?.status || 0) === 409" in source
    assert "/approval request is no longer pending/i" in source
    assert "clearSessionPendingApproval(approvalSessionId, submittedApprovalId)" in submit_branch


def test_stale_turn_failure_cannot_replace_authoritative_messages() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "findConfirmedTurnSessionId(" in source
    assert "stillOwnsOptimisticAssistant(" in source
    assert "currentMessages === messages" in source
    assert "Connection interrupted before the turn could be confirmed" in source
    assert "[Error] ${String(error.message || error)}" not in source


def test_gateway_exit_clears_active_approval_and_cache_is_busted() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")
    gateway_exit_branch = source.split("if (type === 'gateway.exit') {", 1)[1].split(
        "if (type === 'message.start') {", 1
    )[0]

    assert "pendingApprovalsBySessionId.clear()" in gateway_exit_branch
    assert "syncActiveSessionUiState()" in gateway_exit_branch
    assert "renderApprovalModal()" in gateway_exit_branch
    assert "app.js?v=20260723-model-labels" in index

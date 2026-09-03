from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "interface" / "static" / "lite" / "app.js"
INDEX_HTML = REPO_ROOT / "interface" / "static" / "lite" / "index.html"
STYLES_CSS = REPO_ROOT / "interface" / "static" / "lite" / "styles.css"


def test_completed_assistant_messages_expose_an_idempotent_fork_action() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "forkCursor: String(message?.fork_cursor" in source
    assert "message.role === 'assistant'" in source
    assert "&& message.done" in source
    assert "&& !isStreaming" in source
    assert "forkRequestIdsByCursor.get(operationKey) || uuid()" in source
    assert "state.forkingMessageCursors.has(operationKey)" in source
    assert "fork_button" not in source
    assert "fork_cursor: forkCursor" in source
    assert "request_id: requestId" in source
    assert "await openSession(forkedSession.id)" in source


def test_fork_control_is_accessible_on_keyboard_and_touch() -> None:
    markup = INDEX_HTML.read_text(encoding="utf-8")
    stylesheet = STYLES_CSS.read_text(encoding="utf-8")
    assert 'class="message-fork-button"' in markup
    assert 'aria-label="Fork conversation here"' in markup
    assert 'icons/git-fork.svg' in markup
    assert ".message-fork-button:focus-visible" in stylesheet
    assert "@media (hover: none), (pointer: coarse)" in stylesheet
    assert "pointer-events: auto" in stylesheet

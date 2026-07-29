from __future__ import annotations

from interface.verify_background_turn_flow import _completion_timeout_message


def test_completion_timeout_message_omits_live_state_content() -> None:
    sentinel = "approval-command-and-chat-content"
    message = _completion_timeout_message(
        "session-id",
        {
            "status": "awaiting_approval",
            "pending_approval": {"command": sentinel},
            "last_error": sentinel,
        },
    )

    assert message == (
        "timed out waiting for completion: "
        "session_id=session-id status=awaiting_approval"
    )
    assert sentinel not in message

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _Bridge:
    user_id = "user-1"

    async def rpc(self, method: str, params: dict) -> dict:
        assert method == "session.interrupt"
        assert params == {"session_id": "live-1"}
        return {"status": "interrupted"}


class _MissingSessionBridge:
    user_id = "user-1"

    async def rpc(self, method: str, params: dict) -> dict:
        assert method == "session.interrupt"
        assert params == {"session_id": "live-1"}
        from interface.tui_gateway_bridge import TuiGatewayBridgeError

        raise TuiGatewayBridgeError("session not found")


def _run(coro):
    return asyncio.run(coro)


def test_interrupt_marks_live_state_final_and_done() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-interrupt-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
        save_display_messages,
        save_live_session_state,
    )
    from interface.session_run_manager import SessionRunManager

    save_display_messages(
        "user-1",
        "session-1",
        [
            {"id": "user-msg", "role": "user", "content": "hello", "done": True},
            {"id": "assistant-1", "role": "assistant", "content": "", "done": False},
        ],
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="running",
        pending_approval={"command": "cmd", "description": "desc"},
        last_error="",
        last_event_seq=7,
        db_path=db_path,
    )

    manager = SessionRunManager(db_path=db_path)
    result = _run(
        manager.interrupt_run(
            bridge=_Bridge(),  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
        )
    )

    assert result == {"status": "interrupted"}
    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "interrupted"
    assert live_state["pending_approval"] is None
    assert live_state["finished_at"] > 0

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-1")
    assert assistant["done"] is True


def test_late_delta_after_interrupt_does_not_reopen_live_state() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-late-delta-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
        save_display_messages,
        save_live_session_state,
    )
    from interface.session_run_manager import SessionRunManager

    save_display_messages(
        "user-1",
        "session-1",
        [
            {"id": "user-msg", "role": "user", "content": "hello", "done": True},
            {"id": "assistant-1", "role": "assistant", "content": "partial", "done": True},
        ],
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="interrupted",
        pending_approval=None,
        last_error="",
        last_event_seq=7,
        finished_at=1,
        db_path=db_path,
    )

    manager = SessionRunManager(db_path=db_path)
    _run(
        manager.handle_bridge_event(
            _Bridge(),  # type: ignore[arg-type]
            {
                "type": "message.delta",
                "session_id": "live-1",
                "persistent_session_id": "session-1",
                "run_id": "run-1",
                "seq": 8,
                "payload": {"text": " late"},
            },
        )
    )

    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "interrupted"
    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-1")
    assert assistant["content"] == "partial"


def test_interrupt_marks_missing_gateway_session_failed() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-missing-session-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
        save_display_messages,
        save_live_session_state,
    )
    from interface.session_run_manager import SessionRunManager

    save_display_messages(
        "user-1",
        "session-1",
        [
            {"id": "user-msg", "role": "user", "content": "hello", "done": True},
            {"id": "assistant-1", "role": "assistant", "content": "", "done": False},
        ],
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="running",
        pending_approval=None,
        last_error="",
        last_event_seq=9,
        db_path=db_path,
    )

    manager = SessionRunManager(db_path=db_path)
    result = _run(
        manager.interrupt_run(
            bridge=_MissingSessionBridge(),  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
        )
    )

    assert result["status"] == "failed"
    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "failed"
    assert "no longer attached" in live_state["last_error"]

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-1")
    assert assistant["done"] is True
    assert "no longer attached" in assistant["content"]


def run() -> None:
    test_interrupt_marks_live_state_final_and_done()
    test_late_delta_after_interrupt_does_not_reopen_live_state()
    test_interrupt_marks_missing_gateway_session_failed()


if __name__ == "__main__":
    run()

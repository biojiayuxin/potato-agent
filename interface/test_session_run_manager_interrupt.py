from __future__ import annotations

import asyncio
import copy
import os
import tempfile
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _Bridge:
    user_id = "user-1"

    def __init__(self) -> None:
        self.forgotten: list[str] = []

    def add_event_listener(self, listener) -> str:
        return "listener-1"

    def remove_event_listener(self, listener_id: str) -> None:
        return None

    async def rpc(self, method: str, params: dict) -> dict:
        assert method == "session.interrupt"
        assert params == {"session_id": "live-1"}
        return {"status": "interrupted"}

    def forget_live_session(self, live_session_id: str) -> None:
        self.forgotten.append(live_session_id)


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


def test_late_interrupt_does_not_overwrite_completed_state() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-late-interrupt-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import get_live_session_state, save_live_session_state
    from interface.session_run_manager import SessionRunManager
    from interface.tui_gateway_bridge import TuiGatewayBridgeError

    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="completed",
        pending_approval=None,
        last_error="",
        last_event_seq=8,
        finished_at=1,
        db_path=db_path,
    )

    class UnexpectedRpcBridge:
        async def rpc(self, method: str, params: dict) -> dict:
            raise AssertionError("a terminal session must not reach the gateway")

    manager = SessionRunManager(db_path=db_path)
    try:
        _run(
            manager.interrupt_run(
                bridge=UnexpectedRpcBridge(),  # type: ignore[arg-type]
                user_id="user-1",
                session_id="session-1",
            )
        )
    except TuiGatewayBridgeError as exc:
        assert "no longer active" in str(exc)
    else:
        raise AssertionError("a completed session accepted a late interrupt")

    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "completed"
    assert live_state["finished_at"] == 1


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


def test_late_complete_from_interrupted_run_does_not_finish_new_turn() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-late-complete-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
        save_display_messages,
        save_live_session_state,
    )
    from interface.session_run_manager import SessionRunContext, SessionRunManager

    save_display_messages(
        "user-1",
        "session-1",
        [
            {"id": "user-1", "role": "user", "content": "first", "done": True},
            {"id": "assistant-1", "role": "assistant", "content": "", "done": True},
            {"id": "user-2", "role": "user", "content": "second", "done": True},
            {"id": "assistant-2", "role": "assistant", "content": "working", "done": False},
        ],
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-2",
        live_session_id="live-2",
        tip_session_id="session-1",
        assistant_message_id="assistant-2",
        status="running",
        pending_approval=None,
        last_error="",
        last_event_seq=20,
        db_path=db_path,
    )

    async def scenario() -> _Bridge:
        bridge = _Bridge()
        manager = SessionRunManager(db_path=db_path)
        await manager.attach_bridge(bridge)  # type: ignore[arg-type]
        old_context = SessionRunContext(
            user_id="user-1",
            session_id="session-1",
            run_id="run-1",
            assistant_message_id="assistant-1",
        )
        await manager._close_run_context(old_context, live_session_id="live-1")
        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "message.complete",
                "session_id": "live-1",
                "persistent_session_id": "session-1",
                "run_id": "run-1",
                "seq": 21,
                "payload": {"text": None, "status": "interrupted"},
            },
        )
        await manager.shutdown()
        return bridge

    bridge = _run(scenario())

    assert "live-1" in bridge.forgotten
    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["run_id"] == "run-2"
    assert live_state["live_session_id"] == "live-2"
    assert live_state["status"] == "running"
    assert live_state["last_event_seq"] == 20

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-2")
    assert assistant["content"] == "working"
    assert assistant["done"] is False


def test_unknown_tip_complete_after_interrupt_does_not_finish_new_turn() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-tip-complete-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
        save_display_messages,
        save_live_session_state,
    )
    from interface.session_run_manager import SessionRunContext, SessionRunManager

    save_display_messages(
        "user-1",
        "session-1",
        [
            {"id": "user-1", "role": "user", "content": "first", "done": True},
            {"id": "assistant-1", "role": "assistant", "content": "partial", "done": True},
            {"id": "user-2", "role": "user", "content": "second", "done": True},
            {"id": "assistant-2", "role": "assistant", "content": "", "done": False},
        ],
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-2",
        live_session_id="live-2",
        tip_session_id="session-1",
        assistant_message_id="assistant-2",
        status="starting",
        pending_approval=None,
        last_error="",
        last_event_seq=0,
        db_path=db_path,
    )

    async def scenario() -> None:
        manager = SessionRunManager(
            tip_resolver=lambda user_id, session_id: session_id,
            db_path=db_path,
        )
        context = SessionRunContext(
            user_id="user-1",
            session_id="session-1",
            run_id="run-2",
            assistant_message_id="assistant-2",
        )
        async with manager._lock:
            manager._run_contexts_by_session_id[("user-1", "session-1")] = context
            manager._ensure_flush_task_locked(context)
        await manager.handle_bridge_event(
            _Bridge(),  # type: ignore[arg-type]
            {
                "type": "message.complete",
                "session_id": "session-1",
                "persistent_session_id": "",
                "run_id": "",
                "seq": 42,
                "payload": {
                    "text": None,
                    "status": "interrupted",
                    "reasoning": "stale interrupted run",
                },
            },
        )
        await manager.shutdown()

    _run(scenario())

    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["run_id"] == "run-2"
    assert live_state["live_session_id"] == "live-2"
    assert live_state["status"] == "starting"
    assert live_state["last_event_seq"] == 0
    assert live_state["last_error"] == ""

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-2")
    assert assistant["content"] == ""
    assert assistant.get("reasoningContent", "") == ""
    assert assistant["done"] is False


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


def test_error_complete_without_text_persists_user_visible_failure() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-error-complete-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
        save_display_messages,
        save_live_session_state,
    )
    from interface.session_run_manager import (
        MODEL_RESPONSE_ERROR_MESSAGE,
        SessionRunManager,
    )

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
    _run(
        manager.handle_bridge_event(
            _Bridge(),  # type: ignore[arg-type]
            {
                "type": "message.complete",
                "session_id": "live-1",
                "persistent_session_id": "session-1",
                "run_id": "run-1",
                "seq": 10,
                "payload": {"text": None, "status": "error"},
            },
        )
    )

    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "failed"
    assert live_state["last_error"] == MODEL_RESPONSE_ERROR_MESSAGE

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-1")
    assert assistant["done"] is True
    assert assistant["content"] == MODEL_RESPONSE_ERROR_MESSAGE


def test_tool_start_and_complete_are_persisted_as_progress() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-tool-progress-")) / "interface.db"
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
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "",
                "progressLines": [],
                "done": False,
            },
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
        last_event_seq=1,
        db_path=db_path,
    )

    async def scenario() -> None:
        manager = SessionRunManager(db_path=db_path)
        bridge = _Bridge()
        assert await manager.get_workspace_change_revision("user-1") == ""
        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "tool.start",
                "session_id": "live-1",
                "persistent_session_id": "session-1",
                "run_id": "run-1",
                "seq": 2,
                "payload": {"context": "`🛠️ list_directory test_dir`"},
            },
        )
        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "tool.complete",
                "session_id": "live-1",
                "persistent_session_id": "session-1",
                "run_id": "run-1",
                "seq": 3,
                "payload": {"summary": "`🛠️ list_directory completed`"},
            },
        )
        tool_live_state = get_live_session_state(
            "user-1", "session-1", db_path=db_path
        )
        assert tool_live_state is not None
        assert tool_live_state["last_workspace_event_seq"] == 3
        tool_revision = await manager.get_workspace_change_revision("user-1")
        assert tool_revision
        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "message.complete",
                "session_id": "live-1",
                "persistent_session_id": "session-1",
                "run_id": "run-1",
                "seq": 4,
                "payload": {"text": "done", "status": "complete"},
            },
        )
        complete_revision = await manager.get_workspace_change_revision("user-1")
        assert complete_revision
        assert complete_revision != tool_revision
        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "background.complete",
                "session_id": "live-1",
                "persistent_session_id": "session-1",
                "run_id": "run-1",
                "seq": 5,
                "payload": {"task_id": "bg-1", "text": "done"},
            },
        )
        background_revision = await manager.get_workspace_change_revision("user-1")
        assert background_revision
        assert background_revision != complete_revision

    _run(scenario())

    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "completed"
    assert live_state["last_workspace_event_seq"] == 4

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-1")
    assert assistant["progressLines"] == [
        "`🛠️ list_directory test_dir`",
        "`🛠️ list_directory completed`",
    ]
    assert assistant["content"] == "done"
    assert assistant["done"] is True


def test_delta_retry_after_partial_persistence_is_idempotent(monkeypatch) -> None:
    from interface.session_run_manager import SessionRunContext, SessionRunManager

    manager = SessionRunManager()
    context = SessionRunContext(
        user_id="user-1",
        session_id="session-1",
        run_id="run-1",
        assistant_message_id="assistant-1",
    )
    stored_messages = [
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": "",
            "done": False,
        }
    ]
    live_save_calls = 0

    async def get_messages(user_id: str, session_id: str):
        return copy.deepcopy(stored_messages)

    async def save_messages(user_id: str, session_id: str, messages, **kwargs):
        nonlocal stored_messages
        stored_messages = copy.deepcopy(messages)

    async def get_live_state(user_id: str, session_id: str):
        return {"pending_approval": None, "tip_session_id": "session-1"}

    async def save_live_state(user_id: str, session_id: str, **kwargs):
        nonlocal live_save_calls
        live_save_calls += 1
        if live_save_calls == 1:
            raise RuntimeError("database is locked")

    async def request_flush(run_context):
        return None

    monkeypatch.setattr(manager, "_get_display_messages", get_messages)
    monkeypatch.setattr(manager, "_save_display_messages", save_messages)
    monkeypatch.setattr(manager, "_get_live_session_state", get_live_state)
    monkeypatch.setattr(manager, "_save_live_session_state", save_live_state)
    monkeypatch.setattr(manager, "_request_flush", request_flush)

    async def scenario() -> None:
        try:
            await manager._append_delta(
                context,
                {"text": "hello"},
                7,
                "live-1",
            )
        except RuntimeError as exc:
            assert str(exc) == "database is locked"
        else:
            raise AssertionError("the first live-state write should fail")

        await manager._append_delta(
            context,
            {"text": "hello"},
            7,
            "live-1",
        )

    _run(scenario())

    assert live_save_calls == 2
    assert stored_messages[0]["content"] == "hello"
    assert stored_messages[0]["_lastDeltaEventSeq"] == 7


def run() -> None:
    test_interrupt_marks_live_state_final_and_done()
    test_late_delta_after_interrupt_does_not_reopen_live_state()
    test_late_complete_from_interrupted_run_does_not_finish_new_turn()
    test_interrupt_marks_missing_gateway_session_failed()
    test_error_complete_without_text_persists_user_visible_failure()
    test_tool_start_and_complete_are_persisted_as_progress()


if __name__ == "__main__":
    run()

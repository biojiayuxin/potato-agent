from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest


class _SubmitBridge:
    user_id = "user-1"

    def __init__(
        self,
        *,
        dispatch_result: dict[str, Any] | None = None,
        complete_on_submit: bool = True,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.dispatch_result = dispatch_result or {"type": "skill", "message": "skill prompt"}
        self.complete_on_submit = complete_on_submit
        self.listener = None
        self.persistent_session_id = ""
        self.run_id = ""
        self.forgotten: list[str] = []

    def add_event_listener(self, listener) -> str:
        self.listener = listener
        return "listener-1"

    def remove_event_listener(self, listener_id: str) -> None:
        return None

    def remember_live_session(
        self,
        live_session_id: str,
        *,
        persistent_session_id: str = "",
        run_id: str = "",
    ) -> None:
        self.persistent_session_id = persistent_session_id
        self.run_id = run_id

    def forget_live_session(self, live_session_id: str) -> None:
        self.forgotten.append(live_session_id)

    async def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "command.dispatch":
            return self.dispatch_result
        if method == "prompt.submit":
            if self.complete_on_submit and self.listener is not None:
                await self.listener(
                    {
                        "type": "message.complete",
                        "session_id": str(params.get("session_id") or ""),
                        "persistent_session_id": self.persistent_session_id,
                        "run_id": self.run_id,
                        "seq": 1,
                        "payload": {"text": "done", "status": "complete"},
                    }
                )
            return {"ok": True}
        raise AssertionError(f"unexpected rpc method: {method}")


def _run(coro):
    return asyncio.run(coro)


def _db_path(prefix: str) -> Path:
    db_path = Path(tempfile.mkdtemp(prefix=prefix)) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)
    return db_path


async def _wait_for_calls(bridge: _SubmitBridge, count: int) -> None:
    for _ in range(100):
        if len(bridge.calls) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} bridge calls, got {bridge.calls!r}")


async def _wait_for_status(db_path: Path, status: str) -> dict[str, Any]:
    from interface.display_store import get_live_session_state

    for _ in range(100):
        live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
        if live_state is not None and live_state["status"] == status:
            return live_state
        await asyncio.sleep(0.01)
    raise AssertionError(f"live state did not reach {status!r}")


def test_chat_mode_submits_prompt_directly() -> None:
    db_path = _db_path("potato-run-manager-chat-mode-")

    from interface.display_store import get_display_messages
    from interface.session_run_manager import SessionRunManager

    async def scenario() -> _SubmitBridge:
        bridge = _SubmitBridge()
        manager = SessionRunManager(db_path=db_path)
        await manager.submit_turn(
            bridge=bridge,  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
            live_session_id="live-1",
            prompt="hello",
            attachments=[],
            existing_messages=[],
            mode="chat",
            request_id="request-1",
        )
        await _wait_for_calls(bridge, 1)
        await _wait_for_status(db_path, "completed")
        await manager.shutdown()
        return bridge

    bridge = _run(scenario())

    assert [method for method, _ in bridge.calls] == ["prompt.submit"]
    assert bridge.run_id == "request-1"
    assert bridge.calls[0][1]["text"] == "hello"

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello"


def test_plan_mode_dispatches_plan_then_submits_skill_message() -> None:
    db_path = _db_path("potato-run-manager-plan-mode-")

    from interface.display_store import get_display_messages
    from interface.session_run_manager import ATTACHMENT_BLOCK_START, SessionRunManager

    async def scenario() -> _SubmitBridge:
        bridge = _SubmitBridge(
            dispatch_result={"type": "skill", "message": "native plan skill prompt"}
        )
        manager = SessionRunManager(db_path=db_path)
        await manager.submit_turn(
            bridge=bridge,  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
            live_session_id="live-1",
            prompt="inspect this file",
            attachments=[
                {
                    "name": "notes.txt",
                    "size": 5,
                    "content_type": "text/plain",
                    "localPath": "/tmp/notes.txt",
                }
            ],
            existing_messages=[],
            mode="plan",
        )
        await _wait_for_calls(bridge, 2)
        await _wait_for_status(db_path, "completed")
        await manager.shutdown()
        return bridge

    bridge = _run(scenario())

    assert [method for method, _ in bridge.calls] == ["command.dispatch", "prompt.submit"]
    assert bridge.calls[0][1]["session_id"] == "live-1"
    assert bridge.calls[0][1]["name"] == "plan"
    assert ATTACHMENT_BLOCK_START in bridge.calls[0][1]["arg"]
    assert "inspect this file" in bridge.calls[0][1]["arg"]
    assert bridge.calls[1][1]["text"] == "native plan skill prompt"

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "inspect this file"
    assert "native plan skill prompt" not in messages[0]["content"]


@pytest.mark.parametrize(
    "dispatch_result",
    [
        {"type": "text", "message": "not a skill"},
        {"type": "skill", "message": ""},
    ],
)
def test_plan_mode_failure_marks_run_failed(dispatch_result: dict[str, Any]) -> None:
    db_path = _db_path("potato-run-manager-plan-failed-")

    from interface.display_store import get_display_messages, get_live_session_state
    from interface.session_run_manager import (
        PLAN_COMMAND_FAILURE_MESSAGE,
        SessionRunManager,
    )

    async def scenario() -> _SubmitBridge:
        bridge = _SubmitBridge(
            dispatch_result=dispatch_result,
            complete_on_submit=False,
        )
        manager = SessionRunManager(db_path=db_path)
        await manager.submit_turn(
            bridge=bridge,  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
            live_session_id="live-1",
            prompt="make a plan",
            attachments=[],
            existing_messages=[],
            mode="plan",
        )
        await _wait_for_calls(bridge, 1)
        await _wait_for_status(db_path, "failed")
        await manager.shutdown()
        return bridge

    bridge = _run(scenario())

    assert [method for method, _ in bridge.calls] == ["command.dispatch"]
    live_state = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "failed"
    assert live_state["last_error"] == PLAN_COMMAND_FAILURE_MESSAGE

    messages = get_display_messages("user-1", "session-1", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["role"] == "assistant")
    assert assistant["done"] is True
    assert PLAN_COMMAND_FAILURE_MESSAGE in assistant["content"]

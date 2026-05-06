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

    def __init__(self) -> None:
        self.remembered: list[tuple[str, str, str]] = []

    def add_event_listener(self, listener) -> str:
        return "listener-1"

    def remove_event_listener(self, listener_id: str) -> None:
        return None

    def remember_live_session(
        self,
        live_session_id: str,
        *,
        persistent_session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.remembered.append(
            (
                live_session_id,
                persistent_session_id or "",
                run_id or "",
            )
        )


def _run(coro):
    return asyncio.run(coro)


async def _wait_for(predicate, *, timeout_seconds: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()


def test_tip_reconcile_keeps_late_child_events_on_root_transcript() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-run-manager-tip-")) / "interface.db"
    os.environ["INTERFACE_AUTH_DB"] = str(db_path)

    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
        save_display_messages,
        save_live_session_state,
    )
    from interface.session_run_manager import SessionRunManager

    tip_by_root = {"session-root": "session-root"}

    def resolve_tip(user_id: str, session_id: str) -> str:
        assert user_id == "user-1"
        return tip_by_root.get(session_id, session_id)

    save_display_messages(
        "user-1",
        "session-root",
        [
            {"id": "user-msg", "role": "user", "content": "hello", "done": True},
            {"id": "assistant-1", "role": "assistant", "content": "", "done": False},
        ],
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-root",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="running",
        pending_approval=None,
        last_error="",
        last_event_seq=1,
        db_path=db_path,
    )

    async def scenario() -> _Bridge:
        bridge = _Bridge()
        manager = SessionRunManager(
            tip_resolver=resolve_tip,
            tip_reconcile_interval_seconds=0.02,
            db_path=db_path,
        )
        await manager.attach_bridge(bridge)  # type: ignore[arg-type]
        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "message.delta",
                "session_id": "live-1",
                "persistent_session_id": "session-root",
                "run_id": "run-1",
                "seq": 2,
                "payload": {"text": "before "},
            },
        )

        tip_by_root["session-root"] = "session-tip"
        await _wait_for(
            lambda: (
                get_live_session_state("user-1", "session-root", db_path=db_path)
                or {}
            ).get("tip_session_id")
            == "session-tip"
        )

        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "message.delta",
                "session_id": "session-tip",
                "persistent_session_id": "",
                "run_id": "",
                "seq": 3,
                "payload": {"text": "after"},
            },
        )
        await manager.handle_bridge_event(
            bridge,  # type: ignore[arg-type]
            {
                "type": "message.complete",
                "session_id": "session-tip",
                "persistent_session_id": "",
                "run_id": "",
                "seq": 4,
                "payload": {"text": "", "status": "complete"},
            },
        )
        await manager.shutdown()
        return bridge

    bridge = _run(scenario())

    live_state = get_live_session_state("user-1", "session-root", db_path=db_path)
    assert live_state is not None
    assert live_state["status"] == "completed"
    assert live_state["live_session_id"] == "live-1"
    assert live_state["tip_session_id"] == "session-tip"
    assert ("session-tip", "session-root", "run-1") in bridge.remembered

    messages = get_display_messages("user-1", "session-root", db_path=db_path)
    assert messages is not None
    assistant = next(message for message in messages if message["id"] == "assistant-1")
    assert "before after" in assistant["content"]
    assert assistant["done"] is True


def run() -> None:
    test_tip_reconcile_keeps_late_child_events_on_root_transcript()


if __name__ == "__main__":
    run()

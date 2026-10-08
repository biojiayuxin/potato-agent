from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


def _model(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "model": model_id.title(),
        "provider": "custom",
        "api_mode": "chat_completions",
        "context_length": 200000,
        "reasoning_effort": "medium",
    }


class _Bridge:
    user_id = "alice"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.bindings: dict[str, tuple[str, str]] = {}
        self.listener = None
        self.submitted = asyncio.Queue()

    def add_event_listener(self, listener) -> str:
        self.listener = listener
        return "listener"

    def remove_event_listener(self, listener_id: str) -> None:
        self.listener = None

    def remember_live_session(
        self, live_session_id: str, *, persistent_session_id: str = "", run_id: str = ""
    ) -> None:
        self.bindings[live_session_id] = (persistent_session_id, run_id)

    def forget_live_session(self, live_session_id: str) -> None:
        self.bindings.pop(live_session_id, None)

    async def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "command.dispatch":
            return {"type": "skill", "message": "expanded plan"}
        assert method == "prompt.submit"
        session_id, run_id = self.bindings[params["session_id"]]
        await self.listener({
            "type": "message.complete", "session_id": params["session_id"],
            "persistent_session_id": session_id, "run_id": run_id, "seq": 1,
            "payload": {"text": "done", "status": "complete"},
        })
        self.submitted.put_nowait(params)
        return {"status": "streaming"}


def _turn(bridge: _Bridge, session_id: str = "one", **extra) -> dict[str, Any]:
    return {
        "bridge": bridge, "user_id": bridge.user_id, "session_id": session_id,
        "live_session_id": f"live-{session_id}", "prompt": "hello",
        "attachments": [], "existing_messages": [], **extra,
    }


@pytest.mark.parametrize("mode", ["chat", "plan"])
def test_queued_turn_keeps_a_copy_of_selected_model(tmp_path: Path, mode: str) -> None:
    from interface.session_run_manager import SessionRunManager

    async def scenario() -> None:
        selected = _model("fast")
        manager = SessionRunManager(db_path=tmp_path / "interface.db", model_resolver=lambda *_: selected)
        bridge = _Bridge()
        release = asyncio.Event()
        original_submit = manager._submit_prompt_task

        async def delayed_submit(**kwargs):
            await release.wait()
            await original_submit(**kwargs)

        manager._submit_prompt_task = delayed_submit
        try:
            await manager.submit_turn(**_turn(bridge, mode=mode))
            selected.update(_model("deep"))
            release.set()
            submitted = await asyncio.wait_for(bridge.submitted.get(), 2)
            assert submitted["model_config"] == _model("fast")
            assert submitted["text"] == ("expanded plan" if mode == "plan" else "hello")
        finally:
            release.set()
            await manager.shutdown()

    asyncio.run(scenario())


def test_model_resolution_holds_only_the_same_conversation_lock(tmp_path: Path) -> None:
    from interface.session_run_manager import SessionRunManager

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        checked_selection = asyncio.Event()

        async def resolve(user_id, session_id):
            assert user_id == "alice"
            if session_id == "one":
                started.set()
                await release.wait()
            return _model("deep" if session_id == "one" else "fast")

        manager = SessionRunManager(db_path=tmp_path / "interface.db", model_resolver=resolve)
        bridge = _Bridge()

        async def check_selection_lock():
            async with manager.session_lock("alice", "one"):
                checked_selection.set()

        pending_turn = asyncio.create_task(manager.submit_turn(**_turn(bridge)))
        try:
            await asyncio.wait_for(started.wait(), 2)
            model_change = asyncio.create_task(check_selection_lock())
            await asyncio.wait_for(manager.submit_turn(**_turn(bridge, "two")), 2)
            second = await asyncio.wait_for(bridge.submitted.get(), 2)
            assert second["session_id"] == "live-two"
            assert second["model_config"] == _model("fast")
            assert not checked_selection.is_set()
            release.set()
            await asyncio.wait_for(pending_turn, 2)
            await asyncio.wait_for(model_change, 2)
            first = await asyncio.wait_for(bridge.submitted.get(), 2)
            assert first["model_config"] == _model("deep")
        finally:
            release.set()
            await manager.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("state", [
    {"status": "queued"},
    {"status": "starting"},
    {"status": "running"},
    {"status": "awaiting_approval"},
    {"status": "completed", "background_pending": True},
    {"status": "completed", "pending_approval": {"approval_id": "child", "subagent_id": "c"}},
])
def test_model_change_busy_is_scoped_to_conversation(tmp_path: Path, state: dict) -> None:
    from interface.display_store import save_live_session_state
    from interface.session_run_manager import SessionRunManager
    from interface.tui_gateway_bridge import TuiGatewayBridgeError

    db_path = tmp_path / "interface.db"
    save_live_session_state("alice", "one", db_path=db_path, **state)

    async def scenario() -> None:
        manager = SessionRunManager(db_path=db_path)
        async with manager.session_lock("alice", "one"):
            with pytest.raises(TuiGatewayBridgeError):
                await manager.assert_model_change_idle("alice", "one")
        async with manager.session_lock("alice", "two"):
            await manager.assert_model_change_idle("alice", "two")
        async with manager.session_lock("bob", "one"):
            await manager.assert_model_change_idle("bob", "one")

    asyncio.run(scenario())


def test_unpersisted_delegation_count_also_blocks_model_change(tmp_path: Path) -> None:
    from interface.session_run_manager import SessionRunManager
    from interface.tui_gateway_bridge import TuiGatewayBridgeError

    async def scenario() -> None:
        manager = SessionRunManager(db_path=tmp_path / "interface.db")
        manager._delegation_counts[("alice", "one")] = 1
        async with manager.session_lock("alice", "one"):
            with pytest.raises(TuiGatewayBridgeError):
                await manager.assert_model_change_idle("alice", "one")
            manager._delegation_counts.clear()
            await manager.assert_model_change_idle("alice", "one")

    asyncio.run(scenario())


def test_automatic_delegation_delivery_resolves_owner_model(tmp_path: Path) -> None:
    from interface.display_store import save_live_session_state
    from interface.session_run_manager import SessionRunManager

    db_path = tmp_path / "interface.db"
    save_live_session_state(
        "alice", "one", status="completed", background_pending=True,
        live_session_id="live-one", tip_session_id="compressed-one", db_path=db_path,
    )

    async def scenario() -> None:
        resolved = []

        async def resolve(user_id, session_id):
            resolved.append((user_id, session_id))
            return _model("deep")

        manager = SessionRunManager(db_path=db_path, model_resolver=resolve)
        bridge = _Bridge()
        try:
            await manager.handle_bridge_event(bridge, {
                "type": "delegation.ready", "persistent_session_id": "one",
                "session_id": "live-one", "payload": {"delegation_ids": ["result-1"]},
            })
            submitted = await asyncio.wait_for(bridge.submitted.get(), 2)
            assert resolved == [("alice", "one")]
            assert submitted["model_config"] == _model("deep")
            assert submitted["delegation_ids"] == ["result-1"]
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


def test_model_resolution_failure_does_not_queue_or_change_transcript(tmp_path: Path) -> None:
    from interface.display_store import get_display_messages, get_live_session_state
    from interface.session_run_manager import SessionRunManager

    db_path = tmp_path / "interface.db"

    async def scenario() -> None:
        def resolve(*_):
            raise ValueError("invalid whitelist")

        manager = SessionRunManager(db_path=db_path, model_resolver=resolve)
        bridge = _Bridge()
        try:
            with pytest.raises(ValueError, match="invalid whitelist"):
                await manager.submit_turn(**_turn(bridge))
            assert not bridge.calls
            assert get_display_messages("alice", "one", db_path) is None
            assert get_live_session_state("alice", "one", db_path) is None
        finally:
            await manager.shutdown()

    asyncio.run(scenario())

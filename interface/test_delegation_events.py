from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from interface.display_store import get_display_messages, get_live_poll_snapshot, get_live_session_state, save_live_session_state
from interface.session_run_manager import SessionRunManager
from interface.test_session_run_manager_plan_mode import _SubmitBridge
from interface.delegation_events import handle_event
from interface.tui_gateway_bridge import TuiGatewayBridgeError


def seed(db_path, status="completed"):
    save_live_session_state("user-1", "session-1", run_id="old", live_session_id="live-1",
                            assistant_message_id="old-assistant", status=status, db_path=db_path)


def event(kind, **payload):
    return {"type": kind, "session_id": "live-1", "persistent_session_id": "session-1",
            "seq": 10, "payload": payload}


@pytest.mark.parametrize("no_results", [False, True])
def test_completion_uses_managed_turn_without_fake_user_message(tmp_path, no_results):
    db_path = tmp_path / "interface.db"
    seed(db_path)

    class Bridge(_SubmitBridge):
        async def rpc(self, method, params):
            if no_results:
                self.calls.append((method, params))
                return {"status": "no_results"}
            return await super().rpc(method, params)

    async def scenario():
        bridge = Bridge()
        manager = SessionRunManager(db_path=db_path)
        await handle_event(manager, bridge, event("delegation.ready", delegation_ids=["saved-one"]))
        for _ in range(100):
            await asyncio.sleep(0.01)
            state = get_live_session_state("user-1", "session-1", db_path=db_path)
            if bridge.calls and state["status"] == "completed":
                break
        assert bridge.calls == [("prompt.submit", {"session_id": "live-1", "text": "", "delegation_ids": ["saved-one"]})]
        assert state["status"] == "completed"
        messages = get_display_messages("user-1", "session-1", db_path=db_path)
        assert [m["role"] for m in messages] == ([] if no_results else ["assistant"])
        if messages:
            assert messages[0]["content"] == "done"
    asyncio.run(scenario())


def test_ready_waits_while_user_turn_is_active(tmp_path):
    db_path = tmp_path / "interface.db"
    seed(db_path, "running")
    bridge = _SubmitBridge()
    asyncio.run(handle_event(SessionRunManager(db_path=db_path), bridge,
                             event("delegation.ready", delegation_ids=["saved"])))
    assert bridge.calls == []


def test_http_snapshot_keeps_polling_across_background_result_handoff(tmp_path):
    db_path = tmp_path / "interface.db"
    seed(db_path)

    async def scenario():
        bridge = _SubmitBridge(complete_on_submit=False)
        manager = SessionRunManager(db_path=db_path)
        await handle_event(manager, bridge, event("delegation.state", active=3, pending=0))
        # A new HTTP reader sees the flag even after the first parent turn ended.
        def snapshot():
            return get_live_poll_snapshot("user-1", "session-1", db_path=db_path)["live"]
        assert snapshot()["status"] == "completed"
        assert snapshot()["background_pending"]
        await handle_event(manager, bridge, event("delegation.state", active=0, pending=1))
        assert snapshot()["background_pending"]
        await handle_event(manager, bridge, event("delegation.ready", delegation_ids=["saved-one"]))
        for _ in range(100):
            if bridge.calls:
                break
            await asyncio.sleep(0.01)
        assert bridge.calls
        await manager.handle_bridge_event(bridge, event("message.start"))
        # Claiming all child results drops the background count while the
        # summary itself keeps the existing foreground polling alive.
        await handle_event(manager, bridge, event("delegation.state", active=0, pending=0))
        assert not snapshot()["background_pending"]
        assert snapshot()["status"] == "running"
        await manager.handle_bridge_event(bridge, event("message.complete", text="summary", status="complete"))
        assert snapshot()["status"] == "completed"
        assert not snapshot()["background_pending"]
        assert get_display_messages("user-1", "session-1", db_path=db_path)[-1]["content"] == "summary"

    asyncio.run(scenario())


@pytest.mark.parametrize("end", ["stop", "gateway_exit"])
def test_background_polling_clears_on_stop_or_gateway_exit(tmp_path, end):
    db_path = tmp_path / "interface.db"
    seed(db_path)

    class Bridge(_SubmitBridge):
        async def rpc(self, method, params):
            assert method == "session.interrupt"
            return {"status": "interrupted"}

    async def scenario():
        bridge = Bridge()
        manager = SessionRunManager(db_path=db_path)
        await handle_event(manager, bridge, event("delegation.state", active=1, pending=0))
        if end == "stop":
            await manager.interrupt_run(bridge=bridge, user_id="user-1", session_id="session-1")
            # Draining child threads must not restart polling after Stop.
            await handle_event(manager, bridge, event("delegation.state", active=1, pending=0))
        else:
            await manager.handle_bridge_event(bridge, event("gateway.exit"))
        assert not get_live_poll_snapshot("user-1", "session-1", db_path=db_path)["live"]["background_pending"]

    asyncio.run(scenario())


@pytest.mark.parametrize("expire", [False, True])
def test_background_approval_restores_completed_state_and_uses_exact_child_token(tmp_path, expire):
    db_path = tmp_path / "interface.db"
    seed(db_path)
    calls = []
    class Bridge:
        user_id = "user-1"
        async def rpc(self, method, params):
            calls.append((method, params))
            return {"resolved": 1}

    async def scenario():
        manager = SessionRunManager(db_path=db_path)
        bridge = Bridge()
        for suffix in ("one", "two"):
            await handle_event(manager, bridge, event("approval.request", subagent_id=f"child-{suffix}",
                               approval_id=f"token-{suffix}", command="rm -rf ./target"))
        state = get_live_session_state("user-1", "session-1", db_path=db_path)
        assert state["status"] == "awaiting_approval"
        assert state["pending_approval"]["approval_id"] == "token-one"
        for suffix in ("one", "two"):
            if expire:
                await handle_event(manager, bridge, event("approval.expired", subagent_id=f"child-{suffix}", approval_id=f"token-{suffix}"))
            else:
                await manager.respond_to_approval(bridge=bridge, user_id="user-1", session_id="session-1",
                                                  choice="deny", approval_id=f"token-{suffix}")
        state = get_live_session_state("user-1", "session-1", db_path=db_path)
        assert state["status"] == "completed"
        assert state["pending_approval"] is None
        if not expire:
            assert [params["subagent_id"] for _, params in calls] == ["child-one", "child-two"]
            assert [params["approval_id"] for _, params in calls] == ["token-one", "token-two"]
    asyncio.run(scenario())


def test_background_lease_keeps_idle_gateway_alive_and_releases_on_exit(monkeypatch):
    import time
    from interface import tui_gateway_bridge as mod
    calls = []
    monkeypatch.setattr(mod, "create_runtime_lease", lambda *args, **kwargs: calls.append(("create", kwargs)) or "lease")
    monkeypatch.setattr(mod, "heartbeat_runtime_lease", lambda *args, **kwargs: calls.append(("heartbeat", kwargs)))
    monkeypatch.setattr(mod, "finish_runtime_lease", lambda *args, **kwargs: calls.append(("finish", kwargs)))

    async def scenario():
        bridge = mod.TuiGatewayBridge(user_id="user-1", target=SimpleNamespace())
        generation = SimpleNamespace(number=1, state=mod._GatewayGenerationState.READY)
        bridge._generation = generation
        bridge._delegation_deadlines["live-1"] = time.monotonic() + 30
        await bridge._refresh_delegation_lease(generation)
        assert bridge.has_inflight_activity()
        assert bridge.has_reconfigure_conflict()
        assert calls[0][1]["lease_type"] == mod.BACKGROUND_JOB_LEASE
        bridge._delegation_lease_refreshed -= 20
        await bridge._refresh_delegation_lease(generation)
        assert calls[-1][0] == "heartbeat"
        await bridge._release_all_foreground_leases()
        assert calls[-1][0] == "finish"
        assert not bridge._delegation_deadlines
        assert not bridge._delegation_lease
        await bridge._refresh_delegation_lease(SimpleNamespace(number=0))
        assert len(calls) == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["message.complete", "error"])
def test_parent_start_and_finish_preserve_child_approval_until_gateway_exit(tmp_path, terminal):
    db_path = tmp_path / "interface.db"
    async def scenario():
        bridge = _SubmitBridge(complete_on_submit=False)
        manager = SessionRunManager(db_path=db_path)
        await manager.submit_turn(bridge=bridge, user_id="user-1", session_id="session-1",
                                  live_session_id="live-1", prompt="parent", attachments=[])
        for _ in range(100):
            if bridge.calls:
                break
            await asyncio.sleep(0.01)
        await manager.handle_bridge_event(bridge, event("approval.request", subagent_id="child", approval_id="child-token"))
        await manager.handle_bridge_event(bridge, event("message.start"))
        assert get_live_session_state("user-1", "session-1", db_path=db_path)["pending_approval"]["approval_id"] == "child-token"
        await manager.handle_bridge_event(bridge, event(terminal, text="parent done", status="complete", message="provider failed"))
        state = get_live_session_state("user-1", "session-1", db_path=db_path)
        assert state["status"] == "awaiting_approval"
        assert state["pending_approval"]["resume_status"] == ("completed" if terminal == "message.complete" else "failed")
        await manager.handle_bridge_event(bridge, event("gateway.exit"))
        state = get_live_session_state("user-1", "session-1", db_path=db_path)
        assert state["pending_approval"] is None
        assert state["status"] == ("completed" if terminal == "message.complete" else "failed")
        assert not manager._delegation_counts
    asyncio.run(scenario())


def test_stop_rejects_child_approval_waiting_for_the_state_lock(tmp_path):
    db_path = tmp_path / "interface.db"
    seed(db_path)
    manager = SessionRunManager(db_path=db_path)

    class Bridge(_SubmitBridge):
        async def rpc(self, method, params):
            assert method == "session.interrupt"
            self.late = asyncio.create_task(manager.handle_bridge_event(
                self, event("approval.request", subagent_id="stopped-child", approval_id="stopped-token")
            ))
            await asyncio.sleep(0.02)
            return {"status": "interrupted"}

    async def scenario():
        bridge = Bridge()
        await manager.handle_bridge_event(bridge, event("delegation.state", active=1))
        await manager.interrupt_run(bridge=bridge, user_id="user-1", session_id="session-1")
        await bridge.late
        # A completion already in transit must not admit another managed turn
        # between Stop and a second late approval request.
        await manager.handle_bridge_event(bridge, event("delegation.ready", delegation_ids=["stopped-result"]))
        await manager.handle_bridge_event(bridge, event("approval.request", subagent_id="stopped-child", approval_id="another-token"))
        await manager.handle_bridge_event(bridge, event("delegation.state", active=0, pending=0))
        await manager.handle_bridge_event(bridge, event("gateway.exit"))
        state = get_live_session_state("user-1", "session-1", db_path=db_path)
        assert state["status"] == "interrupted"
        assert state["pending_approval"] is None
        assert not state["background_pending"]
        assert not bridge.calls
        await manager.shutdown()

    asyncio.run(scenario())


def test_new_user_turn_after_stop_can_request_child_approval(tmp_path):
    db_path = tmp_path / "interface.db"
    seed(db_path, "interrupted")

    async def scenario():
        manager = SessionRunManager(db_path=db_path)
        bridge = _SubmitBridge(complete_on_submit=False)
        try:
            await manager.submit_turn(bridge=bridge, user_id="user-1", session_id="session-1",
                                      live_session_id="live-1", prompt="new task", attachments=[])
            for _ in range(100):
                if bridge.calls:
                    break
                await asyncio.sleep(0.01)
            assert bridge.calls
            await manager.handle_bridge_event(bridge, event("approval.request", subagent_id="new-child", approval_id="new-token"))
            state = get_live_session_state("user-1", "session-1", db_path=db_path)
            assert state["status"] == "awaiting_approval"
            assert state["pending_approval"]["approval_id"] == "new-token"
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("resume_status", ["interrupted", "completed", "running"])
def test_gateway_exit_clears_saved_child_approvals_after_counts_drain(tmp_path, resume_status):
    db_path = tmp_path / "interface.db"
    seed(db_path, resume_status)

    async def scenario():
        bridge = _SubmitBridge()
        manager = SessionRunManager(db_path=db_path)
        # Seed a legacy orphan, including one already saved before this fix.
        pending = {"approval_id": "stale", "subagent_id": "child", "resume_status": resume_status}
        save_live_session_state("user-1", "session-1", status="awaiting_approval",
                                pending_approval=pending, last_event_seq=50, db_path=db_path)
        save_live_session_state("other-user", "session-1", status="awaiting_approval",
                                pending_approval=pending, db_path=db_path)
        await manager.handle_bridge_event(bridge, event("delegation.state", active=0, pending=0))
        assert not manager._delegation_counts
        await manager.handle_bridge_event(bridge, {"type": "gateway.exit", "payload": {}})
        state = get_live_poll_snapshot("user-1", "session-1", db_path=db_path)["live"]
        assert state["status"] == ("failed" if resume_status == "running" else resume_status)
        assert state["pending_approval"] is None
        assert not state["background_pending"]
        # A seq=0 exit must not make HTTP polling reject the final snapshot.
        assert state["last_event_seq"] == 50
        assert get_live_session_state("other-user", "session-1", db_path=db_path)["pending_approval"] == pending

    asyncio.run(scenario())


@pytest.mark.parametrize("choice", ["deny", "once", "session", "always"])
@pytest.mark.parametrize("error", ["session not found", "RPC timed out"])
def test_orphan_approval_recovers_only_when_gateway_confirms_missing_session(tmp_path, choice, error):
    db_path = tmp_path / "interface.db"
    seed(db_path)

    class Bridge(_SubmitBridge):
        async def rpc(self, method, params):
            assert method == "approval.respond"
            raise TuiGatewayBridgeError(error)

    async def scenario():
        manager = SessionRunManager(db_path=db_path)
        bridge = Bridge()
        for suffix in ("one", "two"):
            await manager.handle_bridge_event(bridge, event("approval.request", subagent_id=f"child-{suffix}", approval_id=f"token-{suffix}"))
        with pytest.raises(TuiGatewayBridgeError, match="no longer pending" if error == "session not found" else error):
            await manager.respond_to_approval(bridge=bridge, user_id="user-1", session_id="session-1",
                                              choice=choice, approval_id="token-one")
        state = get_live_poll_snapshot("user-1", "session-1", db_path=db_path)["live"]
        if error == "session not found":
            assert state["status"] == "completed"
            assert state["pending_approval"] is None
            assert not state["background_pending"]
            assert not manager._delegation_counts
        else:
            assert state["status"] == "awaiting_approval"
            assert [item["approval_id"] for item in state["pending_approval"]["queue"]] == ["token-one", "token-two"]

    asyncio.run(scenario())

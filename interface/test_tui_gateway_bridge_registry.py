from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from interface import tui_gateway_bridge as bridge_mod
from interface.tui_gateway_bridge import (
    TuiGatewayBridge,
    TuiGatewayBridgeError,
    TuiGatewayBridgeRegistry,
    _GatewayGenerationState,
)


class _FakeStdin:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, value: str) -> int:
        if self.closed:
            raise BrokenPipeError("stdin is closed")
        self.lines.append(value)
        return len(value)

    def flush(self) -> None:
        if self.closed:
            raise BrokenPipeError("stdin is closed")

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = None
        self.stderr = None
        self.returncode: int | None = None
        self.signals: list[int] = []
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, value: int) -> None:
        self.signals.append(value)
        self.returncode = -value

    def wait(self, _timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -signal.SIGKILL


def _install_fake_processes(
    monkeypatch: pytest.MonkeyPatch,
    bridge: TuiGatewayBridge,
) -> list[_FakeProcess]:
    processes: list[_FakeProcess] = []

    def fake_popen(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(bridge_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bridge, "_build_command", lambda: ["fake-gateway"])
    monkeypatch.setattr(bridge, "_build_env", lambda: {})
    monkeypatch.setattr(bridge, "_build_cwd", lambda: Path("/tmp"))
    monkeypatch.setattr(bridge, "_start_reader_threads", lambda _generation: None)
    return processes


async def _wait_for_generation(
    bridge: TuiGatewayBridge,
    *,
    after: int = 0,
) -> Any:
    for _ in range(100):
        generation = bridge._generation
        if generation is not None and generation.number > after:
            return generation
        await asyncio.sleep(0)
    raise AssertionError("gateway generation was not created")


async def _mark_ready(bridge: TuiGatewayBridge, generation: Any) -> None:
    bridge._dispatch_message(
        generation,
        {
            "method": "event",
            "params": {"type": "gateway.ready", "payload": {"generation": generation.number}},
        },
    )
    await asyncio.sleep(0)


class _DummyBridge:
    def __init__(self) -> None:
        self._closed = False
        self._subscribers = 0
        self._busy = True
        self._pending = False
        self._starting = False
        self.close_calls = 0

    def subscriber_count(self) -> int:
        return self._subscribers

    def has_pending_requests(self) -> bool:
        return self._pending

    def has_reconfigure_conflict(self) -> bool:
        return self._pending or self._starting

    def has_inflight_activity(self) -> bool:
        return self._busy

    async def close(self) -> None:
        self.close_calls += 1
        self._closed = True


def test_bridge_environment_injects_runtime_profile_guards(monkeypatch) -> None:
    bridge = TuiGatewayBridge(
        user_id="user-1",
        target=SimpleNamespace(
            home_dir=Path("/home/hmx_alice"),
            hermes_home=Path("/home/hmx_alice/.hermes"),
            workdir=Path("/home/hmx_alice/work"),
            runtime_profile_path=Path("/opt/potato/profile.yaml"),
            browser_cdp_url="ws://127.0.0.1:9222/devtools/browser/local",
        ),
    )

    env = bridge._build_env()

    assert env["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    assert env["HERMES_SKIP_NODE_BOOTSTRAP"] == "1"
    assert env["HERMES_DISABLE_GATEWAY_PLATFORMS"] == "1"
    assert env["HERMES_DISABLE_MCP"] == "1"
    assert env["HERMES_DISABLE_CRON"] == "1"
    assert env["HERMES_DISABLE_KANBAN"] == "1"
    assert env["TERMINAL_ENV"] == "local"
    assert env["AGENT_BROWSER_ENGINE"] == "chrome"
    assert (
        env["BROWSER_CDP_URL"]
        == "ws://127.0.0.1:9222/devtools/browser/local"
    )
    assert env["CAMOFOX_URL"] == ""
    assert env["HERMES_BUNDLED_SKILLS"] == (
        "/opt/potato-hermes-lite/current/share/hermes/skills"
    )
    assert env["HERMES_OPTIONAL_SKILLS"] == (
        "/opt/potato-hermes-lite/current/share/hermes/optional-skills"
    )
    assert env["HERMES_AGENT_BROWSER_BIN_DIR"] == (
        "/opt/potato-hermes-lite/current/browser/bin"
    )
    assert env["AGENT_BROWSER_EXECUTABLE_PATH"] == (
        "/opt/potato-hermes-lite/current/browser/chrome/chrome-linux64/chrome"
    )
    assert env["HERMES_RUNTIME_PROFILE_PATH"] == "/opt/potato/profile.yaml"


@pytest.mark.asyncio
async def test_concurrent_waiters_share_startup_and_rpc_waits_for_ready(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    processes = _install_fake_processes(monkeypatch, bridge)

    first_waiter = asyncio.create_task(bridge.ensure_started())
    generation = await _wait_for_generation(bridge)
    second_waiter = asyncio.create_task(bridge.ensure_started())
    rpc_task = asyncio.create_task(bridge.rpc("session.resume", {"session_id": "session-1"}))
    await asyncio.sleep(0)

    assert len(processes) == 1
    assert processes[0].stdin.lines == []
    assert bridge.startup_in_progress() is True
    assert bridge.has_inflight_activity() is True

    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter
    assert second_waiter.done() is False

    await _mark_ready(bridge, generation)
    await second_waiter
    for _ in range(10):
        if processes[0].stdin.lines:
            break
        await asyncio.sleep(0)

    request = json.loads(processes[0].stdin.lines[0])
    bridge._dispatch_message(generation, {"id": request["id"], "result": {"ok": True}})

    assert await rpc_task == {"ok": True}
    assert len(processes) == 1
    await bridge.close()


@pytest.mark.asyncio
async def test_startup_timeout_is_configurable_and_generation_can_restart(monkeypatch) -> None:
    monkeypatch.delenv("INTERFACE_TUI_GATEWAY_STARTUP_TIMEOUT_SECONDS", raising=False)
    assert bridge_mod._startup_timeout_seconds() == 60.0
    monkeypatch.setenv("INTERFACE_TUI_GATEWAY_STARTUP_TIMEOUT_SECONDS", "0.01")

    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    processes = _install_fake_processes(monkeypatch, bridge)

    with pytest.raises(TuiGatewayBridgeError, match="did not become ready"):
        await bridge.ensure_started()

    first_generation = bridge._generation
    assert first_generation is not None
    assert first_generation.state is _GatewayGenerationState.EXITED
    assert bridge._closed is False
    assert processes[0].signals == [signal.SIGTERM]

    monkeypatch.setenv("INTERFACE_TUI_GATEWAY_STARTUP_TIMEOUT_SECONDS", "1")
    restart = asyncio.create_task(bridge.ensure_started())
    second_generation = await _wait_for_generation(bridge, after=first_generation.number)
    await _mark_ready(bridge, second_generation)
    await restart

    assert len(processes) == 2
    assert second_generation.state is _GatewayGenerationState.READY
    await bridge.close()


@pytest.mark.asyncio
async def test_reader_ready_seen_before_deadline_survives_delayed_loop_wakeup(
    monkeypatch,
) -> None:
    monkeypatch.setenv("INTERFACE_TUI_GATEWAY_STARTUP_TIMEOUT_SECONDS", "0.01")
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    processes = _install_fake_processes(monkeypatch, bridge)

    start = asyncio.create_task(bridge.ensure_started())
    generation = await _wait_for_generation(bridge)
    assert bridge._record_generation_ready(generation, {"skin": "default"}) is True

    await start

    assert generation.state is _GatewayGenerationState.READY
    assert generation.ready_seen is True
    assert processes[0].signals == []
    await bridge.close()


@pytest.mark.asyncio
async def test_natural_exit_restarts_and_ignores_old_generation_callbacks(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    processes = _install_fake_processes(monkeypatch, bridge)

    first_start = asyncio.create_task(bridge.ensure_started())
    first_generation = await _wait_for_generation(bridge)
    await _mark_ready(bridge, first_generation)
    await first_start

    processes[0].returncode = 1
    await bridge._handle_exit(first_generation)
    assert first_generation.state is _GatewayGenerationState.EXITED
    assert bridge._closed is False

    restart = asyncio.create_task(bridge.ensure_started())
    second_generation = await _wait_for_generation(bridge, after=first_generation.number)
    await _mark_ready(bridge, first_generation)
    await asyncio.sleep(0)

    assert restart.done() is False
    assert bridge._last_ready_payload is None

    await _mark_ready(bridge, second_generation)
    await restart
    assert second_generation.state is _GatewayGenerationState.READY
    assert len(processes) == 2
    await bridge.close()


@pytest.mark.asyncio
async def test_restart_waits_until_old_generation_exit_is_broadcast(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    processes = _install_fake_processes(monkeypatch, bridge)
    exit_listener_started = asyncio.Event()
    release_exit_listener = asyncio.Event()
    received_events: list[str] = []

    async def listener(event: dict[str, Any]) -> None:
        received_events.append(str(event.get("type") or ""))
        if event.get("type") == "gateway.exit":
            exit_listener_started.set()
            await release_exit_listener.wait()

    bridge.add_event_listener(listener)
    first_start = asyncio.create_task(bridge.ensure_started())
    first_generation = await _wait_for_generation(bridge)
    await _mark_ready(bridge, first_generation)
    await first_start

    processes[0].returncode = 1
    exit_waiter = asyncio.create_task(bridge._handle_exit(first_generation))
    await exit_listener_started.wait()
    exit_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await exit_waiter
    restart = asyncio.create_task(bridge.ensure_started())
    await asyncio.sleep(0)

    assert bridge._generation is first_generation
    assert len(processes) == 1
    assert restart.done() is False

    release_exit_listener.set()
    assert first_generation.exit_task is not None
    await first_generation.exit_task
    second_generation = await _wait_for_generation(
        bridge, after=first_generation.number
    )
    await _mark_ready(bridge, second_generation)
    await restart

    assert received_events.count("gateway.exit") == 1
    assert second_generation.state is _GatewayGenerationState.READY
    await bridge.close()


@pytest.mark.asyncio
async def test_close_during_startup_is_permanent(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    processes = _install_fake_processes(monkeypatch, bridge)

    waiter = asyncio.create_task(bridge.ensure_started())
    await _wait_for_generation(bridge)
    await bridge.close()

    with pytest.raises(TuiGatewayBridgeError, match="bridge is closed"):
        await waiter
    with pytest.raises(TuiGatewayBridgeError, match="bridge is closed"):
        await bridge.ensure_started()

    assert len(processes) == 1
    assert bridge._closed is True


@pytest.mark.asyncio
async def test_rpc_cancellation_clears_pending_and_late_completion_is_safe(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    processes = _install_fake_processes(monkeypatch, bridge)

    start = asyncio.create_task(bridge.ensure_started())
    generation = await _wait_for_generation(bridge)
    await _mark_ready(bridge, generation)
    await start

    rpc_task = asyncio.create_task(bridge.rpc("session.resume", {"session_id": "session-1"}))
    for _ in range(10):
        if processes[0].stdin.lines:
            break
        await asyncio.sleep(0)
    assert bridge.has_pending_requests() is True

    rpc_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await rpc_task
    assert bridge.has_pending_requests() is False

    request = json.loads(processes[0].stdin.lines[0])
    bridge._dispatch_message(generation, {"id": request["id"], "result": {"late": True}})
    cancelled_future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    cancelled_future.cancel()
    bridge._set_future_result_if_pending(cancelled_future, {"late": True})
    bridge._set_future_exception_if_pending(cancelled_future, TuiGatewayBridgeError("late"))
    await asyncio.sleep(0)

    assert cancelled_future.cancelled() is True
    await bridge.close()


@pytest.mark.asyncio
async def test_bridge_registry_waits_for_inflight_activity_before_close(monkeypatch) -> None:
    registry = TuiGatewayBridgeRegistry()
    bridge = _DummyBridge()
    registry._bridges["alice"] = bridge  # type: ignore[assignment]

    sleep_tokens: asyncio.Queue[None] = asyncio.Queue()

    async def fake_sleep(_seconds: float) -> None:
        await sleep_tokens.get()

    monkeypatch.setattr("interface.tui_gateway_bridge.asyncio.sleep", fake_sleep)

    await registry.maybe_close_if_unused("alice")
    close_task = registry._close_tasks.get("alice")
    assert close_task is not None

    await sleep_tokens.put(None)
    await asyncio.sleep(0)
    assert bridge.close_calls == 0
    assert registry._bridges.get("alice") is bridge

    bridge._busy = False
    await sleep_tokens.put(None)
    await close_task

    assert bridge.close_calls == 1
    assert registry._bridges.get("alice") is None
    assert registry._close_tasks.get("alice") is None


@pytest.mark.asyncio
async def test_gateway_exit_releases_foreground_leases(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    released: list[str] = []

    async def fake_release_all() -> None:
        released.append("all")

    monkeypatch.setattr(bridge, "_release_all_foreground_leases", fake_release_all)

    await bridge._handle_exit()

    assert released == ["all"]
    assert bridge.has_inflight_activity() is False


@pytest.mark.asyncio
async def test_terminal_error_releases_its_foreground_lease(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    _install_fake_processes(monkeypatch, bridge)
    released: list[str] = []
    monkeypatch.setattr(
        bridge,
        "_schedule_foreground_lease_release",
        lambda live_session_id, _generation: released.append(live_session_id),
    )

    start = asyncio.create_task(bridge.ensure_started())
    generation = await _wait_for_generation(bridge)
    await _mark_ready(bridge, generation)
    await start
    bridge._dispatch_message(
        generation,
        {
            "method": "event",
            "params": {
                "type": "error",
                "session_id": "live-1",
                "payload": {"message": "turn failed"},
            },
        },
    )

    assert released == ["live-1"]
    await bridge.close()


@pytest.mark.asyncio
async def test_listener_retries_a_transient_event_failure() -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    calls: list[str] = []

    async def listener(event: dict[str, Any]) -> None:
        calls.append(str(event.get("type") or ""))
        if len(calls) == 1:
            raise RuntimeError("database is locked")

    listener_id = bridge.add_event_listener(listener)
    await bridge._broadcast_event({"type": "message.delta", "payload": {}})
    await bridge._broadcast_event({"type": "message.complete", "payload": {}})

    assert calls == ["message.delta", "message.delta", "message.complete"]
    assert listener_id in bridge._event_listeners


@pytest.mark.asyncio
async def test_foreground_lease_heartbeat_retries_transient_error(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    calls = 0

    async def fake_sleep(_seconds: float) -> None:
        return None

    def fake_heartbeat(lease_id: str, *, ttl_seconds: int) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is locked")
        return False

    monkeypatch.setattr("interface.tui_gateway_bridge.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "interface.tui_gateway_bridge.heartbeat_runtime_lease",
        fake_heartbeat,
    )

    await bridge._foreground_chat_lease_heartbeat(
        "lease-1", ttl_seconds=90, interval_seconds=15
    )

    assert calls == 2


@pytest.mark.asyncio
async def test_foreground_lease_finish_retries_before_forgetting_lease(monkeypatch) -> None:
    bridge = TuiGatewayBridge(user_id="user-1", target=None)  # type: ignore[arg-type]
    heartbeat_blocker = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_blocker.wait())
    bridge._foreground_leases["live-1"] = ("lease-1", heartbeat_task)
    calls = 0

    def fake_finish(lease_id: str, *, user_id: str) -> bool:
        nonlocal calls
        calls += 1
        assert lease_id == "lease-1"
        assert user_id == "user-1"
        if calls == 1:
            raise RuntimeError("database is locked")
        return True

    monkeypatch.setattr(
        "interface.tui_gateway_bridge.finish_runtime_lease", fake_finish
    )

    await bridge._release_foreground_lease("live-1")

    assert calls == 2
    assert bridge.has_active_foreground_leases() is False
    assert heartbeat_task.cancelled() is True


@pytest.mark.asyncio
async def test_close_for_reconfigure_ignores_stale_foreground_lease_without_pending_rpc() -> None:
    registry = TuiGatewayBridgeRegistry()
    bridge = _DummyBridge()
    bridge._busy = True
    bridge._pending = False
    registry._bridges["alice"] = bridge  # type: ignore[assignment]

    closed = await registry.close_for_reconfigure("alice")

    assert closed is True
    assert bridge.close_calls == 1
    assert registry._bridges.get("alice") is None


@pytest.mark.asyncio
async def test_close_for_reconfigure_rejects_pending_rpc() -> None:
    registry = TuiGatewayBridgeRegistry()
    bridge = _DummyBridge()
    bridge._busy = True
    bridge._pending = True
    registry._bridges["alice"] = bridge  # type: ignore[assignment]

    closed = await registry.close_for_reconfigure("alice")

    assert closed is False
    assert bridge.close_calls == 0
    assert registry._bridges.get("alice") is bridge


@pytest.mark.asyncio
async def test_close_for_reconfigure_rejects_startup_in_progress() -> None:
    registry = TuiGatewayBridgeRegistry()
    bridge = _DummyBridge()
    bridge._busy = True
    bridge._starting = True
    registry._bridges["alice"] = bridge  # type: ignore[assignment]

    closed = await registry.close_for_reconfigure("alice")

    assert closed is False
    assert bridge.close_calls == 0
    assert registry._bridges.get("alice") is bridge


@pytest.mark.asyncio
async def test_close_for_cleanup_rejects_inflight_activity() -> None:
    registry = TuiGatewayBridgeRegistry()
    bridge = _DummyBridge()
    bridge._busy = True
    registry._bridges["alice"] = bridge  # type: ignore[assignment]

    closed = await registry.close_for_cleanup("alice")

    assert closed is False
    assert bridge.close_calls == 0
    assert registry._bridges.get("alice") is bridge


@pytest.mark.asyncio
async def test_close_for_cleanup_closes_idle_bridge() -> None:
    registry = TuiGatewayBridgeRegistry()
    bridge = _DummyBridge()
    bridge._busy = False
    registry._bridges["alice"] = bridge  # type: ignore[assignment]

    closed = await registry.close_for_cleanup("alice")

    assert closed is True
    assert bridge.close_calls == 1
    assert registry._bridges.get("alice") is None

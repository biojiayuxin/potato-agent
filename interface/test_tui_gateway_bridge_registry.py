from __future__ import annotations

import asyncio

import pytest

from interface.tui_gateway_bridge import TuiGatewayBridge, TuiGatewayBridgeRegistry


class _DummyBridge:
    def __init__(self) -> None:
        self._closed = False
        self._subscribers = 0
        self._busy = True
        self._pending = False
        self.close_calls = 0

    def subscriber_count(self) -> int:
        return self._subscribers

    def has_pending_requests(self) -> bool:
        return self._pending

    def has_inflight_activity(self) -> bool:
        return self._busy

    async def close(self) -> None:
        self.close_calls += 1
        self._closed = True


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

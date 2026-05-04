from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from interface.mapping import HermesTarget
from interface.runtime_state import (
    FOREGROUND_CHAT_LEASE,
    create_runtime_lease,
    heartbeat_runtime_lease,
    mark_user_message_activity,
    release_runtime_lease,
)


class TuiGatewayBridgeError(RuntimeError):
    pass


@dataclass
class _PendingRequest:
    future: asyncio.Future[dict[str, Any]]
    method: str
    created_at: float = field(default_factory=time.monotonic)


BridgeEventListener = Callable[[dict[str, Any]], Awaitable[None]]


class TuiGatewayBridge:
    def __init__(self, user_id: str, target: HermesTarget):
        self.user_id = user_id
        self.target = target
        self._proc: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._subscribers_lock = threading.Lock()
        self._subscribers: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_ready_payload: dict[str, Any] | None = None
        self._stderr_tail: list[str] = []
        self._ready_event: asyncio.Event | None = None
        self._closed = False
        self._started_at = 0.0
        self._last_event_at = 0.0
        self._foreground_leases_lock = threading.Lock()
        self._foreground_leases: dict[str, tuple[str, asyncio.Task[None]]] = {}
        self._event_listeners_lock = threading.Lock()
        self._event_listeners: dict[str, BridgeEventListener] = {}
        self._session_mapping_lock = threading.Lock()
        self._persistent_ids_by_live_session_id: dict[str, str] = {}
        self._run_ids_by_live_session_id: dict[str, str] = {}
        self._event_seq_lock = threading.Lock()
        self._event_seq = 0

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def last_event_at(self) -> float:
        return self._last_event_at

    def subscriber_count(self) -> int:
        with self._subscribers_lock:
            return len(self._subscribers)

    def has_pending_requests(self) -> bool:
        with self._pending_lock:
            return bool(self._pending)

    def has_active_foreground_leases(self) -> bool:
        with self._foreground_leases_lock:
            return bool(self._foreground_leases)

    def has_inflight_activity(self) -> bool:
        return self.has_pending_requests() or self.has_active_foreground_leases()

    async def ensure_started(self) -> None:
        if self._closed:
            raise TuiGatewayBridgeError("bridge is closed")
        if self._proc is not None and self._proc.poll() is None:
            return

        loop = asyncio.get_running_loop()
        self._loop = loop
        self._ready_event = asyncio.Event()
        self._last_ready_payload = None

        command = self._build_command()
        env = self._build_env()
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self.target.workdir),
            env=env,
        )
        self._started_at = time.monotonic()
        self._last_event_at = self._started_at
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=20.0)
        except TimeoutError as exc:
            await self.close()
            tail = "\n".join(self._stderr_tail[-20:]).strip()
            detail = (
                f"tui_gateway did not become ready in time. stderr tail:\n{tail}"
                if tail
                else "tui_gateway did not become ready in time"
            )
            raise TuiGatewayBridgeError(detail) from exc

    def _build_command(self) -> list[str]:
        python_bin = os.getenv("INTERFACE_TUI_GATEWAY_PYTHON") or "/opt/hermes-agent-venv/bin/python3"
        return [
            "runuser",
            "-u",
            self.target.linux_user,
            "--",
            "env",
            f"HOME={self.target.home_dir}",
            f"HERMES_HOME={self.target.hermes_home}",
            f"TERMINAL_CWD={self.target.workdir}",
            f"PATH={os.environ.get('PATH', '')}",
            "PYTHONUNBUFFERED=1",
            python_bin,
            "-m",
            "tui_gateway.entry",
        ]

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.target.home_dir)
        env["HERMES_HOME"] = str(self.target.hermes_home)
        env["TERMINAL_CWD"] = str(self.target.workdir)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    async def add_subscriber(self, websocket: WebSocket) -> None:
        with self._subscribers_lock:
            self._subscribers.add(websocket)
        if self._last_ready_payload is not None:
            await self._send_ws(
                websocket,
                {
                    "type": "gateway.ready",
                    "payload": self._last_ready_payload,
                },
            )

    def remove_subscriber(self, websocket: WebSocket) -> None:
        with self._subscribers_lock:
            self._subscribers.discard(websocket)

    def add_event_listener(self, listener: BridgeEventListener) -> str:
        listener_id = uuid.uuid4().hex
        with self._event_listeners_lock:
            self._event_listeners[listener_id] = listener
        return listener_id

    def remove_event_listener(self, listener_id: str) -> None:
        normalized_listener_id = str(listener_id or "").strip()
        if not normalized_listener_id:
            return
        with self._event_listeners_lock:
            self._event_listeners.pop(normalized_listener_id, None)

    def remember_live_session(
        self,
        live_session_id: str,
        *,
        persistent_session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        normalized_live_session_id = str(live_session_id or "").strip()
        if not normalized_live_session_id:
            return
        with self._session_mapping_lock:
            if persistent_session_id is not None:
                normalized_persistent_session_id = str(persistent_session_id or "").strip()
                if normalized_persistent_session_id:
                    self._persistent_ids_by_live_session_id[normalized_live_session_id] = (
                        normalized_persistent_session_id
                    )
            if run_id is not None:
                normalized_run_id = str(run_id or "").strip()
                if normalized_run_id:
                    self._run_ids_by_live_session_id[normalized_live_session_id] = normalized_run_id

    def forget_live_session(self, live_session_id: str) -> None:
        normalized_live_session_id = str(live_session_id or "").strip()
        if not normalized_live_session_id:
            return
        with self._session_mapping_lock:
            self._persistent_ids_by_live_session_id.pop(normalized_live_session_id, None)
            self._run_ids_by_live_session_id.pop(normalized_live_session_id, None)

    def get_persistent_session_id(self, live_session_id: str) -> str:
        normalized_live_session_id = str(live_session_id or "").strip()
        if not normalized_live_session_id:
            return ""
        with self._session_mapping_lock:
            return str(self._persistent_ids_by_live_session_id.get(normalized_live_session_id) or "")

    def get_run_id(self, live_session_id: str) -> str:
        normalized_live_session_id = str(live_session_id or "").strip()
        if not normalized_live_session_id:
            return ""
        with self._session_mapping_lock:
            return str(self._run_ids_by_live_session_id.get(normalized_live_session_id) or "")

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.ensure_started()
        if self._proc is None or self._proc.stdin is None:
            raise TuiGatewayBridgeError("tui_gateway process is not available")

        rpc_params = params or {}
        live_session_id = str(rpc_params.get("session_id") or "").strip()
        if method == "prompt.submit" and live_session_id:
            await self._start_foreground_lease(live_session_id)

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        with self._pending_lock:
            self._pending[request_id] = _PendingRequest(future=future, method=method)

        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            ensure_ascii=False,
        )
        try:
            with self._write_lock:
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TuiGatewayBridgeError(f"failed to write to tui_gateway: {exc}") from exc

        try:
            return await asyncio.wait_for(future, timeout=60.0)
        except Exception:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            if method == "prompt.submit" and live_session_id:
                await self._release_foreground_lease(live_session_id)
            raise

    async def close(self) -> None:
        self._closed = True
        await self._release_all_foreground_leases()
        proc = self._proc
        self._proc = None

        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()

        for entry in pending:
            if not entry.future.done():
                entry.future.set_exception(TuiGatewayBridgeError("tui_gateway bridge closed"))

        if proc is None:
            return

        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()

        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.to_thread(proc.wait, 3)
            except Exception:
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(proc.wait, 3)

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            self._last_event_at = time.monotonic()
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._dispatch_message(payload)
        self._dispatch_exit()

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            line = raw.rstrip("\n")
            if not line:
                continue
            self._last_event_at = time.monotonic()
            self._stderr_tail = (self._stderr_tail + [line])[-200:]
            self._dispatch_event({"type": "gateway.stderr", "payload": {"line": line}})

    def _dispatch_message(self, payload: dict[str, Any]) -> None:
        if payload.get("method") == "event":
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            event_type = str(params.get("type") or "")
            event_session_id = str(params.get("session_id") or "").strip()
            event_payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            if event_type == "gateway.ready":
                self._last_ready_payload = event_payload
                if self._ready_event is not None and self._loop is not None:
                    self._loop.call_soon_threadsafe(self._ready_event.set)
            persistent_session_id = self.get_persistent_session_id(event_session_id)
            run_id = self.get_run_id(event_session_id)
            if event_type == "message.complete" and event_session_id:
                self._schedule_foreground_lease_release(event_session_id)
            elif event_type == "gateway.exit":
                self._schedule_release_all_foreground_leases()
            with self._event_seq_lock:
                self._event_seq += 1
                event_seq = self._event_seq
            self._dispatch_event(
                {
                    "type": event_type,
                    "session_id": params.get("session_id"),
                    "payload": event_payload,
                    "persistent_session_id": persistent_session_id,
                    "run_id": run_id,
                    "seq": event_seq,
                }
            )
            if event_type == "message.complete" and event_session_id:
                self.forget_live_session(event_session_id)
            return

        request_id = str(payload.get("id") or "")
        if not request_id:
            return

        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if entry is None or self._loop is None:
            return

        if "error" in payload:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            message = str(error.get("message") or "RPC error")
            self._loop.call_soon_threadsafe(entry.future.set_exception, TuiGatewayBridgeError(message))
            return

        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        self._loop.call_soon_threadsafe(entry.future.set_result, result)

    async def _start_foreground_lease(self, live_session_id: str) -> None:
        normalized_session_id = str(live_session_id or "").strip()
        if not normalized_session_id:
            return
        with self._foreground_leases_lock:
            if normalized_session_id in self._foreground_leases:
                return
        await asyncio.to_thread(mark_user_message_activity, self.user_id)
        lease_id = await asyncio.to_thread(
            create_runtime_lease,
            self.user_id,
            lease_type=FOREGROUND_CHAT_LEASE,
            ttl_seconds=90,
            resource_id=normalized_session_id,
            meta={
                "mapping_username": self.target.username,
                "transport": "tui_gateway",
            },
        )
        heartbeat_task = asyncio.create_task(
            self._foreground_chat_lease_heartbeat(lease_id, ttl_seconds=90, interval_seconds=15)
        )
        with self._foreground_leases_lock:
            self._foreground_leases[normalized_session_id] = (lease_id, heartbeat_task)

    async def _release_foreground_lease(self, live_session_id: str) -> None:
        normalized_session_id = str(live_session_id or "").strip()
        if not normalized_session_id:
            return
        with self._foreground_leases_lock:
            lease = self._foreground_leases.pop(normalized_session_id, None)
        if lease is None:
            return
        lease_id, heartbeat_task = lease
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        await asyncio.to_thread(release_runtime_lease, lease_id)

    async def _release_all_foreground_leases(self) -> None:
        with self._foreground_leases_lock:
            live_session_ids = list(self._foreground_leases.keys())
        for live_session_id in live_session_ids:
            await self._release_foreground_lease(live_session_id)

    async def _foreground_chat_lease_heartbeat(
        self,
        lease_id: str,
        *,
        ttl_seconds: int,
        interval_seconds: int,
    ) -> None:
        try:
            while True:
                await asyncio.sleep(max(interval_seconds, 1))
                renewed = await asyncio.to_thread(
                    heartbeat_runtime_lease,
                    lease_id,
                    ttl_seconds=ttl_seconds,
                )
                if not renewed:
                    return
        except asyncio.CancelledError:
            raise

    def _schedule_foreground_lease_release(self, live_session_id: str) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            asyncio.create_task,
            self._release_foreground_lease(live_session_id),
        )

    def _schedule_release_all_foreground_leases(self) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            asyncio.create_task,
            self._release_all_foreground_leases(),
        )

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(asyncio.create_task, self._broadcast_event(event))

    def _dispatch_exit(self) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(asyncio.create_task, self._handle_exit())

    async def _handle_exit(self) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            if not entry.future.done():
                entry.future.set_exception(TuiGatewayBridgeError("tui_gateway process exited"))
        await self._broadcast_event({"type": "gateway.exit", "payload": {"user_id": self.user_id}})

    async def _broadcast_event(self, event: dict[str, Any]) -> None:
        with self._event_listeners_lock:
            listeners = list(self._event_listeners.items())
        stale_listener_ids: list[str] = []
        for listener_id, listener in listeners:
            try:
                await listener(event)
            except Exception:
                stale_listener_ids.append(listener_id)
        if stale_listener_ids:
            with self._event_listeners_lock:
                for listener_id in stale_listener_ids:
                    self._event_listeners.pop(listener_id, None)

        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        stale: list[WebSocket] = []
        for websocket in subscribers:
            try:
                await self._send_ws(websocket, event)
            except Exception:
                stale.append(websocket)
        if stale:
            with self._subscribers_lock:
                for websocket in stale:
                    self._subscribers.discard(websocket)

    async def _send_ws(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))


class TuiGatewayBridgeRegistry:
    def __init__(self) -> None:
        self._bridges: dict[str, TuiGatewayBridge] = {}
        self._close_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, user_id: str, target: HermesTarget) -> TuiGatewayBridge:
        async with self._lock:
            close_task = self._close_tasks.pop(user_id, None)
            if close_task is not None:
                close_task.cancel()
            bridge = self._bridges.get(user_id)
            if bridge is None or bridge._closed:
                bridge = TuiGatewayBridge(user_id=user_id, target=target)
                self._bridges[user_id] = bridge
        await bridge.ensure_started()
        return bridge

    async def maybe_close_if_unused(self, user_id: str) -> None:
        async with self._lock:
            bridge = self._bridges.get(user_id)
            if bridge is None:
                return
            if bridge.subscriber_count() > 0:
                return
            existing_task = self._close_tasks.get(user_id)
            if existing_task is not None and not existing_task.done():
                return
            self._close_tasks[user_id] = asyncio.create_task(
                self._close_if_still_unused_after_delay(user_id, bridge)
            )

    async def _close_if_still_unused_after_delay(
        self, user_id: str, bridge: TuiGatewayBridge
    ) -> None:
        current_task = asyncio.current_task()
        try:
            while True:
                await asyncio.sleep(15)
                async with self._lock:
                    current = self._bridges.get(user_id)
                    if current is not bridge:
                        return
                    if bridge.subscriber_count() > 0:
                        return
                    if bridge.has_inflight_activity():
                        continue
                    self._bridges.pop(user_id, None)
                    self._close_tasks.pop(user_id, None)
                    break
            await bridge.close()
        except asyncio.CancelledError:
            raise
        finally:
            async with self._lock:
                existing = self._close_tasks.get(user_id)
                if existing is current_task or (existing is not None and existing.done()):
                    self._close_tasks.pop(user_id, None)

    async def close_all(self) -> None:
        async with self._lock:
            bridges = list(self._bridges.values())
            self._bridges.clear()
            close_tasks = list(self._close_tasks.values())
            self._close_tasks.clear()
        for task in close_tasks:
            task.cancel()
        for bridge in bridges:
            await bridge.close()

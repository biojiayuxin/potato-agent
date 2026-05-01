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
from typing import Any

from fastapi import WebSocket

from interface.mapping import HermesTarget


class TuiGatewayBridgeError(RuntimeError):
    pass


@dataclass
class _PendingRequest:
    future: asyncio.Future[dict[str, Any]]
    method: str
    created_at: float = field(default_factory=time.monotonic)


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

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def last_event_at(self) -> float:
        return self._last_event_at

    def subscriber_count(self) -> int:
        with self._subscribers_lock:
            return len(self._subscribers)

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

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.ensure_started()
        if self._proc is None or self._proc.stdin is None:
            raise TuiGatewayBridgeError("tui_gateway process is not available")

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
            raise

    async def close(self) -> None:
        self._closed = True
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
            event_payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            if event_type == "gateway.ready":
                self._last_ready_payload = event_payload
                if self._ready_event is not None and self._loop is not None:
                    self._loop.call_soon_threadsafe(self._ready_event.set)
            self._dispatch_event(
                {
                    "type": event_type,
                    "session_id": params.get("session_id"),
                    "payload": event_payload,
                }
            )
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
        self._lock = asyncio.Lock()

    async def get_or_create(self, user_id: str, target: HermesTarget) -> TuiGatewayBridge:
        async with self._lock:
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
            self._bridges.pop(user_id, None)
        await bridge.close()

    async def close_all(self) -> None:
        async with self._lock:
            bridges = list(self._bridges.values())
            self._bridges.clear()
        for bridge in bridges:
            await bridge.close()

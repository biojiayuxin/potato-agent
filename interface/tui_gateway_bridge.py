from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from interface.hermes_profile import runtime_profile_environment
from interface.mapping import HermesTarget
from interface.privileged_client import privileged_client
from interface.runtime_state import (
    FOREGROUND_CHAT_LEASE,
    create_runtime_lease,
    finish_runtime_lease,
    heartbeat_runtime_lease,
    mark_user_message_activity,
)


class TuiGatewayBridgeError(RuntimeError):
    pass


class _GatewayGenerationState(Enum):
    STARTING = "starting"
    READY = "ready"
    EXITED = "exited"


@dataclass
class _GatewayGeneration:
    number: int
    proc: subprocess.Popen[str]
    ready_event: asyncio.Event
    state: _GatewayGenerationState = _GatewayGenerationState.STARTING
    startup_error: str = ""
    stderr_tail: list[str] = field(default_factory=list)
    exit_broadcast: bool = False
    exit_task: asyncio.Task[None] | None = field(default=None, repr=False)
    ready_seen: bool = False
    ready_logged: bool = False
    state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass
class _PendingRequest:
    future: asyncio.Future[dict[str, Any]]
    method: str
    generation: int
    created_at: float = field(default_factory=time.monotonic)


BridgeEventListener = Callable[[dict[str, Any]], Awaitable[None]]

_STARTUP_TIMEOUT_ENV = "INTERFACE_TUI_GATEWAY_STARTUP_TIMEOUT_SECONDS"
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 60.0
_EVENT_LISTENER_TIMEOUT_SECONDS = 180.0
_EVENT_LISTENER_RETRY_DELAYS_SECONDS = (0.0, 0.1, 0.5)
LOGGER = logging.getLogger("potato_interface.tui_gateway_bridge")


def _startup_timeout_seconds() -> float:
    raw = os.getenv(_STARTUP_TIMEOUT_ENV, str(_DEFAULT_STARTUP_TIMEOUT_SECONDS)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_STARTUP_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_STARTUP_TIMEOUT_SECONDS


def _force_helper_enabled() -> bool:
    return os.getenv("INTERFACE_FORCE_PRIVILEGED_HELPER", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


class TuiGatewayBridge:
    def __init__(self, user_id: str, target: HermesTarget):
        self.user_id = user_id
        self.target = target
        self._generation: _GatewayGeneration | None = None
        self._next_generation = 0
        self._startup_task: asyncio.Task[_GatewayGeneration] | None = None
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
        self._foreground_release_tasks: dict[str, asyncio.Task[None]] = {}
        self._event_listeners_lock = threading.Lock()
        self._event_listeners: dict[str, BridgeEventListener] = {}
        self._event_listener_delivery_locks: dict[str, asyncio.Lock] = {}
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

    def startup_in_progress(self) -> bool:
        startup_task = self._startup_task
        generation = self._generation
        return bool(
            (startup_task is not None and not startup_task.done())
            or (
                generation is not None
                and generation.state is _GatewayGenerationState.STARTING
            )
        )

    def has_reconfigure_conflict(self) -> bool:
        return self.startup_in_progress() or self.has_pending_requests()

    def has_inflight_activity(self) -> bool:
        return (
            self.startup_in_progress()
            or self.has_pending_requests()
            or self.has_active_foreground_leases()
        )

    async def ensure_started(self) -> None:
        await self._ensure_ready_generation()

    async def _ensure_ready_generation(self) -> _GatewayGeneration:
        if self._closed:
            raise TuiGatewayBridgeError("bridge is closed")

        startup_task = self._startup_task
        if startup_task is not None and not startup_task.done():
            return await asyncio.shield(startup_task)

        generation = self._generation
        if generation is not None and generation.state is _GatewayGenerationState.READY:
            if generation.proc.poll() is None:
                return generation
            await self._handle_exit(generation)
        elif generation is not None and generation.state is _GatewayGenerationState.EXITED:
            exit_task = generation.exit_task
            if exit_task is not None and not exit_task.done():
                await asyncio.shield(exit_task)

        startup_task = self._startup_task
        if startup_task is None or startup_task.done():
            startup_task = asyncio.create_task(self._start_generation())
            startup_task.add_done_callback(self._consume_startup_task_result)
            self._startup_task = startup_task

        return await asyncio.shield(startup_task)

    @staticmethod
    def _consume_startup_task_result(task: asyncio.Task[_GatewayGeneration]) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.exception()

    async def _start_generation(self) -> _GatewayGeneration:
        current_task = asyncio.current_task()
        generation: _GatewayGeneration | None = None
        try:
            if self._closed:
                raise TuiGatewayBridgeError("bridge is closed")

            existing = self._generation
            if (
                existing is not None
                and existing.state is _GatewayGenerationState.READY
                and existing.proc.poll() is None
            ):
                return existing

            loop = asyncio.get_running_loop()
            self._loop = loop
            self._last_ready_payload = None

            command = self._build_command()
            env = self._build_env()
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(self._build_cwd()),
                env=env,
            )
            self._next_generation += 1
            generation = _GatewayGeneration(
                number=self._next_generation,
                proc=proc,
                ready_event=asyncio.Event(),
            )
            self._generation = generation
            self._proc = proc
            self._ready_event = generation.ready_event
            self._stderr_tail = generation.stderr_tail
            self._started_at = time.monotonic()
            self._last_event_at = self._started_at
            LOGGER.info(
                "Starting tui_gateway generation %s for user %s",
                generation.number,
                self.user_id,
            )
            self._start_reader_threads(generation)

            try:
                await asyncio.wait_for(
                    generation.ready_event.wait(),
                    timeout=_startup_timeout_seconds(),
                )
            except TimeoutError as exc:
                with generation.state_lock:
                    ready_seen = generation.ready_seen
                if ready_seen:
                    generation.ready_event.set()
                else:
                    LOGGER.warning(
                        "tui_gateway generation %s for user %s timed out after %.1fs",
                        generation.number,
                        self.user_id,
                        _startup_timeout_seconds(),
                    )
                    await self._retire_generation(
                        generation,
                        reason="tui_gateway did not become ready in time",
                    )
                    tail = "\n".join(generation.stderr_tail[-20:]).strip()
                    detail = (
                        f"tui_gateway did not become ready in time. stderr tail:\n{tail}"
                        if tail
                        else "tui_gateway did not become ready in time"
                    )
                    raise TuiGatewayBridgeError(detail) from exc

            if self._closed:
                raise TuiGatewayBridgeError("bridge is closed")
            if (
                self._generation is not generation
                or generation.state is not _GatewayGenerationState.READY
            ):
                raise TuiGatewayBridgeError(
                    generation.startup_error or "tui_gateway process exited before ready"
                )
            return generation
        except asyncio.CancelledError:
            if (
                generation is not None
                and self._generation is generation
                and generation.state is not _GatewayGenerationState.EXITED
            ):
                await self._retire_generation(
                    generation,
                    reason="tui_gateway startup cancelled",
                )
            raise
        except Exception:
            if (
                generation is not None
                and self._generation is generation
                and generation.state is not _GatewayGenerationState.EXITED
            ):
                await self._retire_generation(
                    generation,
                    reason="tui_gateway failed during startup",
                )
            raise
        finally:
            if self._startup_task is current_task:
                self._startup_task = None

    def _build_command(self) -> list[str]:
        return privileged_client.tui_gateway_command(self.target)

    def _start_reader_threads(self, generation: _GatewayGeneration) -> None:
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(generation,),
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(generation,),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _build_cwd(self) -> Path:
        if os.geteuid() == 0 and not _force_helper_enabled():
            return self.target.workdir
        return Path(os.getenv("POTATO_AGENT_REPO_ROOT") or "/srv/potato_agent")

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.target.home_dir)
        env["HERMES_HOME"] = str(self.target.hermes_home)
        env["TERMINAL_CWD"] = str(self.target.workdir)
        env["PYTHONUNBUFFERED"] = "1"
        env.update(
            runtime_profile_environment(
                profile_path=self.target.runtime_profile_path,
                browser_cdp_url=self.target.browser_cdp_url,
            )
        )
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
            self._event_listener_delivery_locks[listener_id] = asyncio.Lock()
        return listener_id

    def remove_event_listener(self, listener_id: str) -> None:
        normalized_listener_id = str(listener_id or "").strip()
        if not normalized_listener_id:
            return
        with self._event_listeners_lock:
            self._event_listeners.pop(normalized_listener_id, None)
            self._event_listener_delivery_locks.pop(normalized_listener_id, None)

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
        generation = await self._ensure_ready_generation()
        proc = generation.proc
        if proc.stdin is None:
            raise TuiGatewayBridgeError("tui_gateway process is not available")

        rpc_params = params or {}
        live_session_id = str(rpc_params.get("session_id") or "").strip()
        if method == "prompt.submit" and live_session_id:
            await self._start_foreground_lease(live_session_id)

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        with self._pending_lock:
            self._pending[request_id] = _PendingRequest(
                future=future,
                method=method,
                generation=generation.number,
            )

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
                if (
                    self._closed
                    or self._generation is not generation
                    or generation.state is not _GatewayGenerationState.READY
                    or proc.poll() is not None
                ):
                    raise TuiGatewayBridgeError("tui_gateway process is not available")
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            if method == "prompt.submit" and live_session_id:
                await self._release_foreground_lease(live_session_id)
            if isinstance(exc, TuiGatewayBridgeError):
                raise
            raise TuiGatewayBridgeError(f"failed to write to tui_gateway: {exc}") from exc

        try:
            return await asyncio.wait_for(future, timeout=60.0)
        except asyncio.CancelledError:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            if method == "prompt.submit" and live_session_id:
                await self._release_foreground_lease(live_session_id)
            raise
        except Exception:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            if method == "prompt.submit" and live_session_id:
                await self._release_foreground_lease(live_session_id)
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        generation = self._generation
        if generation is not None:
            self._mark_generation_exited(generation, "tui_gateway bridge closed")
        await self._release_all_foreground_leases()
        if generation is None:
            self._fail_pending_requests("tui_gateway bridge closed")
            return
        await self._terminate_process(generation)

    async def _retire_generation(
        self,
        generation: _GatewayGeneration,
        *,
        reason: str,
    ) -> None:
        if self._generation is not generation:
            return
        self._mark_generation_exited(generation, reason)
        with contextlib.suppress(Exception):
            await self._release_all_foreground_leases()
        await self._terminate_process(generation)

    def _mark_generation_exited(
        self,
        generation: _GatewayGeneration,
        reason: str,
    ) -> None:
        with generation.state_lock:
            if generation.state is not _GatewayGenerationState.EXITED:
                generation.state = _GatewayGenerationState.EXITED
                generation.startup_error = reason
            elif not generation.startup_error:
                generation.startup_error = reason
        generation.ready_event.set()
        if self._generation is generation:
            self._proc = None
            self._last_ready_payload = None
        self._fail_pending_requests(reason, generation=generation.number)

    def _fail_pending_requests(
        self,
        reason: str,
        *,
        generation: int | None = None,
    ) -> None:
        with self._pending_lock:
            if generation is None:
                pending = list(self._pending.values())
                self._pending.clear()
            else:
                request_ids = [
                    request_id
                    for request_id, entry in self._pending.items()
                    if entry.generation == generation
                ]
                pending = [self._pending.pop(request_id) for request_id in request_ids]

        for entry in pending:
            self._set_future_exception_if_pending(
                entry.future,
                TuiGatewayBridgeError(reason),
            )

    @staticmethod
    def _set_future_result_if_pending(
        future: asyncio.Future[dict[str, Any]],
        result: dict[str, Any],
    ) -> None:
        if not future.done():
            future.set_result(result)

    @staticmethod
    def _set_future_exception_if_pending(
        future: asyncio.Future[dict[str, Any]],
        error: Exception,
    ) -> None:
        if not future.done():
            future.set_exception(error)

    async def _terminate_process(self, generation: _GatewayGeneration) -> None:
        proc = generation.proc

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

    def _read_stdout(self, generation: _GatewayGeneration) -> None:
        proc = generation.proc
        if proc.stdout is None:
            return
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            if self._generation is not generation:
                continue
            self._last_event_at = time.monotonic()
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._dispatch_message(generation, payload)
        self._dispatch_exit(generation)

    def _read_stderr(self, generation: _GatewayGeneration) -> None:
        proc = generation.proc
        if proc.stderr is None:
            return
        for raw in proc.stderr:
            line = raw.rstrip("\n")
            if not line:
                continue
            if self._generation is not generation:
                continue
            self._last_event_at = time.monotonic()
            generation.stderr_tail.append(line)
            del generation.stderr_tail[:-200]
            self._dispatch_event(
                {"type": "gateway.stderr", "payload": {"line": line}},
                generation=generation,
            )

    def _dispatch_message(
        self,
        generation: _GatewayGeneration,
        payload: dict[str, Any],
    ) -> None:
        if (
            self._generation is not generation
            or generation.state is _GatewayGenerationState.EXITED
        ):
            return
        if payload.get("method") == "event":
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            event_type = str(params.get("type") or "")
            event_session_id = str(params.get("session_id") or "").strip()
            event_payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
            if event_type == "gateway.ready":
                if self._record_generation_ready(generation, event_payload):
                    self._schedule_generation_callback(
                        generation,
                        self._mark_generation_ready,
                        event_payload,
                    )
            persistent_session_id = self.get_persistent_session_id(event_session_id)
            run_id = self.get_run_id(event_session_id)
            if event_type in {"message.complete", "error"} and event_session_id:
                self._schedule_foreground_lease_release(event_session_id, generation)
            elif event_type == "gateway.exit":
                self._schedule_release_all_foreground_leases(generation)
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
                },
                generation=generation,
            )
            if event_type == "message.complete" and event_session_id:
                self.forget_live_session(event_session_id)
            return

        request_id = str(payload.get("id") or "")
        if not request_id:
            return

        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if (
            entry is None
            or entry.generation != generation.number
            or self._loop is None
        ):
            return

        if "error" in payload:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            message = str(error.get("message") or "RPC error")
            self._loop.call_soon_threadsafe(
                self._set_future_exception_if_pending,
                entry.future,
                TuiGatewayBridgeError(message),
            )
            return

        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        self._loop.call_soon_threadsafe(
            self._set_future_result_if_pending,
            entry.future,
            result,
        )

    def _mark_generation_ready(
        self,
        generation: _GatewayGeneration,
        payload: dict[str, Any],
    ) -> None:
        with generation.state_lock:
            if (
                self._closed
                or self._generation is not generation
                or generation.state is not _GatewayGenerationState.READY
            ):
                return
            should_log = not generation.ready_logged
            generation.ready_logged = True
        generation.ready_event.set()
        if should_log:
            LOGGER.info(
                "tui_gateway generation %s for user %s ready after %.3fs",
                generation.number,
                self.user_id,
                max(time.monotonic() - self._started_at, 0.0),
            )

    def _record_generation_ready(
        self,
        generation: _GatewayGeneration,
        payload: dict[str, Any],
    ) -> bool:
        with generation.state_lock:
            if (
                self._closed
                or self._generation is not generation
                or generation.state is _GatewayGenerationState.EXITED
                or generation.proc.poll() is not None
            ):
                return False
            generation.ready_seen = True
            generation.state = _GatewayGenerationState.READY
            self._last_ready_payload = payload
            return True

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
        release_task = self._foreground_release_tasks.get(normalized_session_id)
        if release_task is None or release_task.done():
            release_task = asyncio.create_task(
                self._finish_foreground_lease(normalized_session_id)
            )
            self._foreground_release_tasks[normalized_session_id] = release_task

            def forget_release_task(completed: asyncio.Task[None]) -> None:
                if self._foreground_release_tasks.get(normalized_session_id) is completed:
                    self._foreground_release_tasks.pop(normalized_session_id, None)
                self._consume_startup_task_result(completed)

            release_task.add_done_callback(forget_release_task)
        await asyncio.shield(release_task)

    async def _finish_foreground_lease(self, normalized_session_id: str) -> None:
        with self._foreground_leases_lock:
            lease = self._foreground_leases.get(normalized_session_id)
        if lease is None:
            return
        lease_id, heartbeat_task = lease
        retry_delay = 0.1
        while True:
            try:
                await asyncio.to_thread(
                    finish_runtime_lease,
                    lease_id,
                    user_id=self.user_id,
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "Failed to finish foreground lease %s for user %s; retrying",
                    lease_id,
                    self.user_id,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 5.0)

        with self._foreground_leases_lock:
            if self._foreground_leases.get(normalized_session_id) == lease:
                self._foreground_leases.pop(normalized_session_id, None)
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

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
                try:
                    renewed = await asyncio.to_thread(
                        heartbeat_runtime_lease,
                        lease_id,
                        ttl_seconds=ttl_seconds,
                    )
                except Exception:
                    LOGGER.exception(
                        "Failed to heartbeat foreground lease %s for user %s",
                        lease_id,
                        self.user_id,
                    )
                    continue
                if not renewed:
                    return
        except asyncio.CancelledError:
            raise

    def _schedule_generation_callback(
        self,
        generation: _GatewayGeneration,
        callback: Callable[..., None],
        *args: Any,
    ) -> None:
        loop = self._loop
        if loop is None:
            return

        def run_if_current() -> None:
            if self._generation is generation:
                callback(generation, *args)

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(run_if_current)

    def _schedule_generation_coroutine(
        self,
        generation: _GatewayGeneration,
        factory: Callable[[], Awaitable[None]],
    ) -> None:
        loop = self._loop
        if loop is None:
            return

        def run_if_current() -> None:
            if self._generation is generation:
                asyncio.create_task(factory())

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(run_if_current)

    def _schedule_foreground_lease_release(
        self,
        live_session_id: str,
        generation: _GatewayGeneration,
    ) -> None:
        self._schedule_generation_coroutine(
            generation,
            lambda: self._release_foreground_lease(live_session_id),
        )

    def _schedule_release_all_foreground_leases(
        self,
        generation: _GatewayGeneration,
    ) -> None:
        self._schedule_generation_coroutine(
            generation,
            self._release_all_foreground_leases,
        )

    def _dispatch_event(
        self,
        event: dict[str, Any],
        *,
        generation: _GatewayGeneration | None = None,
    ) -> None:
        loop = self._loop
        if loop is None:
            return

        def schedule() -> None:
            if generation is not None and self._generation is not generation:
                return
            asyncio.create_task(self._broadcast_event(event, generation=generation))

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(schedule)

    def _dispatch_exit(self, generation: _GatewayGeneration) -> None:
        self._schedule_generation_coroutine(
            generation,
            lambda: self._handle_exit(generation),
        )

    async def _handle_exit(
        self,
        generation: _GatewayGeneration | None = None,
    ) -> None:
        if generation is None:
            generation = self._generation
            if generation is None:
                with contextlib.suppress(Exception):
                    await self._release_all_foreground_leases()
                self._fail_pending_requests("tui_gateway process exited")
                await self._broadcast_event(
                    {"type": "gateway.exit", "payload": {"user_id": self.user_id}}
                )
                return
        if self._generation is not generation:
            exit_task = generation.exit_task
            if exit_task is not None:
                await asyncio.shield(exit_task)
            return
        exit_task = generation.exit_task
        if exit_task is None:
            exit_task = asyncio.create_task(self._run_generation_exit(generation))
            exit_task.add_done_callback(self._consume_startup_task_result)
            generation.exit_task = exit_task
        await asyncio.shield(exit_task)

    async def _run_generation_exit(self, generation: _GatewayGeneration) -> None:
        LOGGER.warning(
            "tui_gateway generation %s for user %s exited",
            generation.number,
            self.user_id,
        )
        self._mark_generation_exited(generation, "tui_gateway process exited")
        if not generation.exit_broadcast:
            generation.exit_broadcast = True
            await self._broadcast_event(
                {"type": "gateway.exit", "payload": {"user_id": self.user_id}},
                generation=generation,
            )
        await self._release_all_foreground_leases()
        await self._terminate_process(generation)

    async def _broadcast_event(
        self,
        event: dict[str, Any],
        *,
        generation: _GatewayGeneration | None = None,
    ) -> None:
        if generation is not None and self._generation is not generation:
            return
        if (
            generation is not None
            and event.get("type") == "gateway.ready"
            and generation.state is not _GatewayGenerationState.READY
        ):
            return
        with self._event_listeners_lock:
            listeners = [
                (
                    listener_id,
                    listener,
                    self._event_listener_delivery_locks.get(listener_id),
                )
                for listener_id, listener in self._event_listeners.items()
            ]
        for listener_id, listener, delivery_lock in listeners:
            if delivery_lock is None:
                continue
            async with delivery_lock:
                for attempt, retry_delay in enumerate(
                    _EVENT_LISTENER_RETRY_DELAYS_SECONDS, start=1
                ):
                    if retry_delay > 0:
                        await asyncio.sleep(retry_delay)
                    try:
                        await asyncio.wait_for(
                            listener(event),
                            timeout=_EVENT_LISTENER_TIMEOUT_SECONDS,
                        )
                        break
                    except Exception:
                        if attempt == len(_EVENT_LISTENER_RETRY_DELAYS_SECONDS):
                            LOGGER.exception(
                                "Bridge event listener %s failed for user %s after %s attempts",
                                listener_id,
                                self.user_id,
                                attempt,
                            )
                        else:
                            LOGGER.warning(
                                "Bridge event listener %s failed for user %s; retrying (%s/%s)",
                                listener_id,
                                self.user_id,
                                attempt,
                                len(_EVENT_LISTENER_RETRY_DELAYS_SECONDS),
                                exc_info=True,
                            )
            if generation is not None and self._generation is not generation:
                return

        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        stale: list[WebSocket] = []
        for websocket in subscribers:
            try:
                await self._send_ws(websocket, event)
            except Exception:
                stale.append(websocket)
            if generation is not None and self._generation is not generation:
                return
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

    async def get_existing(self, user_id: str) -> TuiGatewayBridge | None:
        async with self._lock:
            return self._bridges.get(user_id)

    async def close_for_reconfigure(self, user_id: str) -> bool:
        async with self._lock:
            bridge = self._bridges.get(user_id)
            if bridge is None:
                return True
            if bridge.has_reconfigure_conflict():
                return False
            self._bridges.pop(user_id, None)
            close_task = self._close_tasks.pop(user_id, None)

        if close_task is not None:
            close_task.cancel()
        await bridge.close()
        return True

    async def close_for_cleanup(self, user_id: str) -> bool:
        async with self._lock:
            bridge = self._bridges.get(user_id)
            if bridge is not None and bridge.has_inflight_activity():
                return False
            self._bridges.pop(user_id, None)
            close_task = self._close_tasks.pop(user_id, None)

        if close_task is not None:
            close_task.cancel()
        if bridge is not None:
            await bridge.close()
        return True

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

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import threading
import time
import uuid
from typing import Any


_PATCH_APPLIED = False


def apply_patch() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    from aiohttp import web
    from pydantic import BaseModel

    from gateway.platforms import api_server as api_server_mod
    from tools import approval as approval_mod

    APIServerAdapter = api_server_mod.APIServerAdapter

    original_init = APIServerAdapter.__init__
    original_run_agent = APIServerAdapter._run_agent
    original_connect = APIServerAdapter.connect

    approval_route_name = "potato.api.approvals.resolve"
    approval_ttl_seconds = 300

    class ApprovalDecisionBody(BaseModel):
        choice: str

    def _cleanup_pending_approvals(self: Any) -> None:
        now = time.time()
        with self._approval_state_lock:
            stale_ids = [
                approval_id
                for approval_id, payload in self._pending_approvals.items()
                if now - float(payload.get("created_at") or 0) > approval_ttl_seconds
            ]
            for approval_id in stale_ids:
                self._pending_approvals.pop(approval_id, None)

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._approval_state_lock = threading.Lock()
        self._pending_approvals: dict[str, dict[str, Any]] = {}

    async def _resolve_approval(self: Any, request: web.Request) -> web.Response:
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        approval_id = str(request.match_info.get("approval_id") or "").strip()
        if not approval_id:
            return web.json_response(
                {"error": {"message": "Missing approval id", "type": "invalid_request_error"}},
                status=400,
            )

        try:
            body = ApprovalDecisionBody.model_validate(await request.json())
        except Exception:
            return web.json_response(
                {"error": {"message": "Invalid approval payload", "type": "invalid_request_error"}},
                status=400,
            )

        choice = str(body.choice or "").strip().lower()
        if choice not in {"once", "session", "always", "deny"}:
            return web.json_response(
                {
                    "error": {
                        "message": "choice must be one of once, session, always, deny",
                        "type": "invalid_request_error",
                    }
                },
                status=400,
            )

        _cleanup_pending_approvals(self)
        with self._approval_state_lock:
            approval = self._pending_approvals.get(approval_id)
            if approval is None:
                return web.json_response(
                    {"error": {"message": "Approval not found or expired", "type": "invalid_request_error"}},
                    status=404,
                )
            if approval.get("status") != "pending":
                return web.json_response(
                    {"error": {"message": "Approval already resolved", "type": "invalid_request_error"}},
                    status=409,
                )
            approval["status"] = "resolved"
            approval["resolved_at"] = time.time()
            approval["choice"] = choice
            session_key = str(approval.get("session_key") or "")

        try:
            count = approval_mod.resolve_gateway_approval(
                session_key, choice, resolve_all=False
            )
        except Exception as exc:
            with self._approval_state_lock:
                current = self._pending_approvals.get(approval_id)
                if current is not None and current.get("status") == "resolved":
                    current["status"] = "pending"
                    current.pop("resolved_at", None)
                    current.pop("choice", None)
            return web.json_response(
                {"error": {"message": str(exc), "type": "server_error"}},
                status=500,
            )

        if count <= 0:
            with self._approval_state_lock:
                current = self._pending_approvals.get(approval_id)
                if current is not None and current.get("status") == "resolved":
                    current["status"] = "pending"
                    current.pop("resolved_at", None)
                    current.pop("choice", None)
            return web.json_response(
                {"error": {"message": "Approval is no longer pending", "type": "invalid_request_error"}},
                status=409,
            )

        return web.json_response({"ok": True, "approval_id": approval_id, "choice": choice})

    async def patched_run_agent(
        self: Any,
        user_message: str,
        conversation_history: list[dict[str, str]],
        ephemeral_system_prompt: str | None = None,
        session_id: str | None = None,
        stream_delta_callback=None,
        tool_progress_callback=None,
        agent_ref: list[Any] | None = None,
    ) -> tuple[Any, Any]:
        loop = asyncio.get_event_loop()

        stream_queue = None
        closure_vars = inspect.getclosurevars(stream_delta_callback) if callable(stream_delta_callback) else None
        if closure_vars:
            stream_queue = closure_vars.nonlocals.get("_stream_q")

        if stream_queue is None:
            return await original_run_agent(
                self,
                user_message=user_message,
                conversation_history=conversation_history,
                ephemeral_system_prompt=ephemeral_system_prompt,
                session_id=session_id,
                stream_delta_callback=stream_delta_callback,
                tool_progress_callback=tool_progress_callback,
                agent_ref=agent_ref,
            )

        def _queue_approval_event(payload: dict[str, Any]) -> None:
            if stream_queue is None:
                return
            try:
                stream_queue.put(("__approval_required__", payload))
            except Exception:
                pass

        def _run() -> tuple[Any, Any]:
            _cleanup_pending_approvals(self)
            agent = self._create_agent(
                ephemeral_system_prompt=ephemeral_system_prompt,
                session_id=session_id,
                stream_delta_callback=stream_delta_callback,
                tool_progress_callback=tool_progress_callback,
            )
            if agent_ref is not None:
                agent_ref[0] = agent

            approval_session_key = f"api_server:{session_id or uuid.uuid4().hex}:{uuid.uuid4().hex}"

            def _approval_notify_sync(approval_data: dict[str, Any]) -> None:
                approval_id = f"appr_{uuid.uuid4().hex}"
                payload = {
                    "approval_id": approval_id,
                    "session_id": session_id or "",
                    "command": str(approval_data.get("command") or ""),
                    "description": str(approval_data.get("description") or "dangerous command"),
                    "pattern_key": str(approval_data.get("pattern_key") or ""),
                    "pattern_keys": [
                        str(item)
                        for item in approval_data.get("pattern_keys", [])
                        if str(item).strip()
                    ],
                    "options": ["once", "session", "always", "deny"],
                }
                with self._approval_state_lock:
                    self._pending_approvals[approval_id] = {
                        **payload,
                        "created_at": time.time(),
                        "status": "pending",
                        "session_key": approval_session_key,
                    }
                _queue_approval_event(payload)

            token = approval_mod.set_current_session_key(approval_session_key)
            approval_mod.register_gateway_notify(
                approval_session_key, _approval_notify_sync
            )
            try:
                result = agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history,
                    task_id="default",
                )
            finally:
                approval_mod.unregister_gateway_notify(approval_session_key)
                approval_mod.reset_current_session_key(token)
                with self._approval_state_lock:
                    stale_ids = [
                        approval_id
                        for approval_id, payload in self._pending_approvals.items()
                        if payload.get("session_key") == approval_session_key
                    ]
                    for approval_id in stale_ids:
                        self._pending_approvals.pop(approval_id, None)

            usage = {
                "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
            }
            return result, usage

        return await loop.run_in_executor(None, _run)

    async def patched_connect(self: Any) -> bool:
        if not api_server_mod.AIOHTTP_AVAILABLE:
            api_server_mod.logger.warning("[%s] aiohttp not installed", self.name)
            return False

        try:
            mws = [
                mw
                for mw in (
                    api_server_mod.cors_middleware,
                    api_server_mod.body_limit_middleware,
                    api_server_mod.security_headers_middleware,
                )
                if mw is not None
            ]
            self._app = web.Application(middlewares=mws)
            self._app["api_server_adapter"] = self
            self._app.router.add_get("/health", self._handle_health)
            self._app.router.add_get("/v1/health", self._handle_health)
            self._app.router.add_get("/v1/models", self._handle_models)
            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
            self._app.router.add_post("/v1/responses", self._handle_responses)
            self._app.router.add_get("/v1/responses/{response_id}", self._handle_get_response)
            self._app.router.add_delete(
                "/v1/responses/{response_id}", self._handle_delete_response
            )
            self._app.router.add_get("/api/jobs", self._handle_list_jobs)
            self._app.router.add_post("/api/jobs", self._handle_create_job)
            self._app.router.add_get("/api/jobs/{job_id}", self._handle_get_job)
            self._app.router.add_patch("/api/jobs/{job_id}", self._handle_update_job)
            self._app.router.add_delete("/api/jobs/{job_id}", self._handle_delete_job)
            self._app.router.add_post("/api/jobs/{job_id}/pause", self._handle_pause_job)
            self._app.router.add_post(
                "/api/jobs/{job_id}/resume", self._handle_resume_job
            )
            self._app.router.add_post("/api/jobs/{job_id}/run", self._handle_run_job)
            self._app.router.add_post("/v1/runs", self._handle_runs)
            self._app.router.add_get("/v1/runs/{run_id}/events", self._handle_run_events)
            self._app.router.add_post(
                "/v1/approvals/{approval_id}",
                self._handle_resolve_approval,
                name=approval_route_name,
            )

            sweep_task = asyncio.create_task(self._sweep_orphaned_runs())
            try:
                self._background_tasks.add(sweep_task)
            except TypeError:
                pass
            if hasattr(sweep_task, "add_done_callback"):
                sweep_task.add_done_callback(self._background_tasks.discard)

            if api_server_mod.is_network_accessible(self._host) and not self._api_key:
                api_server_mod.logger.error(
                    "[%s] Refusing to start: binding to %s requires API_SERVER_KEY. "
                    "Set API_SERVER_KEY or use the default 127.0.0.1.",
                    self.name,
                    self._host,
                )
                return False

            if api_server_mod.is_network_accessible(self._host) and self._api_key:
                try:
                    from hermes_cli.auth import has_usable_secret

                    if not has_usable_secret(self._api_key, min_length=8):
                        api_server_mod.logger.error(
                            "[%s] Refusing to start: API_SERVER_KEY is set to a "
                            "placeholder value. Generate a real secret "
                            "(e.g. `openssl rand -hex 32`) and set API_SERVER_KEY "
                            "before exposing the API server on %s.",
                            self.name,
                            self._host,
                        )
                        return False
                except ImportError:
                    pass

            try:
                with api_server_mod._socket.socket(
                    api_server_mod._socket.AF_INET, api_server_mod._socket.SOCK_STREAM
                ) as sock:
                    sock.settimeout(1)
                    sock.connect(("127.0.0.1", self._port))
                api_server_mod.logger.error(
                    "[%s] Port %d already in use. Set a different port in config.yaml: "
                    "platforms.api_server.port",
                    self.name,
                    self._port,
                )
                return False
            except (ConnectionRefusedError, OSError):
                pass

            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self._host, self._port)
            await self._site.start()

            self._mark_connected()
            if not self._api_key:
                api_server_mod.logger.warning(
                    "[%s] No API key configured (API_SERVER_KEY / platforms.api_server.key). "
                    "All requests will be accepted without authentication. "
                    "Set an API key for production deployments to prevent "
                    "unauthorized access to sessions, responses, and cron jobs.",
                    self.name,
                )
            api_server_mod.logger.info(
                "[%s] API server listening on http://%s:%d (model: %s)",
                self.name,
                self._host,
                self._port,
                self._model_name,
            )
            return True
        except Exception as exc:
            api_server_mod.logger.error("[%s] Failed to start API server: %s", self.name, exc)
            return False

    async def patched_write_sse(self: Any, *args: Any, **kwargs: Any) -> Any:
        request = args[0]
        completion_id = args[1]
        model = args[2]
        created = args[3]
        stream_q = args[4]
        agent_task = args[5]
        agent_ref = kwargs.get("agent_ref") if "agent_ref" in kwargs else (args[6] if len(args) > 6 else None)
        session_id = kwargs.get("session_id") if "session_id" in kwargs else (args[7] if len(args) > 7 else None)

        import queue as _q

        sse_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        origin = request.headers.get("Origin", "")
        cors = self._cors_headers_for_origin(origin) if origin else None
        if cors:
            sse_headers.update(cors)
        if session_id:
            sse_headers["X-Hermes-Session-Id"] = session_id
        response = web.StreamResponse(status=200, headers=sse_headers)
        await response.prepare(request)

        try:
            last_activity = time.monotonic()
            role_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            await response.write(f"data: {json.dumps(role_chunk)}\n\n".encode())
            last_activity = time.monotonic()

            async def _emit(item: Any) -> float:
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "__tool_progress__":
                    event_data = json.dumps(item[1])
                    await response.write(
                        f"event: hermes.tool.progress\ndata: {event_data}\n\n".encode()
                    )
                    return time.monotonic()
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "__approval_required__":
                    event_data = json.dumps(item[1])
                    await response.write(
                        f"event: hermes.approval.required\ndata: {event_data}\n\n".encode()
                    )
                    return time.monotonic()

                content_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": item}, "finish_reason": None}],
                }
                await response.write(f"data: {json.dumps(content_chunk)}\n\n".encode())
                return time.monotonic()

            loop = asyncio.get_event_loop()
            while True:
                try:
                    delta = await loop.run_in_executor(None, lambda: stream_q.get(timeout=0.5))
                except _q.Empty:
                    if agent_task.done():
                        while True:
                            try:
                                delta = stream_q.get_nowait()
                                if delta is None:
                                    break
                                last_activity = await _emit(delta)
                            except _q.Empty:
                                break
                        break
                    if time.monotonic() - last_activity >= api_server_mod.CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS:
                        await response.write(b": keepalive\n\n")
                        last_activity = time.monotonic()
                    continue

                if delta is None:
                    break

                last_activity = await _emit(delta)

            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            try:
                _result, agent_usage = await agent_task
                usage = agent_usage or usage
            except Exception:
                pass

            finish_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }
            await response.write(f"data: {json.dumps(finish_chunk)}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            agent = agent_ref[0] if agent_ref else None
            if agent is not None:
                with contextlib.suppress(Exception):
                    agent.interrupt("SSE client disconnected")
            if not agent_task.done():
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await agent_task
        return response

    APIServerAdapter.__init__ = patched_init
    APIServerAdapter._run_agent = patched_run_agent
    APIServerAdapter._write_sse_chat_completion = patched_write_sse
    APIServerAdapter.connect = patched_connect
    APIServerAdapter._handle_resolve_approval = _resolve_approval

    _PATCH_APPLIED = True

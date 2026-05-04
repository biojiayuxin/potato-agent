from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from interface.display_store import (
    append_session_event,
    get_display_messages,
    get_live_session_state,
    save_display_messages,
    save_live_session_state,
)
from interface.tui_gateway_bridge import TuiGatewayBridge, TuiGatewayBridgeError


ATTACHMENT_BLOCK_START = "<potato-files>"
ATTACHMENT_BLOCK_END = "</potato-files>"
ATTACHMENT_HINT_LINE = "Use the attachment local paths above if you need to inspect the files."
STREAM_FLUSH_INTERVAL_SECONDS = 0.5
ACTIVE_LIVE_STATUSES = {"queued", "starting", "running", "awaiting_approval"}
FINAL_LIVE_STATUSES = {"completed", "failed", "interrupted"}


def now_seconds() -> int:
    return int(time.time())


def _normalize_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    normalized = {
        "id": str(tool_call.get("id") or ""),
        "index": int(tool_call.get("index") or 0),
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": str(function.get("arguments") or ""),
        },
    }
    if tool_call.get("type"):
        normalized["type"] = str(tool_call.get("type") or "")
    return normalized


def _normalize_display_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(message.get("id") or uuid.uuid4().hex),
        "role": str(message.get("role") or "assistant"),
        "content": str(message.get("content") or ""),
        "reasoningContent": str(message.get("reasoningContent") or ""),
        "toolCalls": [
            _normalize_tool_call(item)
            for item in (message.get("toolCalls") if isinstance(message.get("toolCalls"), list) else [])
            if isinstance(item, dict)
        ],
        "progressLines": [
            str(item)
            for item in (message.get("progressLines") if isinstance(message.get("progressLines"), list) else [])
            if str(item).strip()
        ],
        "files": message.get("files") if isinstance(message.get("files"), list) else [],
        "timestamp": int(message.get("timestamp") or 0),
        "done": bool(message.get("done", True)),
        "source": str(message.get("source") or "display_store"),
    }


def _normalize_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        _normalize_display_message(message)
        for message in (messages or [])
        if isinstance(message, dict)
    ]


def _serialize_attachments_for_hermes(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for attachment in attachments:
        path = str(attachment.get("localPath") or attachment.get("path") or "").strip()
        if not path:
            continue
        serialized.append(
            {
                "name": str(attachment.get("name") or "attachment"),
                "path": path,
                "content_type": str(attachment.get("content_type") or ""),
                "size": int(attachment.get("size") or 0),
            }
        )
    return serialized


def build_hermes_user_content(text: str, attachments: list[dict[str, Any]]) -> str:
    normalized_text = str(text or "").strip()
    files = _serialize_attachments_for_hermes(attachments)
    if not files:
        return normalized_text
    block = json.dumps(files, ensure_ascii=False, indent=2)
    return (
        f"{ATTACHMENT_BLOCK_START}\n{block}\n{ATTACHMENT_BLOCK_END}\n\n"
        f"{ATTACHMENT_HINT_LINE}\n\n{normalized_text}"
    ).strip()


@dataclass(frozen=True)
class SessionRunContext:
    user_id: str
    session_id: str
    run_id: str
    assistant_message_id: str


class SessionRunManager:
    def __init__(self) -> None:
        self._bridge_listener_entries: dict[str, tuple[TuiGatewayBridge, str]] = {}
        self._run_contexts_by_session_id: dict[tuple[str, str], SessionRunContext] = {}
        self._run_contexts_by_live_session_id: dict[tuple[str, str], SessionRunContext] = {}
        self._flush_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._flush_events: dict[tuple[str, str], asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def attach_bridge(self, bridge: TuiGatewayBridge) -> None:
        async with self._lock:
            existing_entry = self._bridge_listener_entries.get(bridge.user_id)
            if existing_entry is not None and existing_entry[0] is bridge:
                return

            async def _listener(event: dict[str, Any]) -> None:
                await self.handle_bridge_event(bridge, event)

            if existing_entry is not None:
                existing_bridge, existing_listener_id = existing_entry
                existing_bridge.remove_event_listener(existing_listener_id)
            listener_id = bridge.add_event_listener(_listener)
            self._bridge_listener_entries[bridge.user_id] = (bridge, listener_id)

    async def detach_bridge(self, bridge: TuiGatewayBridge) -> None:
        async with self._lock:
            entry = self._bridge_listener_entries.pop(bridge.user_id, None)
        if entry is not None and entry[0] is bridge:
            bridge.remove_event_listener(entry[1])

    async def ensure_session_bound(
        self,
        *,
        bridge: TuiGatewayBridge,
        user_id: str,
        session_id: str,
        live_session_id: str,
        run_id: str = "",
    ) -> None:
        await self.attach_bridge(bridge)
        bridge.remember_live_session(
            live_session_id,
            persistent_session_id=session_id,
            run_id=run_id,
        )

    async def submit_turn(
        self,
        *,
        bridge: TuiGatewayBridge,
        user_id: str,
        session_id: str,
        live_session_id: str,
        prompt: str,
        attachments: list[dict[str, Any]],
        existing_messages: list[dict[str, Any]] | None = None,
        draft_title: str = "",
    ) -> dict[str, Any]:
        await self.attach_bridge(bridge)

        existing_live_state = get_live_session_state(user_id, session_id)
        if existing_live_state and str(existing_live_state.get("status") or "") in ACTIVE_LIVE_STATUSES:
            raise TuiGatewayBridgeError("session busy")

        run_id = uuid.uuid4().hex
        submitted_at = now_seconds()
        user_message_id = uuid.uuid4().hex
        assistant_message_id = uuid.uuid4().hex

        user_message = _normalize_display_message(
            {
                "id": user_message_id,
                "role": "user",
                "content": str(prompt or "").strip(),
                "reasoningContent": "",
                "toolCalls": [],
                "progressLines": [],
                "files": attachments,
                "timestamp": submitted_at,
                "done": True,
                "source": "display_store",
            }
        )
        assistant_message = _normalize_display_message(
            {
                "id": assistant_message_id,
                "role": "assistant",
                "content": "",
                "reasoningContent": "",
                "toolCalls": [],
                "progressLines": [],
                "files": [],
                "timestamp": submitted_at,
                "done": False,
                "source": "display_store",
            }
        )

        base_messages = (
            _normalize_messages(existing_messages)
            if isinstance(existing_messages, list)
            else _normalize_messages(get_display_messages(user_id, session_id))
        )
        next_messages = [*base_messages, user_message, assistant_message]
        save_display_messages(
            user_id,
            session_id,
            next_messages,
            draft_title=draft_title or None,
        )
        save_live_session_state(
            user_id,
            session_id,
            run_id=run_id,
            live_session_id=live_session_id,
            assistant_message_id=assistant_message_id,
            status="queued",
            pending_approval=None,
            last_error="",
            last_event_seq=0,
            created_at=submitted_at,
            started_at=0,
            finished_at=0,
        )
        append_session_event(
            user_id,
            session_id,
            run_id=run_id,
            seq=0,
            event_type="turn.submitted",
            payload={
                "live_session_id": live_session_id,
                "assistant_message_id": assistant_message_id,
                "prompt": prompt,
                "attachments": attachments,
            },
        )

        context = SessionRunContext(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            assistant_message_id=assistant_message_id,
        )
        async with self._lock:
            self._run_contexts_by_session_id[(user_id, session_id)] = context
            self._run_contexts_by_live_session_id[(user_id, live_session_id)] = context
            self._ensure_flush_task_locked(context)

        await self.ensure_session_bound(
            bridge=bridge,
            user_id=user_id,
            session_id=session_id,
            live_session_id=live_session_id,
            run_id=run_id,
        )

        submission_text = build_hermes_user_content(prompt, attachments)
        asyncio.create_task(
            self._submit_prompt_task(
                bridge=bridge,
                context=context,
                live_session_id=live_session_id,
                text=submission_text,
            )
        )
        return {
            "run_id": run_id,
            "session_id": session_id,
            "live_session_id": live_session_id,
            "assistant_message_id": assistant_message_id,
            "messages": next_messages,
            "live": get_live_session_state(user_id, session_id),
        }

    async def _submit_prompt_task(
        self,
        *,
        bridge: TuiGatewayBridge,
        context: SessionRunContext,
        live_session_id: str,
        text: str,
    ) -> None:
        save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            assistant_message_id=context.assistant_message_id,
            status="starting",
            pending_approval=None,
            last_error="",
        )
        try:
            await bridge.rpc(
                "prompt.submit",
                {
                    "session_id": live_session_id,
                    "text": text,
                },
            )
        except Exception as exc:
            await self._mark_failed(
                context=context,
                live_session_id=live_session_id,
                error_message=str(exc),
            )

    async def interrupt_run(
        self,
        *,
        bridge: TuiGatewayBridge,
        user_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        live_state = get_live_session_state(user_id, session_id)
        if live_state is None:
            raise TuiGatewayBridgeError("session not found")
        live_session_id = str(live_state.get("live_session_id") or "").strip()
        if not live_session_id:
            raise TuiGatewayBridgeError("no live session is attached to this conversation")
        result = await bridge.rpc("session.interrupt", {"session_id": live_session_id})
        append_session_event(
            user_id,
            session_id,
            run_id=str(live_state.get("run_id") or ""),
            seq=int(live_state.get("last_event_seq") or 0),
            event_type="session.interrupt",
            payload={"result": result},
        )
        return result

    async def respond_to_approval(
        self,
        *,
        bridge: TuiGatewayBridge,
        user_id: str,
        session_id: str,
        choice: str,
    ) -> dict[str, Any]:
        live_state = get_live_session_state(user_id, session_id)
        if live_state is None:
            raise TuiGatewayBridgeError("session not found")
        live_session_id = str(live_state.get("live_session_id") or "").strip()
        if not live_session_id:
            raise TuiGatewayBridgeError("approval request is no longer attached to a live session")
        result = await bridge.rpc(
            "approval.respond",
            {
                "session_id": live_session_id,
                "choice": choice,
            },
        )
        save_live_session_state(
            user_id,
            session_id,
            run_id=str(live_state.get("run_id") or ""),
            live_session_id=live_session_id,
            assistant_message_id=str(live_state.get("assistant_message_id") or ""),
            status="running",
            pending_approval=None,
            last_error=str(live_state.get("last_error") or ""),
            last_event_seq=int(live_state.get("last_event_seq") or 0),
        )
        append_session_event(
            user_id,
            session_id,
            run_id=str(live_state.get("run_id") or ""),
            seq=int(live_state.get("last_event_seq") or 0),
            event_type="approval.respond",
            payload={"choice": choice, "result": result},
        )
        return result

    async def handle_bridge_event(
        self,
        bridge: TuiGatewayBridge,
        event: dict[str, Any],
    ) -> None:
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            return

        live_session_id = str(event.get("session_id") or "").strip()
        persistent_session_id = str(event.get("persistent_session_id") or "").strip()
        run_id = str(event.get("run_id") or "").strip()
        seq = int(event.get("seq") or 0)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        if event_type == "gateway.ready":
            return

        if event_type == "gateway.exit":
            await self._handle_gateway_exit(bridge.user_id, payload)
            return

        context = await self._resolve_context(
            bridge_user_id=bridge.user_id,
            live_session_id=live_session_id,
            session_id=persistent_session_id,
            run_id=run_id,
        )
        if context is None:
            return

        append_session_event(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
        )

        if event_type == "message.start":
            save_live_session_state(
                context.user_id,
                context.session_id,
                run_id=context.run_id,
                live_session_id=live_session_id,
                assistant_message_id=context.assistant_message_id,
                status="running",
                pending_approval=None,
                last_error="",
                last_event_seq=seq,
                started_at=now_seconds(),
                finished_at=0,
            )
            await self._request_flush(context)
            return

        if event_type == "message.delta":
            await self._append_delta(context, payload, seq, live_session_id)
            return

        if event_type == "tool.progress":
            await self._append_progress(context, payload, seq, live_session_id)
            return

        if event_type == "approval.request":
            save_live_session_state(
                context.user_id,
                context.session_id,
                run_id=context.run_id,
                live_session_id=live_session_id,
                assistant_message_id=context.assistant_message_id,
                status="awaiting_approval",
                pending_approval={
                    "command": str(payload.get("command") or ""),
                    "description": str(payload.get("description") or "Hermes needs approval to continue."),
                },
                last_error="",
                last_event_seq=seq,
            )
            await self._request_flush(context)
            return

        if event_type == "message.complete":
            await self._complete_message(context, payload, seq, live_session_id)
            return

        if event_type == "error":
            await self._mark_failed(
                context=context,
                live_session_id=live_session_id,
                error_message=str(payload.get("message") or "Unknown bridge error"),
                seq=seq,
            )

    async def _resolve_context(
        self,
        *,
        bridge_user_id: str,
        live_session_id: str,
        session_id: str,
        run_id: str,
    ) -> SessionRunContext | None:
        async with self._lock:
            if live_session_id:
                direct = self._run_contexts_by_live_session_id.get((bridge_user_id, live_session_id))
                if direct is not None:
                    return direct
            if session_id:
                direct = self._run_contexts_by_session_id.get((bridge_user_id, session_id))
                if direct is not None:
                    return direct

        if not session_id:
            return None

        live_state = get_live_session_state(bridge_user_id, session_id)
        if live_state is None:
            return None

        assistant_message_id = str(live_state.get("assistant_message_id") or "").strip()
        resolved_run_id = str(run_id or live_state.get("run_id") or "").strip()
        if not assistant_message_id or not resolved_run_id:
            return None

        context = SessionRunContext(
            user_id=bridge_user_id,
            session_id=session_id,
            run_id=resolved_run_id,
            assistant_message_id=assistant_message_id,
        )
        async with self._lock:
            self._run_contexts_by_session_id[(bridge_user_id, session_id)] = context
            if live_session_id:
                self._run_contexts_by_live_session_id[(bridge_user_id, live_session_id)] = context
            self._ensure_flush_task_locked(context)
        return context

    async def _append_delta(
        self,
        context: SessionRunContext,
        payload: dict[str, Any],
        seq: int,
        live_session_id: str,
    ) -> None:
        text = str(payload.get("text") or "")
        messages = _normalize_messages(get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        assistant["content"] = f"{assistant['content']}{text}"
        assistant["timestamp"] = max(int(assistant.get("timestamp") or 0), now_seconds())
        assistant["done"] = False
        save_display_messages(context.user_id, context.session_id, messages)
        save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            assistant_message_id=context.assistant_message_id,
            status="running",
            pending_approval=None,
            last_error="",
            last_event_seq=seq,
        )
        await self._request_flush(context)

    async def _append_progress(
        self,
        context: SessionRunContext,
        payload: dict[str, Any],
        seq: int,
        live_session_id: str,
    ) -> None:
        preview = str(payload.get("preview") or payload.get("name") or "").strip()
        if not preview:
            return
        messages = _normalize_messages(get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        progress_lines = assistant.setdefault("progressLines", [])
        if preview and preview not in progress_lines:
            progress_lines.append(preview)
        assistant["timestamp"] = max(int(assistant.get("timestamp") or 0), now_seconds())
        assistant["done"] = False
        save_display_messages(context.user_id, context.session_id, messages)
        save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            assistant_message_id=context.assistant_message_id,
            status="running",
            pending_approval=None,
            last_error="",
            last_event_seq=seq,
        )
        await self._request_flush(context)

    async def _complete_message(
        self,
        context: SessionRunContext,
        payload: dict[str, Any],
        seq: int,
        live_session_id: str,
    ) -> None:
        text = str(payload.get("text") or "")
        reasoning = str(payload.get("reasoning") or "")
        status = str(payload.get("status") or "complete").strip().lower()
        warning = str(payload.get("warning") or "")
        messages = _normalize_messages(get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        if text and not str(assistant.get("content") or "").strip():
            assistant["content"] = text
        if reasoning:
            assistant["reasoningContent"] = reasoning
        if warning:
            combined = str(assistant.get("content") or "")
            assistant["content"] = f"{combined}\n\n[Warning] {warning}".strip()
        assistant["timestamp"] = now_seconds()
        assistant["done"] = status != "interrupted"
        save_display_messages(context.user_id, context.session_id, messages)

        live_status = "completed"
        if status == "interrupted":
            live_status = "interrupted"
        elif status == "error":
            live_status = "failed"

        save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            assistant_message_id=context.assistant_message_id,
            status=live_status,
            pending_approval=None,
            last_error="" if live_status == "completed" else warning,
            last_event_seq=seq,
            finished_at=now_seconds(),
        )
        await self._request_flush(context)
        await self._close_run_context(context, live_session_id=live_session_id)

    async def _mark_failed(
        self,
        *,
        context: SessionRunContext,
        live_session_id: str,
        error_message: str,
        seq: int = 0,
    ) -> None:
        messages = _normalize_messages(get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        current_content = str(assistant.get("content") or "").strip()
        failure_line = f"[Error] {error_message}".strip()
        if failure_line and failure_line not in current_content:
            assistant["content"] = f"{current_content}\n\n{failure_line}".strip()
        assistant["timestamp"] = now_seconds()
        assistant["done"] = True
        save_display_messages(context.user_id, context.session_id, messages)
        save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            assistant_message_id=context.assistant_message_id,
            status="failed",
            pending_approval=None,
            last_error=error_message,
            last_event_seq=seq,
            finished_at=now_seconds(),
        )
        append_session_event(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            seq=seq,
            event_type="run.failed",
            payload={"message": error_message},
        )
        await self._request_flush(context)
        await self._close_run_context(context, live_session_id=live_session_id)

    async def _handle_gateway_exit(self, bridge_user_id: str, payload: dict[str, Any]) -> None:
        live_contexts: list[tuple[str, SessionRunContext]] = []
        async with self._lock:
            for (user_id, live_session_id), context in list(self._run_contexts_by_live_session_id.items()):
                if user_id != bridge_user_id:
                    continue
                live_contexts.append((live_session_id, context))

        for live_session_id, context in live_contexts:
            live_state = get_live_session_state(context.user_id, context.session_id)
            status = str((live_state or {}).get("status") or "")
            if status in FINAL_LIVE_STATUSES:
                continue
            await self._mark_failed(
                context=context,
                live_session_id=live_session_id,
                error_message=str(payload.get("message") or "tui_gateway process exited"),
                seq=int((live_state or {}).get("last_event_seq") or 0),
            )

    def _get_or_create_assistant_message(
        self,
        messages: list[dict[str, Any]],
        context: SessionRunContext,
    ) -> dict[str, Any]:
        for message in messages:
            if str(message.get("id") or "") == context.assistant_message_id:
                return message
        assistant = _normalize_display_message(
            {
                "id": context.assistant_message_id,
                "role": "assistant",
                "content": "",
                "reasoningContent": "",
                "toolCalls": [],
                "progressLines": [],
                "files": [],
                "timestamp": now_seconds(),
                "done": False,
                "source": "display_store",
            }
        )
        messages.append(assistant)
        return assistant

    async def _close_run_context(
        self,
        context: SessionRunContext,
        *,
        live_session_id: str,
    ) -> None:
        async with self._lock:
            self._run_contexts_by_session_id.pop((context.user_id, context.session_id), None)
            if live_session_id:
                self._run_contexts_by_live_session_id.pop((context.user_id, live_session_id), None)

    def _ensure_flush_task_locked(self, context: SessionRunContext) -> None:
        key = (context.user_id, context.session_id)
        if key in self._flush_tasks:
            return
        event = asyncio.Event()
        self._flush_events[key] = event
        self._flush_tasks[key] = asyncio.create_task(self._flush_loop(context, event))

    async def _request_flush(self, context: SessionRunContext) -> None:
        async with self._lock:
            event = self._flush_events.get((context.user_id, context.session_id))
        if event is not None:
            event.set()

    async def _flush_loop(self, context: SessionRunContext, event: asyncio.Event) -> None:
        key = (context.user_id, context.session_id)
        try:
            while True:
                await event.wait()
                event.clear()
                await asyncio.sleep(STREAM_FLUSH_INTERVAL_SECONDS)
                live_state = get_live_session_state(context.user_id, context.session_id)
                status = str((live_state or {}).get("status") or "")
                if status in FINAL_LIVE_STATUSES:
                    return
        except asyncio.CancelledError:
            raise
        finally:
            async with self._lock:
                self._flush_events.pop(key, None)
                self._flush_tasks.pop(key, None)

    async def shutdown(self) -> None:
        async with self._lock:
            bridge_listener_entries = list(self._bridge_listener_entries.values())
            self._bridge_listener_entries.clear()
            tasks = list(self._flush_tasks.values())
            self._flush_tasks.clear()
            self._flush_events.clear()
            self._run_contexts_by_session_id.clear()
            self._run_contexts_by_live_session_id.clear()
        for bridge, listener_id in bridge_listener_entries:
            bridge.remove_event_listener(listener_id)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

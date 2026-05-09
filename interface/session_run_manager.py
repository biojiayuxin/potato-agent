from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from interface.auth_db import DEFAULT_AUTH_DB_PATH
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
TIP_RECONCILE_INTERVAL_SECONDS = 60.0
ACTIVE_LIVE_STATUSES = {"queued", "starting", "running", "awaiting_approval"}
FINAL_LIVE_STATUSES = {"completed", "failed", "interrupted"}
STALE_GATEWAY_SESSION_ERROR = (
    "live TUI session not found; the run is no longer attached to a gateway session"
)
MODEL_RESPONSE_ERROR_MESSAGE = "模型响应失败，请稍后重试。"

TipSessionResolver = Callable[[str, str], str | None | Awaitable[str | None]]


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
    def __init__(
        self,
        *,
        tip_resolver: TipSessionResolver | None = None,
        tip_reconcile_interval_seconds: float = TIP_RECONCILE_INTERVAL_SECONDS,
        db_path: Path = DEFAULT_AUTH_DB_PATH,
    ) -> None:
        self._tip_resolver = tip_resolver
        self._db_path = db_path
        self._tip_reconcile_interval_seconds = (
            float(tip_reconcile_interval_seconds)
            if float(tip_reconcile_interval_seconds or 0) > 0
            else TIP_RECONCILE_INTERVAL_SECONDS
        )
        self._bridge_listener_entries: dict[str, tuple[TuiGatewayBridge, str]] = {}
        self._run_contexts_by_session_id: dict[tuple[str, str], SessionRunContext] = {}
        self._run_contexts_by_live_session_id: dict[tuple[str, str], SessionRunContext] = {}
        self._flush_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._flush_events: dict[tuple[str, str], asyncio.Event] = {}
        self._lock = asyncio.Lock()

    def _get_live_session_state(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        return get_live_session_state(user_id, session_id, db_path=self._db_path)

    def _save_live_session_state(
        self,
        user_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return save_live_session_state(user_id, session_id, db_path=self._db_path, **kwargs)

    def _get_display_messages(
        self,
        user_id: str,
        session_id: str,
    ) -> list[dict[str, Any]] | None:
        return get_display_messages(user_id, session_id, db_path=self._db_path)

    def _save_display_messages(
        self,
        user_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        draft_title: str | None = None,
    ) -> None:
        save_display_messages(
            user_id,
            session_id,
            messages,
            draft_title=draft_title,
            db_path=self._db_path,
        )

    def _append_session_event(
        self,
        user_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        append_session_event(user_id, session_id, db_path=self._db_path, **kwargs)

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
        tip_session_id: str = "",
        prompt: str,
        attachments: list[dict[str, Any]],
        existing_messages: list[dict[str, Any]] | None = None,
        draft_title: str = "",
    ) -> dict[str, Any]:
        await self.attach_bridge(bridge)

        existing_live_state = self._get_live_session_state(user_id, session_id)
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
            else _normalize_messages(self._get_display_messages(user_id, session_id))
        )
        next_messages = [*base_messages, user_message, assistant_message]
        self._save_display_messages(
            user_id,
            session_id,
            next_messages,
            draft_title=draft_title or None,
        )
        self._save_live_session_state(
            user_id,
            session_id,
            run_id=run_id,
            live_session_id=live_session_id,
            tip_session_id=tip_session_id or session_id,
            assistant_message_id=assistant_message_id,
            status="queued",
            pending_approval=None,
            last_error="",
            last_event_seq=0,
            created_at=submitted_at,
            started_at=0,
            finished_at=0,
        )
        self._append_session_event(
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
            "live": self._get_live_session_state(user_id, session_id),
        }

    async def _submit_prompt_task(
        self,
        *,
        bridge: TuiGatewayBridge,
        context: SessionRunContext,
        live_session_id: str,
        text: str,
    ) -> None:
        self._save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            tip_session_id=str(
                (self._get_live_session_state(context.user_id, context.session_id) or {}).get("tip_session_id")
                or ""
            ),
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
        live_state = self._get_live_session_state(user_id, session_id)
        if live_state is None:
            raise TuiGatewayBridgeError("session not found")
        live_session_id = str(live_state.get("live_session_id") or "").strip()
        if not live_session_id:
            raise TuiGatewayBridgeError("no live session is attached to this conversation")
        run_id = str(live_state.get("run_id") or "")
        assistant_message_id = str(live_state.get("assistant_message_id") or "")
        seq = int(live_state.get("last_event_seq") or 0)
        try:
            result = await bridge.rpc("session.interrupt", {"session_id": live_session_id})
        except TuiGatewayBridgeError as exc:
            if "session not found" not in str(exc).lower():
                raise
            await self._mark_failed(
                context=SessionRunContext(
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    assistant_message_id=assistant_message_id,
                ),
                live_session_id=live_session_id,
                error_message=STALE_GATEWAY_SESSION_ERROR,
                seq=seq,
            )
            return {"status": "failed", "message": STALE_GATEWAY_SESSION_ERROR}
        self._append_session_event(
            user_id,
            session_id,
            run_id=run_id,
            seq=seq,
            event_type="session.interrupt",
            payload={"result": result},
        )
        await self._mark_interrupted(
            context=SessionRunContext(
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                assistant_message_id=assistant_message_id,
            ),
            live_session_id=live_session_id,
            seq=seq,
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
        live_state = self._get_live_session_state(user_id, session_id)
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
        self._save_live_session_state(
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
        self._append_session_event(
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
        event["persistent_session_id"] = context.session_id
        event["run_id"] = context.run_id

        live_state = self._get_live_session_state(context.user_id, context.session_id)
        if (
            live_state is not None
            and str(live_state.get("status") or "").strip() in FINAL_LIVE_STATUSES
            and event_type != "message.complete"
        ):
            return
        state_live_session_id = self._state_live_session_id(live_state, live_session_id)

        self._append_session_event(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
        )

        if event_type == "message.start":
            self._save_live_session_state(
                context.user_id,
                context.session_id,
                run_id=context.run_id,
                live_session_id=state_live_session_id,
                tip_session_id=str((live_state or {}).get("tip_session_id") or ""),
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
            await self._append_delta(context, payload, seq, state_live_session_id)
            return

        if event_type == "tool.progress":
            await self._append_progress(context, payload, seq, state_live_session_id)
            return

        if event_type == "approval.request":
            self._save_live_session_state(
                context.user_id,
                context.session_id,
                run_id=context.run_id,
                live_session_id=state_live_session_id,
                tip_session_id=str((live_state or {}).get("tip_session_id") or ""),
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
            live_state = await self._reconcile_tip(context)
            await self._complete_message(
                context,
                payload,
                seq,
                self._state_live_session_id(live_state, live_session_id),
            )
            return

        if event_type == "error":
            await self._mark_failed(
                context=context,
                live_session_id=state_live_session_id,
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
                    if run_id and direct.run_id != run_id:
                        return None
                    live_state = self._get_live_session_state(bridge_user_id, direct.session_id)
                    if (
                        live_state is not None
                        and str(live_state.get("status") or "").strip() in FINAL_LIVE_STATUSES
                    ):
                        return None
                    return direct
            if session_id:
                direct = self._run_contexts_by_session_id.get((bridge_user_id, session_id))
                if direct is not None:
                    if run_id and direct.run_id != run_id:
                        return None
                    live_state = self._get_live_session_state(bridge_user_id, direct.session_id)
                    if (
                        live_state is not None
                        and str(live_state.get("status") or "").strip() in FINAL_LIVE_STATUSES
                    ):
                        return None
                    if live_session_id and not self._event_matches_live_state(
                        live_state,
                        live_session_id,
                    ):
                        return None
                    return direct

        if not session_id:
            return await self._resolve_unknown_live_session_context(
                bridge_user_id=bridge_user_id,
                live_session_id=live_session_id,
            )

        live_state = self._get_live_session_state(bridge_user_id, session_id)
        if live_state is None:
            return None
        if str(live_state.get("status") or "").strip() in FINAL_LIVE_STATUSES:
            return None
        if live_session_id and not self._event_matches_live_state(
            live_state,
            live_session_id,
        ):
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

    async def _resolve_unknown_live_session_context(
        self,
        *,
        bridge_user_id: str,
        live_session_id: str,
    ) -> SessionRunContext | None:
        normalized_live_session_id = str(live_session_id or "").strip()
        if not normalized_live_session_id:
            return None

        async with self._lock:
            contexts = [
                context
                for (user_id, _), context in self._run_contexts_by_session_id.items()
                if user_id == bridge_user_id
            ]

        for context in contexts:
            live_state = await self._reconcile_tip(context)
            if live_state is None:
                continue
            tip_session_id = str(live_state.get("tip_session_id") or "").strip()
            if tip_session_id != normalized_live_session_id:
                continue
            async with self._lock:
                self._run_contexts_by_live_session_id[
                    (bridge_user_id, normalized_live_session_id)
                ] = context
            return context
        return None

    def _event_matches_live_state(
        self,
        live_state: dict[str, Any] | None,
        event_live_session_id: str,
    ) -> bool:
        normalized_event_live_session_id = str(event_live_session_id or "").strip()
        if not normalized_event_live_session_id:
            return True
        if not isinstance(live_state, dict):
            return False

        current_live_session_id = str(live_state.get("live_session_id") or "").strip()
        tip_session_id = str(live_state.get("tip_session_id") or "").strip()
        return normalized_event_live_session_id in {
            item for item in (current_live_session_id, tip_session_id) if item
        }

    def _state_live_session_id(
        self,
        live_state: dict[str, Any] | None,
        event_live_session_id: str,
    ) -> str:
        normalized_event_live_session_id = str(event_live_session_id or "").strip()
        if not isinstance(live_state, dict):
            return normalized_event_live_session_id

        current_live_session_id = str(live_state.get("live_session_id") or "").strip()
        tip_session_id = str(live_state.get("tip_session_id") or "").strip()
        if (
            normalized_event_live_session_id
            and tip_session_id
            and normalized_event_live_session_id == tip_session_id
            and current_live_session_id
            and current_live_session_id != normalized_event_live_session_id
        ):
            return current_live_session_id
        return normalized_event_live_session_id or current_live_session_id

    async def _append_delta(
        self,
        context: SessionRunContext,
        payload: dict[str, Any],
        seq: int,
        live_session_id: str,
    ) -> None:
        text = str(payload.get("text") or "")
        messages = _normalize_messages(self._get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        assistant["content"] = f"{assistant['content']}{text}"
        assistant["timestamp"] = max(int(assistant.get("timestamp") or 0), now_seconds())
        assistant["done"] = False
        self._save_display_messages(context.user_id, context.session_id, messages)
        live_state = self._get_live_session_state(context.user_id, context.session_id)
        self._save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            tip_session_id=str((live_state or {}).get("tip_session_id") or ""),
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
        messages = _normalize_messages(self._get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        progress_lines = assistant.setdefault("progressLines", [])
        if preview and preview not in progress_lines:
            progress_lines.append(preview)
        assistant["timestamp"] = max(int(assistant.get("timestamp") or 0), now_seconds())
        assistant["done"] = False
        self._save_display_messages(context.user_id, context.session_id, messages)
        live_state = self._get_live_session_state(context.user_id, context.session_id)
        self._save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            tip_session_id=str((live_state or {}).get("tip_session_id") or ""),
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
        live_state = self._get_live_session_state(context.user_id, context.session_id)
        if str((live_state or {}).get("status") or "").strip() == "interrupted":
            await self._request_flush(context)
            await self._close_run_context(context, live_session_id=live_session_id)
            return

        text = str(payload.get("text") or "")
        reasoning = str(payload.get("reasoning") or "")
        status = str(payload.get("status") or "complete").strip().lower()
        warning = str(payload.get("warning") or "")
        error_detail = (
            warning.strip()
            or str(payload.get("error") or payload.get("message") or "").strip()
            or MODEL_RESPONSE_ERROR_MESSAGE
        )
        messages = _normalize_messages(self._get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        if text and not str(assistant.get("content") or "").strip():
            assistant["content"] = text
        if reasoning:
            assistant["reasoningContent"] = reasoning
        if status == "error":
            combined = str(assistant.get("content") or "").strip()
            if MODEL_RESPONSE_ERROR_MESSAGE not in combined:
                assistant["content"] = (
                    f"{combined}\n\n{MODEL_RESPONSE_ERROR_MESSAGE}".strip()
                )
        elif warning:
            combined = str(assistant.get("content") or "")
            assistant["content"] = f"{combined}\n\n[Warning] {warning}".strip()
        assistant["timestamp"] = now_seconds()
        assistant["done"] = True
        self._save_display_messages(context.user_id, context.session_id, messages)

        live_status = "completed"
        if status == "interrupted":
            live_status = "interrupted"
        elif status == "error":
            live_status = "failed"

        self._save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            tip_session_id=str((live_state or {}).get("tip_session_id") or ""),
            assistant_message_id=context.assistant_message_id,
            status=live_status,
            pending_approval=None,
            last_error="" if live_status == "completed" else error_detail,
            last_event_seq=seq,
            finished_at=now_seconds(),
        )
        await self._request_flush(context)
        await self._close_run_context(context, live_session_id=live_session_id)

    async def _mark_interrupted(
        self,
        *,
        context: SessionRunContext,
        live_session_id: str,
        seq: int,
    ) -> None:
        messages = _normalize_messages(self._get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        assistant["timestamp"] = now_seconds()
        assistant["done"] = True
        self._save_display_messages(context.user_id, context.session_id, messages)
        live_state = self._get_live_session_state(context.user_id, context.session_id)
        self._save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            tip_session_id=str((live_state or {}).get("tip_session_id") or ""),
            assistant_message_id=context.assistant_message_id,
            status="interrupted",
            pending_approval=None,
            last_error="",
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
        messages = _normalize_messages(self._get_display_messages(context.user_id, context.session_id))
        assistant = self._get_or_create_assistant_message(messages, context)
        current_content = str(assistant.get("content") or "").strip()
        failure_line = f"[Error] {error_message}".strip()
        if failure_line and failure_line not in current_content:
            assistant["content"] = f"{current_content}\n\n{failure_line}".strip()
        assistant["timestamp"] = now_seconds()
        assistant["done"] = True
        self._save_display_messages(context.user_id, context.session_id, messages)
        live_state = self._get_live_session_state(context.user_id, context.session_id)
        self._save_live_session_state(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            live_session_id=live_session_id,
            tip_session_id=str((live_state or {}).get("tip_session_id") or ""),
            assistant_message_id=context.assistant_message_id,
            status="failed",
            pending_approval=None,
            last_error=error_message,
            last_event_seq=seq,
            finished_at=now_seconds(),
        )
        self._append_session_event(
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

        seen_contexts: set[tuple[str, str]] = set()
        for live_session_id, context in live_contexts:
            key = (context.user_id, context.session_id)
            if key in seen_contexts:
                continue
            seen_contexts.add(key)
            live_state = self._get_live_session_state(context.user_id, context.session_id)
            status = str((live_state or {}).get("status") or "")
            if status in FINAL_LIVE_STATUSES:
                continue
            await self._mark_failed(
                context=context,
                live_session_id=live_session_id,
                error_message=str(payload.get("message") or "tui_gateway process exited"),
                seq=int((live_state or {}).get("last_event_seq") or 0),
            )

    async def _resolve_tip_session_id(self, context: SessionRunContext) -> str | None:
        if self._tip_resolver is None:
            return None
        result = self._tip_resolver(context.user_id, context.session_id)
        if inspect.isawaitable(result):
            result = await result
        normalized = str(result or "").strip()
        return normalized or None

    async def reconcile_active_session_tip(
        self,
        user_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        live_state = self._get_live_session_state(user_id, session_id)
        if live_state is None:
            return None
        if str(live_state.get("status") or "").strip() in FINAL_LIVE_STATUSES:
            return live_state

        run_id = str(live_state.get("run_id") or "").strip()
        assistant_message_id = str(live_state.get("assistant_message_id") or "").strip()
        if not run_id or not assistant_message_id:
            return live_state

        context = SessionRunContext(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            assistant_message_id=assistant_message_id,
        )
        async with self._lock:
            self._run_contexts_by_session_id[(user_id, session_id)] = context
            live_session_id = str(live_state.get("live_session_id") or "").strip()
            if live_session_id:
                self._run_contexts_by_live_session_id[(user_id, live_session_id)] = context
            self._ensure_flush_task_locked(context)
        return await self._reconcile_tip(context) or live_state

    async def _reconcile_tip(self, context: SessionRunContext) -> dict[str, Any] | None:
        live_state = self._get_live_session_state(context.user_id, context.session_id)
        if live_state is None:
            return None
        if str(live_state.get("status") or "").strip() in FINAL_LIVE_STATUSES:
            return None

        try:
            tip_session_id = await self._resolve_tip_session_id(context)
        except Exception:
            return None
        if not tip_session_id:
            return None

        await self._remember_tip_session(context, tip_session_id)

        current_tip_session_id = str(
            live_state.get("tip_session_id") or context.session_id
        ).strip()
        if tip_session_id == current_tip_session_id:
            return live_state

        updated = self._save_live_session_state(
            context.user_id,
            context.session_id,
            tip_session_id=tip_session_id,
        )
        self._append_session_event(
            context.user_id,
            context.session_id,
            run_id=context.run_id,
            seq=int(updated.get("last_event_seq") or 0),
            event_type="session.tip.updated",
            payload={
                "root_session_id": context.session_id,
                "previous_tip_session_id": current_tip_session_id,
                "tip_session_id": tip_session_id,
            },
        )
        return updated

    async def _remember_tip_session(
        self,
        context: SessionRunContext,
        tip_session_id: str,
    ) -> None:
        normalized_tip_session_id = str(tip_session_id or "").strip()
        if not normalized_tip_session_id:
            return
        async with self._lock:
            self._run_contexts_by_live_session_id[
                (context.user_id, normalized_tip_session_id)
            ] = context
            bridge_entry = self._bridge_listener_entries.get(context.user_id)
        if bridge_entry is not None:
            bridge_entry[0].remember_live_session(
                normalized_tip_session_id,
                persistent_session_id=context.session_id,
                run_id=context.run_id,
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
        live_session_ids_to_forget: set[str] = set()
        normalized_live_session_id = str(live_session_id or "").strip()
        if normalized_live_session_id:
            live_session_ids_to_forget.add(normalized_live_session_id)
        bridge: TuiGatewayBridge | None = None
        async with self._lock:
            self._run_contexts_by_session_id.pop((context.user_id, context.session_id), None)
            for key, existing_context in list(self._run_contexts_by_live_session_id.items()):
                if existing_context == context:
                    if key[1]:
                        live_session_ids_to_forget.add(key[1])
                    self._run_contexts_by_live_session_id.pop(key, None)
            bridge_entry = self._bridge_listener_entries.get(context.user_id)
            if bridge_entry is not None:
                bridge = bridge_entry[0]
        if bridge is not None:
            for session_id_to_forget in live_session_ids_to_forget:
                forget_live_session = getattr(bridge, "forget_live_session", None)
                if callable(forget_live_session):
                    forget_live_session(session_id_to_forget)

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
        next_tip_reconcile_at = (
            time.monotonic() + self._tip_reconcile_interval_seconds
        )
        try:
            while True:
                timeout_seconds = max(next_tip_reconcile_at - time.monotonic(), 0.0)
                try:
                    await asyncio.wait_for(
                        event.wait(),
                        timeout=timeout_seconds,
                    )
                    event.clear()
                    await asyncio.sleep(STREAM_FLUSH_INTERVAL_SECONDS)
                except asyncio.TimeoutError:
                    await self._reconcile_tip(context)
                    next_tip_reconcile_at = (
                        time.monotonic() + self._tip_reconcile_interval_seconds
                    )
                else:
                    if time.monotonic() >= next_tip_reconcile_at:
                        await self._reconcile_tip(context)
                        next_tip_reconcile_at = (
                            time.monotonic() + self._tip_reconcile_interval_seconds
                        )
                live_state = self._get_live_session_state(context.user_id, context.session_id)
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
            live_contexts = list(self._run_contexts_by_live_session_id.items())
        for bridge, listener_id in bridge_listener_entries:
            bridge.remove_event_listener(listener_id)

        seen_contexts: set[tuple[str, str]] = set()
        for (user_id, live_session_id), context in live_contexts:
            key = (user_id, context.session_id)
            if key in seen_contexts:
                continue
            seen_contexts.add(key)
            live_state = self._get_live_session_state(context.user_id, context.session_id)
            status = str((live_state or {}).get("status") or "")
            if status in FINAL_LIVE_STATUSES:
                continue
            await self._mark_failed(
                context=context,
                live_session_id=live_session_id,
                error_message="interface shutdown before the run completed",
                seq=int((live_state or {}).get("last_event_seq") or 0),
            )

        async with self._lock:
            tasks = list(self._flush_tasks.values())
            self._flush_tasks.clear()
            self._flush_events.clear()
            self._run_contexts_by_session_id.clear()
            self._run_contexts_by_live_session_id.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

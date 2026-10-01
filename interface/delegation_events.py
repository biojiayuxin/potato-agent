"""Deliver saved subagent results through the existing managed chat-turn path."""

from __future__ import annotations

import asyncio


async def handle_event(manager, bridge, event: dict) -> bool:
    from interface.session_run_manager import (
        ACTIVE_LIVE_STATUSES, _pending_approval_queue, _serialize_pending_approval,
    )
    from interface.tui_gateway_bridge import TuiGatewayBridgeError

    kind = event.get("type")
    payload = event.get("payload") or {}
    is_approval = kind in {"approval.request", "approval.expired"} and bool(payload.get("subagent_id"))
    if kind == "gateway.exit":
        # Counts can already be zero when stopped children finish. The saved
        # approval state still needs cleanup, including orphans from older runs.
        states = await manager._list_live_session_states(bridge.user_id)
        keys = {key for key in manager._delegation_counts if key[0] == bridge.user_id}
        keys.update(
            (bridge.user_id, session_id) for session_id, state in states.items()
            if state.get("background_pending") or any(
                item.get("subagent_id") for item in _pending_approval_queue(state.get("pending_approval"))
            )
        )
        for key in keys:
            manager._delegation_counts.pop(key, None)
            lock = manager._approval_state_locks.setdefault(key, asyncio.Lock())
            async with lock:
                state = await manager._get_live_session_state(*key)
                if state is None:
                    continue
                background = [item for item in _pending_approval_queue((state or {}).get("pending_approval"))
                              if item.get("subagent_id")]
                if background:
                    await manager._clear_unavailable_approvals(*key, state)
                elif state.get("background_pending"):
                    await manager._save_live_session_state(*key, background_pending=False)
    if kind not in {"delegation.state", "delegation.ready"} and not is_approval:
        return False
    session_id = str(event.get("persistent_session_id") or payload.get("persistent_session_id") or "")
    live_id = str(event.get("session_id") or "")
    if not session_id or not live_id:
        return True
    key = (bridge.user_id, session_id)
    if kind == "delegation.state":
        count = int(payload.get("active") or 0) + int(payload.get("pending") or 0)
        if count:
            manager._delegation_counts[key] = count
        else:
            manager._delegation_counts.pop(key, None)
        state = await manager._get_live_session_state(*key)
        if state is not None:
            pending = bool(count) and state.get("status") != "interrupted"
            if pending != bool(state.get("background_pending")):
                await manager._save_live_session_state(*key, background_pending=pending)
        return True
    state = await manager._get_live_session_state(*key)
    if state is None:
        return True
    if kind == "delegation.ready":
        if state.get("status") == "interrupted":
            return True  # Stop also suppresses results already in transit.
        if state.get("status") in ACTIVE_LIVE_STATUSES:
            return True  # Gateway retains and offers the event again when idle.
        ids = payload.get("delegation_ids")
        if not isinstance(ids, list) or not ids:
            return True
        try:
            await manager.submit_turn(
                bridge=bridge, user_id=bridge.user_id, session_id=session_id, live_session_id=live_id,
                tip_session_id=str(state.get("tip_session_id") or session_id), prompt="", attachments=[],
                delegation_ids=ids,
            )
        except TuiGatewayBridgeError:
            pass  # A user turn may win admission; the durable result stays pending.
        return True

    approval_id = str(payload.get("approval_id") or "")
    if not approval_id:
        return True
    lock = manager._approval_state_locks.setdefault(key, asyncio.Lock())
    async with lock:
        state = await manager._get_live_session_state(*key)
        if state is None:
            return True
        queue = _pending_approval_queue(state.get("pending_approval"))
        prior = next((item for item in queue if item["approval_id"] == approval_id), None)
        status = str(state.get("status") or "completed")
        if kind == "approval.request":
            if status == "interrupted":
                return True  # An event queued before Stop must not reopen its dialog.
            manager._delegation_counts[key] = max(1, manager._delegation_counts.get(key, 0))
            if prior is None:
                resume = queue[0].get("resume_status", "running") if queue else status
                queue.append({"approval_id": approval_id, "command": str(payload.get("command") or ""),
                              "description": str(payload.get("description") or "Subagent needs approval."),
                              "subagent_id": str(payload["subagent_id"]), "resume_status": resume})
            status = "awaiting_approval"
        elif prior is not None:
            queue = [item for item in queue if item["approval_id"] != approval_id]
            status = "awaiting_approval" if queue else prior.get("resume_status", "running")
        else:
            return True
        await manager._save_live_session_state(
            *key, run_id=state.get("run_id", ""), live_session_id=live_id,
            tip_session_id=state.get("tip_session_id", ""), assistant_message_id=state.get("assistant_message_id", ""),
            status=status, pending_approval=_serialize_pending_approval(queue),
            last_event_seq=int(event.get("seq") or 0),
        )
        await manager._append_session_event(*key, run_id=state.get("run_id", ""), seq=int(event.get("seq") or 0),
                                            event_type=kind, payload=payload)
    return True

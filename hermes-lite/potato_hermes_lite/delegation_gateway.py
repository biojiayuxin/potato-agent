"""Bind delegation ownership to gateway sessions and Interface-managed turns."""

from __future__ import annotations

import threading
import time
import weakref
from pathlib import Path

from hermes_constants import get_hermes_home
from potato_hermes_lite.delegation import DelegationManager
from potato_hermes_lite.delegation_store import DelegationStore, completion_text

_managers: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_lock = threading.Lock()


def bind(session: dict, sid: str, emit) -> DelegationManager:
    manager = session.get("delegation_manager")
    if manager is None:
        home = Path(session.get("profile_home") or get_hermes_home())
        owner = DelegationStore(home).owner(session["session_key"])
        with _lock:
            manager = _managers.get((str(home), owner))
            if manager is None:
                manager = DelegationManager(home, owner)
                _managers[(str(home), owner)] = manager
        session["delegation_manager"] = manager
    manager.store.alias(session["session_key"], manager.owner)
    def notify(kind, payload):
        emit(kind, sid, {**payload, "persistent_session_id": session.get("interface_session_id") or manager.owner})
    manager.notify = notify if session.get("delegation_consumer") else None
    agent = session.get("agent")
    if agent is not None:
        agent._delegation_manager = manager
    return manager


def poll(session: dict, sid: str, emit) -> None:
    manager = session.get("delegation_manager")
    if manager is None or session.get("_finalized"):
        return
    now = time.monotonic()
    if now - session.get("delegation_polled_at", 0) < 2:
        return
    session["delegation_polled_at"] = now
    # Dispatch and worker completion use this same lock. Snapshot and emit
    # together so an old idle observation cannot follow a new dispatch, nor
    # a finishing child disappear between the live and pending checks.
    with manager.lock:
        manager.flush_results()
        if delivery := session.get("delegation_delivery"):
            manager.store.renew(manager.owner, delivery[0])
        active = len(manager._live())
        pending = manager.store.pending(manager.owner) if not manager.stopped else []
        payload = {"persistent_session_id": session.get("interface_session_id") or manager.owner,
                   "active": active, "pending": len(pending)}
        counts = (active, len(pending))
        if counts != (0, 0) or session.get("delegation_counts") != counts:
            emit("delegation.state", sid, payload)
        session["delegation_counts"] = counts
    if pending and not session.get("running"):
        emit("delegation.ready", sid, {**payload, "delegation_ids": pending})


def claim(session: dict, ids: list[str], token: str) -> str | None:
    manager = session["delegation_manager"]
    if manager.stopped:
        return None
    claimed = manager.store.claim(manager.owner, ids, token)
    if not claimed:
        return None
    session["delegation_delivery"] = (token, claimed)
    return completion_text(manager.store.list(manager.owner, unit_ids=claimed))


def settle(session: dict, *, accepted: bool) -> None:
    delivery = session.pop("delegation_delivery", None)
    manager = session.get("delegation_manager")
    if delivery is not None and manager is not None:
        manager.store.settle(manager.owner, delivery[0], accepted=accepted)


def account(session: dict) -> None:
    delivery = session.get("delegation_delivery")
    manager = session.get("delegation_manager")
    agent = session.get("agent")
    if delivery is None or manager is None or agent is None:
        return
    ids = [uid for uid in delivery[1] if uid not in manager.accounted]
    total = sum(float(r.get("cost_usd") or 0) for r in manager.store.list(manager.owner, unit_ids=ids))
    agent.session_estimated_cost_usd = float(getattr(agent, "session_estimated_cost_usd", 0) or 0) + total
    if total:
        agent.session_cost_source = "subagent"
        agent.session_cost_status = "estimated"
    # Preserve completion hooks on the parent thread, between model turns.
    for result in manager.store.list(manager.owner, unit_ids=ids):
        memory = getattr(agent, "_memory_manager", None)
        if memory is not None:
            try:
                memory.on_delegation(task=result["goal"], result=result.get("summary") or "",
                                     child_session_id=result.get("child_session_id", ""))
            except Exception:
                pass
        try:
            from hermes_cli.plugins import invoke_hook
            invoke_hook("subagent_stop", parent_session_id=getattr(agent, "session_id", None),
                        parent_turn_id=getattr(agent, "_current_turn_id", "") or "",
                        child_session_id=result.get("child_session_id"), child_role=result.get("_child_role"),
                        child_summary=result.get("summary"), child_status=result.get("status"),
                        duration_ms=int((result.get("duration_seconds") or 0) * 1000))
        except Exception:
            pass
    manager.accounted.update(ids)

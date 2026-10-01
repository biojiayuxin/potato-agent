"""Lite-owned background delegation, live control and durable result access.

The gateway owns result delivery; worker threads never start a parent model
turn. Ordinary parent turns can finish without cancelling these children.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from potato_hermes_lite.delegation_store import DelegationStore
from tools.thread_context import propagate_context_to_thread

logger = logging.getLogger(__name__)


class DelegationManager:
    def __init__(self, home: Path, owner: str):
        self.store = DelegationStore(home)
        self.owner = self.store.owner(owner)
        self.store.alias(owner, self.owner)
        self.store.recover(self.owner)
        self.store.prune()
        self.lock = threading.RLock()
        self.children: dict[str, object] = {}
        self.notify = None
        self.stopped = False
        self.accounted: set[str] = set()
        self.unsaved: dict[str, dict] = {}

    def _live(self) -> dict:
        with self.lock:
            for sid, child in list(self.children.items()):
                finished = getattr(child, "_delegate_worker_settled", None)
                if getattr(child, "_delegate_result_saved", False) and finished is not None and finished.is_set():
                    self.children.pop(sid, None)
            return dict(self.children)

    def flush_results(self) -> None:
        """Retry transient ledger errors without re-executing any task."""
        with self.lock:
            for sid, result in list(self.unsaved.items()):
                try:
                    self.store.finish(sid, result)
                except Exception:
                    logger.warning("Delegation ledger write failed; retaining result in memory")
                    continue
                self.unsaved.pop(sid, None)
                if sid in self.children:
                    self.children[sid]._delegate_result_saved = True

    def dispatch(self, children: list, parent, max_children: int, *, independent: bool = False) -> dict:
        from tools.delegate_tool import _run_single_child, _close_delegated_child

        with self.lock:
            if not callable(self.notify):
                return {"error": "No background result consumer is attached to this session."}
            if self.stopped:
                return {"error": "Delegation was stopped. Submit a new user turn before spawning more children."}
            if len(self._live()) + len(children) > max_children:
                return {"error": "Background child capacity is full. Continue independent work and end your turn; results will arrive automatically."}
            tasks = [{"id": child._subagent_id, "task_index": i, "goal": task["goal"], "group": task.get("group")}
                     for i, task, child in children]
            unit_ids = self.store.create(self.owner, tasks, independent=independent)
            for _, _, child in children:
                self.children[child._subagent_id] = child
                child._delegate_background = True
                child._delegate_stop_event = threading.Event()
                child._delegate_worker_settled = threading.Event()
                child._delegate_snapshot = lambda result, sid=child._subagent_id: self.store.snapshot(sid, result)
                with parent._active_children_lock:
                    if child in parent._active_children:
                        parent._active_children.remove(child)
            # Announce background work before the parent can finish its turn.
            self.notify("delegation.state", {"active": len(self.children), "pending": 0})

        def run(i, task, child):
            from tools.approval import register_gateway_notify, unregister_gateway_notify, set_current_session_key, reset_current_session_key
            key = child._subagent_id
            def emit(kind, payload):
                if self.notify:
                    self.notify(kind, {**payload, "subagent_id": key, "delegation_owner": self.owner})
            token = set_current_session_key(key)
            try:
                register_gateway_notify(key, lambda data: emit("approval.request", data), lifecycle_cb=emit)
                result = _run_single_child(i, task["goal"], child, parent)
            except Exception:
                logger.exception("Delegated child supervisor failed")
                if getattr(child, "_delegate_worker_future", None) is None:
                    _close_delegated_child(child, parent)
                    child._delegate_worker_settled.set()
                from potato_hermes_lite.delegation_runner import partial_result
                result = {**partial_result(child), "status": "error", "error": "Child supervisor failed; retained output is available."}
            finally:
                reset_current_session_key(token)
                from tools.approval import resolve_gateway_approval
                resolve_gateway_approval(key, "deny", resolve_all=True)
                unregister_gateway_notify(key)
            with self.lock:
                self.unsaved[key] = result
                self.flush_results()
            self._live()

        for i, task, child in children:
            try:
                threading.Thread(target=propagate_context_to_thread(run), args=(i, task, child),
                                 daemon=True, name="lite-delegation").start()
            except Exception as exc:
                child._delegate_worker_settled.set()
                _close_delegated_child(child, parent)
                with self.lock:
                    self.unsaved[child._subagent_id] = {"status": "error", "summary": None, "error": str(exc)}
                    self.flush_results()
        return {"status": "dispatched", "mode": "background", "delegation_ids": unit_ids,
                "subagent_ids": [t["id"] for t in tasks],
                "message": "Do independent work now, then end your turn. Results arrive between turns. Use list/steer/stop for live control, or result to read retained output; do not poll while waiting."}

    def control(self, action: str, subagent_id: str = "", message: str = "") -> dict:
        records = self.store.list(self.owner, child_id=subagent_id)
        live = self._live()
        if action == "list":
            return {"tasks": [{k: value for k, value in row.items()
                               if k in {"subagent_id", "delegation_id", "task_index", "goal", "status", "delivery"}}
                              for row in records], "running": len(live)}
        if not subagent_id or not records:
            return {"error": "No owned subagent with that ID."}
        if action == "result":
            return {"results": records}
        child = live.get(subagent_id)
        if child is None or getattr(child, "_delegate_result_saved", False):
            return {"error": "Subagent has finished. Use action='result' to read its retained output."}
        if action == "steer":
            if not message.strip():
                return {"error": "A nonempty message is required for steer."}
            accepted = child.steer(message)
            return {"accepted": bool(accepted), "subagent_id": subagent_id}
        if action == "stop":
            child._delegate_stop_event.set()
            child.interrupt()
            from tools.approval import resolve_gateway_approval
            resolve_gateway_approval(subagent_id, "deny", resolve_all=True)
            return {"status": "stopping", "subagent_id": subagent_id}
        return {"error": "Unknown delegation action."}

    def stop(self, *, suppress: bool = True) -> None:
        from tools.approval import resolve_gateway_approval
        with self.lock:
            self.stopped = True
            if suppress:
                self.store.suppress(self.owner)
            for sid, child in self._live().items():
                child._delegate_stop_event.set()
                try:
                    child.interrupt()
                except Exception:
                    logger.debug("Child interrupt failed", exc_info=True)
                resolve_gateway_approval(sid, "deny", resolve_all=True)

    def resolve_approval(self, child_id: str, approval_id: str, choice: str) -> int:
        from tools.approval import resolve_gateway_approval
        if not self.store.list(self.owner, child_id=child_id):
            return 0
        return resolve_gateway_approval(child_id, choice, approval_id=approval_id)


def model_dispatch(args: dict, parent) -> str:
    """Shared model-facing path; Python callers keep delegate_task's sync default."""
    from tools.delegate_tool import delegate_task
    return delegate_task(
        goal=args.get("goal"), context=args.get("context"), toolsets=args.get("toolsets"), tasks=args.get("tasks"),
        max_iterations=args.get("max_iterations"), role=args.get("role"), parent_agent=parent,
        acp_command=args.get("acp_command"), acp_args=args.get("acp_args"),
        background=(getattr(parent, "_delegate_depth", 0) == 0
                    and callable(getattr(getattr(parent, "_delegation_manager", None), "notify", None))),
        action=args.get("action"), subagent_id=args.get("subagent_id"), message=args.get("message"),
    )

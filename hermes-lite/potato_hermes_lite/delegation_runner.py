"""Activity-based child waiting and bounded partial results, without changing the model loop.

The liveness window, budget warning and deferred close follow upstream Hermes
f42f579. Lite additionally snapshots output on timeout and never closes a live
worker's database/client from its waiting thread.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future

from tools.thread_context import propagate_context_to_thread

POLL_SECONDS = 0.25
STOP_GRACE_SECONDS = 5.0
IDLE_STALE_SECONDS = 450.0
TOOL_STALE_SECONDS = 1200.0
MAX_TEXT = 16000
logger = logging.getLogger(__name__)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(part) for part in content)
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


def partial_result(child, result: dict | None = None) -> dict:
    result = result or {}
    messages = list(result.get("messages") or getattr(child, "_session_messages", None) or [])
    placeholder = str(result.get("final_response") or "").strip()
    summaries = [_text(m.get("content")).strip() for m in messages
                 if isinstance(m, dict) and m.get("role") == "assistant"]
    summary = next((s for s in reversed(summaries)
                    if s and s != placeholder and not s.startswith("Operation interrupted")), "")
    stream = getattr(child, "_delegate_stream_tail", "")
    if isinstance(stream, str) and stream.strip():
        summary = stream.strip()
    tail = [{"tool_call_id": m.get("tool_call_id"), "content": _text(m.get("content"))[-2000:]}
            for m in messages[-30:] if isinstance(m, dict) and m.get("role") == "tool"][-8:]
    from tools import file_state
    task_id = getattr(child, "_subagent_id", "")
    return {"summary": summary[-MAX_TEXT:] or None, "partial": True, "output_tail": tail,
            "artifacts": sorted(file_state.writes_since("", 0, []).get(task_id, []))[:100] if task_id else [],
            "child_session_id": str(getattr(child, "session_id", "") or "")}


def activity(child) -> tuple:
    try:
        state = child.get_activity_summary() or {}
        return (state.get("api_call_count", 0), state.get("current_tool"),
                state.get("last_activity_ts"), getattr(child, "_delegate_stream_activity", None))
    except Exception:
        return 0, None, None


def wait_for_child(child, goal: str, task_id: str, parent, timeout: float | None):
    """Return (conversation result, failure entry, still-running future)."""
    from tools.delegate_tool import _get_subagent_approval_callback, _set_subagent_approval_cb

    model_config = getattr(parent, "_potato_model_config", None)
    if model_config and getattr(child, "model", None) == model_config["model"]:
        from potato_hermes_lite.session_models import apply_model_config, record_runtime_model
        apply_model_config(child, model_config)
        record_runtime_model(child, getattr(child, "session_id", "") or task_id)

    future = Future()
    settled = threading.Event()
    stop = getattr(child, "_delegate_stop_event", None)
    if not isinstance(stop, threading.Event):
        stop = child._delegate_stop_event = threading.Event()
    child._delegate_worker_settled = settled
    child._delegate_worker_future = future
    snapshot_cb = getattr(child, "_delegate_snapshot", None)
    last_snapshot = 0.0

    def stream(delta):
        child._delegate_stream_tail = (getattr(child, "_delegate_stream_tail", "") + str(delta))[-MAX_TEXT:]
        child._delegate_stream_activity = time.monotonic()

    def run():
        _set_subagent_approval_cb(_get_subagent_approval_callback())
        try:
            if stop.is_set():
                value = {"interrupted": True, "completed": False, "messages": []}
            else:
                value = child.run_conversation(user_message=goal, task_id=task_id, stream_callback=stream)
            future.set_result(value)
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            _set_subagent_approval_cb(None)
            settled.set()

    worker = threading.Thread(target=propagate_context_to_thread(run), daemon=True, name="lite-delegated-child")
    started = last_progress = time.monotonic()
    fingerprint = activity(child)
    warned = False
    stop_at = None
    reason = ""
    try:
        worker.start()
    except BaseException:
        settled.set()
        child._delegate_worker_future = None
        raise
    while not settled.wait(POLL_SECONDS):
        now = time.monotonic()
        current = activity(child)
        if current != fingerprint:
            fingerprint, last_progress, warned = current, now, False
        idle = now - last_progress
        limit = min(timeout or float("inf"), TOOL_STALE_SECONDS if current[1] else IDLE_STALE_SECONDS)
        if callable(snapshot_cb) and now - last_snapshot >= 2:
            try:
                snapshot_cb(partial_result(child))
            except Exception:
                logger.exception("Could not checkpoint delegated child output")
            last_snapshot = now
        if (parent is not None and not getattr(child, "_delegate_background", False)
                and callable(getattr(parent, "_touch_activity", None)) and idle < limit):
            try:
                parent._touch_activity("delegated child working")
            except Exception:
                logger.debug("Parent activity callback failed", exc_info=True)
        if not warned and idle >= limit * 0.8:
            warned = True
            if callable(getattr(child, "steer", None)):
                try:
                    child.steer("[delegation budget warning] No recent progress. Finish this step and return the findings already collected.")
                except Exception:
                    logger.debug("Child steering callback failed", exc_info=True)
        interrupted = stop.is_set() or getattr(child, "_interrupt_requested", False) is True
        if stop_at is None and (interrupted or idle >= limit):
            reason = "interrupted" if interrupted else "timeout"
            stop_at = now
            stop.set()
            try:
                child.interrupt()
            except Exception:
                logger.debug("Child interrupt callback failed", exc_info=True)
        if stop_at is not None and now - stop_at >= STOP_GRACE_SECONDS:
            break
    if future.done():
        try:
            result = future.result()
        except BaseException as exc:
            entry = partial_result(child)
            entry.update(status="error", exit_reason="error", error=str(exc), duration_seconds=round(time.monotonic() - started, 2))
            return None, entry, None
        if reason == "timeout":
            entry = partial_result(child, result)
            entry.update(status="timeout", exit_reason="timeout", error="Subagent stopped making progress; retained partial output.",
                         duration_seconds=round(time.monotonic() - started, 2))
            return None, entry, None
        return result, None, None
    entry = partial_result(child)
    entry.update(status=reason or "timeout", exit_reason=reason or "timeout", api_calls=fingerprint[0],
                 error="Child stop requested; worker is still unwinding. Inspect retained findings before repeating work.",
                 duration_seconds=round(time.monotonic() - started, 2))
    return None, entry, future

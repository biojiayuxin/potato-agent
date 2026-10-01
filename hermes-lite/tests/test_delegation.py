from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from potato_hermes_lite.delegation import DelegationManager, model_dispatch
from potato_hermes_lite.delegation_store import DelegationStore
from potato_hermes_lite import delegation_runner as runner
from tools import delegate_tool


class Child:
    def __init__(self, sid="child-one", *, advance=False):
        self._subagent_id = sid
        self._delegate_depth = 1
        self._delegate_role = "leaf"
        self._credential_pool = None
        self.tool_progress_callback = None
        self.model = "mock"
        self.session_id = sid
        self.session_prompt_tokens = self.session_completion_tokens = 1
        self.session_estimated_cost_usd = 0.02
        self._interrupt_requested = False
        self._session_messages = [{"role": "assistant", "content": "Audited three modules; retain this finding."},
                                  {"role": "tool", "content": "three checks passed", "tool_call_id": "check"}]
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.closed = threading.Event()
        self.closed_while_running = False
        self.advance = advance
        self.steers = []
        self.ts = time.time()

    def run_conversation(self, **kwargs):
        from tools.approval import get_current_session_key
        self.approval_key = get_current_session_key()
        self.started.set()
        while not self.release.wait(0.01):
            if self.advance:
                self.ts = time.time()
        self.finished.set()
        return {"completed": True, "final_response": "Research result", "messages": self._session_messages, "api_calls": 2}

    def get_activity_summary(self):
        return {"api_call_count": 2, "current_tool": None, "last_activity_ts": self.ts}

    def steer(self, text):
        self.steers.append(text)
        return True

    def interrupt(self):
        self._interrupt_requested = True

    def close(self):
        self.closed_while_running = self.started.is_set() and not self.finished.is_set()
        self.closed.set()


def parent(child=None):
    return SimpleNamespace(_active_children=[child] if child else [], _active_children_lock=threading.Lock(),
                           session_id="parent", _delegate_depth=0, session_estimated_cost_usd=0,
                           _touch_activity=lambda _: None)


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_activity_renews_budget_and_frozen_child_preserves_output(runtime_paths, monkeypatch):
    monkeypatch.setattr(runner, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(runner, "STOP_GRACE_SECONDS", 0.04)
    monkeypatch.setattr(delegate_tool, "_get_child_timeout", lambda: 0.12)
    moving = Child(advance=True)
    timer = threading.Timer(0.4, moving.release.set)
    timer.start()
    try:
        result = delegate_tool._run_single_child(0, "audit", moving, parent(moving))
        assert result["status"] == "completed"
        assert result["duration_seconds"] > 0.12
        assert not moving._interrupt_requested
    finally:
        moving.release.set()
        timer.join()

    frozen = Child("frozen")
    try:
        result = delegate_tool._run_single_child(0, "audit", frozen, parent(frozen))
        assert result["status"] == "timeout"
        assert result["partial"]
        assert "Audited three" in result["summary"]
        assert result["output_tail"][0]["content"] == "three checks passed"
        assert not frozen.closed.is_set()
    finally:
        frozen.release.set()
    assert frozen.closed.wait(5)
    assert not frozen.closed_while_running


def test_background_dispatch_control_capacity_and_durable_delivery(runtime_paths, monkeypatch):
    manager = DelegationManager(runtime_paths.hermes_home, "parent")
    events = []
    manager.notify = lambda kind, payload: events.append((kind, payload))
    p = parent()
    p._delegation_manager = manager
    children = []
    def build(**kwargs):
        child = Child(f"child-{len(children)}")
        children.append(child)
        p._active_children.append(child)
        return child
    monkeypatch.setattr(delegate_tool, "_build_child_agent", build)
    monkeypatch.setattr(delegate_tool, "_load_config", lambda: {"max_concurrent_children": 1})
    monkeypatch.setattr(delegate_tool, "_resolve_delegation_credentials", lambda *_: dict.fromkeys(
        ("model", "provider", "base_url", "api_key", "api_mode")))
    result = json.loads(model_dispatch({"goal": "research"}, p))
    try:
        assert result["status"] == "dispatched"
        assert events[0] == ("delegation.state", {"active": 1, "pending": 0})
        child = children[0]
        assert child.started.wait(5)
        assert not child.finished.is_set()
        assert not p._active_children  # a later parent-turn interrupt cannot cancel detached work
        assert child.approval_key == child._subagent_id
        assert manager.control("steer", child._subagent_id, "focus on tests")["accepted"]
        assert child.steers == ["focus on tests"]
        refused = json.loads(model_dispatch({"goal": "extra"}, p))
        assert "capacity" in refused["error"]
        assert children[1].closed.is_set()
        assert manager.control("result", "foreign").get("error")
        child.release.set()
        wait_until(lambda: manager.store.pending("parent"))
        ids = result["delegation_ids"]
        assert manager.store.claim("foreign", ids, "wrong") == []
        assert manager.store.claim("parent", ids, "first") == ids
        assert manager.store.claim("parent", ids, "second") == []
        manager.store.settle("parent", "first", accepted=False)
        assert manager.store.claim("parent", ids, "retry") == ids
        manager.store.settle("parent", "retry", accepted=True)
        assert not manager.store.pending("parent")
        saved = manager.control("result", child._subagent_id)["results"][0]
        assert saved["summary"] == "Research result"
        assert saved["cost_usd"] == 0.02
    finally:
        for child in children:
            child.release.set()


def test_dead_owner_recovery_keeps_finished_and_partial_results(tmp_path, monkeypatch):
    from potato_hermes_lite import delegation_store
    store = DelegationStore(tmp_path)
    units = store.create("owner", [{"id": "one", "task_index": 0, "goal": "A"},
                                   {"id": "two", "task_index": 1, "goal": "B"}])
    store.finish("one", {"status": "completed", "summary": "finished work"})
    store.snapshot("two", {"summary": "partial work", "artifacts": ["artifact"]})
    monkeypatch.setattr(delegation_store, "process_alive", lambda _: False)
    reopened = DelegationStore(tmp_path)
    reopened.recover("owner")
    results = reopened.list("owner")
    assert [r["status"] for r in results] == ["completed", "unknown"]
    assert [r["summary"] for r in results] == ["finished work", "partial work"]
    assert reopened.pending("owner") == units
    assert not reopened.list("foreign")
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_stop_suppresses_wake_but_result_remains_readable(runtime_paths, monkeypatch):
    monkeypatch.setattr(runner, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(runner, "STOP_GRACE_SECONDS", 0.04)
    manager = DelegationManager(runtime_paths.hermes_home, "parent")
    manager.notify = lambda *_: None
    child = Child()
    p = parent(child)
    try:
        manager.dispatch([(0, {"goal": "audit"}, child)], p, 1)
        assert child.started.wait(5)
        manager.stop()
        wait_until(lambda: manager.store.list("parent")[0]["status"] != "running")
        assert not manager.store.pending("parent")
        result = manager.control("result", child._subagent_id)["results"][0]
        assert result["status"] == "interrupted"
        assert "Audited" in result["summary"]
    finally:
        child.release.set()
    assert child.closed.wait(5)


@pytest.mark.parametrize("configured,expected", [(0, None), (-1, None), (600, 600), (5, 30)])
def test_timeout_configuration_is_an_optional_inactivity_window(monkeypatch, configured, expected):
    monkeypatch.setattr(delegate_tool, "_load_config", lambda: {"child_timeout_seconds": configured})
    assert delegate_tool._get_child_timeout() == expected


def test_independent_groups_alias_and_claim_recovery(tmp_path, monkeypatch):
    from potato_hermes_lite import delegation_store
    store = DelegationStore(tmp_path)
    ids = store.create("owner", [
        {"id": "a", "task_index": 0, "goal": "A", "group": "pair"},
        {"id": "b", "task_index": 1, "goal": "B", "group": "pair"},
        {"id": "c", "task_index": 2, "goal": "C"},
    ], independent=True)
    store.finish("a", {"status": "completed", "summary": "A done"})
    assert store.pending("owner") == []
    store.finish("c", {"status": "completed", "summary": "C done"})
    assert store.pending("owner") == [ids[1]]
    store.finish("b", {"status": "completed", "summary": "B done"})
    assert store.pending("owner") == ids
    store.alias("compressed-session", "owner")
    assert store.owner("compressed-session") == "owner"
    store.claim("owner", ids, "interrupted-delivery")
    monkeypatch.setattr(delegation_store, "process_alive", lambda _: False)
    reopened = DelegationStore(tmp_path)
    reopened.recover("owner")
    assert reopened.pending("owner") == ids
    assert {r["status"] for r in reopened.list("owner")} == {"completed"}


def test_transient_result_save_failure_retries_without_reexecution(runtime_paths, monkeypatch):
    manager = DelegationManager(runtime_paths.hermes_home, "parent")
    manager.notify = lambda *_: None
    child = Child()
    real_finish = manager.store.finish
    monkeypatch.setattr(manager.store, "finish", lambda *_: (_ for _ in ()).throw(OSError("disk unavailable")))
    try:
        manager.dispatch([(0, {"goal": "audit"}, child)], parent(child), 1)
        assert child.started.wait(5)
        child.release.set()
        wait_until(lambda: bool(manager.unsaved))
        assert child.closed.wait(5)
        monkeypatch.setattr(manager.store, "finish", real_finish)
        manager.flush_results()
        assert manager.store.pending("parent")
        assert not manager.unsaved
        assert not manager._live()
        assert manager.control("result", child._subagent_id)["results"][0]["summary"] == "Research result"
    finally:
        child.release.set()


def test_background_requires_consumer_and_build_failure_closes_unstarted_children(runtime_paths, monkeypatch):
    manager = DelegationManager(runtime_paths.hermes_home, "parent")
    child = Child()
    assert "consumer" in manager.dispatch([(0, {"goal": "audit"}, child)], parent(child), 1)["error"]
    assert not child.started.is_set()
    p = parent(child)
    count = 0
    def build(**kwargs):
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError("build failed")
        return child
    monkeypatch.setattr(delegate_tool, "_build_child_agent", build)
    monkeypatch.setattr(delegate_tool, "_resolve_delegation_credentials", lambda *_: dict.fromkeys(
        ("model", "provider", "base_url", "api_key", "api_mode")))
    with pytest.raises(RuntimeError, match="build failed"):
        delegate_tool.delegate_task(tasks=[{"goal": "A"}, {"goal": "B"}], parent_agent=p)
    assert child.closed.is_set()
    assert not p._active_children


@pytest.mark.parametrize("outcome,status", [
    ({"failed": True, "error": "provider failed", "final_response": "partial finding"}, "failed"),
    ({"completed": False, "final_response": "budget-limited finding"}, "completed"),
    ({"interrupted": True, "final_response": "Operation interrupted"}, "interrupted"),
])
def test_failure_and_iteration_outcomes_retain_useful_evidence(runtime_paths, outcome, status):
    child = Child()
    child.run_conversation = lambda **_: {**outcome, "messages": child._session_messages}
    result = delegate_tool._run_single_child(0, "audit", child, parent(child))
    assert result["status"] == status
    assert result["partial"]
    assert result["summary"]
    if outcome.get("interrupted"):
        assert "Audited three" in result["summary"]
    elif not outcome.get("failed"):
        assert result["truncated"]
        assert result["exit_reason"] == "max_iterations"


def test_ledger_works_without_global_proc_stat(tmp_path, monkeypatch):
    import builtins
    import os
    from potato_hermes_lite import delegation_store as store_module
    monkeypatch.setattr(store_module.sys, "platform", "linux")
    real_open = builtins.open
    def restricted_open(path, *args, **kwargs):
        if os.fspath(path) == "/proc/stat":
            raise FileNotFoundError(2, "ProcSubset=pid hides system statistics", "/proc/stat")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", restricted_open)
    # The former implementation fails at process construction/create_time.
    with pytest.raises(FileNotFoundError):
        store_module.psutil.Process().create_time()
    identity = store_module.process_identity()
    assert identity.startswith(f"proc-v1:{os.getpid()}:")
    assert store_module.process_alive(identity)
    store = DelegationStore(tmp_path)
    units = store.create("owner", [{"id": "one", "task_index": 0, "goal": "test"}])
    store.recover("owner")
    assert store.list("owner")[0]["status"] == "running"
    store.finish("one", {"status": "completed", "summary": "retained finding"})
    assert store.claim("owner", units, "delivery") == units
    store.settle("owner", "delivery", accepted=True)
    assert store.list("owner")[0]["summary"] == "retained finding"
    assert not store.pending("owner")


def test_proc_identity_parser_handles_spaces_parentheses_and_non_utf8_comm(monkeypatch):
    from potato_hermes_lite import delegation_store as store_module
    # starttime is field 22: state plus fields 4..21 precede it.
    value = b"42 (a ) strange\xff name) S " + b"0 " * 18 + b"123456 0 0\n"
    monkeypatch.setattr(store_module.Path, "read_bytes", lambda _: value)
    assert store_module._linux_process_stat(42) == ("S", 123456)


@pytest.mark.parametrize("state,ticks,expected", [("S", 12, True), ("S", 13, False), ("Z", 12, False), ("X", 12, False)])
def test_proc_identity_distinguishes_pid_reuse_and_exited_workers(monkeypatch, state, ticks, expected):
    from potato_hermes_lite import delegation_store as store_module
    monkeypatch.setattr(store_module, "_linux_process_stat", lambda _: (state, ticks))
    assert store_module.process_alive("proc-v1:42:12") is expected


@pytest.mark.parametrize("error,expected", [(FileNotFoundError(), False), (ProcessLookupError(), False), (PermissionError(), True)])
def test_proc_identity_missing_or_inaccessible_worker(monkeypatch, error, expected):
    from potato_hermes_lite import delegation_store as store_module
    def read(_):
        raise error
    monkeypatch.setattr(store_module, "_linux_process_stat", read)
    assert store_module.process_alive("proc-v1:42:12") is expected


def test_legacy_epoch_identity_does_not_break_recovery_in_restricted_procfs(monkeypatch):
    import os
    from potato_hermes_lite import delegation_store as store_module
    monkeypatch.setattr(store_module.sys, "platform", "linux")
    def no_boot_time(_):
        raise FileNotFoundError(2, "No such file", "/proc/stat")
    monkeypatch.setattr(store_module.psutil, "Process", no_boot_time)
    assert store_module.process_alive(f"{os.getpid()}:1234.5")
    assert not store_module.process_alive("2147483647:1234.5")

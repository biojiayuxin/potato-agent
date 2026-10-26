from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from potato_hermes_lite.delegation import DelegationManager
from potato_hermes_lite.delegation_store import DelegationStore
from potato_hermes_lite import delegation_gateway as gateway


def seed(home, *, count=2):
    manager = DelegationManager(home, "parent")
    ids = manager.store.create("parent", [
        {"id": f"child-{i}", "task_index": i, "goal": f"task {i}"} for i in range(count)
    ])
    return manager, ids, {"delegation_manager": manager, "running": False}


def finish(manager, index, status="completed"):
    manager.store.finish(f"child-{index}", {
        "status": status, "summary": f"result {index}", "cost_usd": 0.25,
    })


def notification(session, ids, token="delivery"):
    text = gateway.claim(session, ids, token)
    return json.loads(text.split("\n", 1)[1])["results"] if text else []


def test_reading_all_results_prevents_idle_notification_and_stale_ready(runtime_paths):
    manager, ids, session = seed(runtime_paths.hermes_home)
    for i in range(2):
        finish(manager, i)
        result = manager.control("result", f"child-{i}")["results"][0]
        assert result["read"] is True
        assert result["summary"] == f"result {i}"
    events = []
    gateway.poll(session, "live", lambda kind, *_: events.append(kind))
    assert "delegation.ready" not in events
    assert manager.store.pending("parent") == []
    assert notification(session, ids) == []
    assert manager.control("result", "child-0")["results"][0]["summary"] == "result 0"
    restored = DelegationManager(runtime_paths.hermes_home, "parent")
    assert notification({"delegation_manager": restored}, ids) == []


def test_reading_one_result_only_notifies_unread_sibling(runtime_paths):
    manager, ids, session = seed(runtime_paths.hermes_home)
    finish(manager, 0)
    assert manager.control("result", "child-0")["results"][0]["read"] is True
    assert manager.store.pending("parent") == []  # Keep the batch completion barrier.
    finish(manager, 1)
    assert manager.store.pending("parent") == ids
    results = notification(session, ids)
    assert [r["subagent_id"] for r in results] == ["child-1"]
    assert [r["read"] for r in manager.store.list("parent")] == [True, False]
    gateway.settle(session, accepted=True)
    assert all(r["read"] for r in manager.store.list("parent"))
    assert not manager.store.pending("parent")


def test_running_snapshot_and_list_do_not_consume_final_result(runtime_paths):
    manager, ids, session = seed(runtime_paths.hermes_home, count=1)
    manager.store.snapshot("child-0", {"summary": "intermediate finding"})
    result = manager.control("result", "child-0")["results"][0]
    assert result["status"] == "running" and result["read"] is False
    finish(manager, 0)
    assert manager.control("list")["tasks"][0]["read"] is False
    assert notification(session, ids)[0]["summary"] == "result 0"


@pytest.mark.parametrize("status", ["completed", "failed", "error", "timeout", "interrupted", "unknown"])
def test_all_terminal_statuses_can_be_read(runtime_paths, status):
    manager, ids, session = seed(runtime_paths.hermes_home, count=1)
    finish(manager, 0, status)
    assert manager.control("result", "child-0")["results"][0]["read"] is True
    assert notification(session, ids) == []


def test_missing_or_foreign_result_does_not_mark_any_result(runtime_paths):
    manager, _, _ = seed(runtime_paths.hermes_home, count=1)
    finish(manager, 0)
    assert manager.control("result").get("error")
    assert manager.control("result", "missing").get("error")
    assert manager.store.read_result("foreign", "child-0") == []
    assert manager.store.list("parent")[0]["read"] is False


def test_read_flags_survive_recovery_and_compressed_session_alias(runtime_paths, monkeypatch):
    manager, ids, _ = seed(runtime_paths.hermes_home)
    finish(manager, 0)
    manager.control("result", "child-0")
    manager.store.snapshot("child-1", {"summary": "retained partial"})
    manager.store.alias("compressed", "parent")
    monkeypatch.setattr("potato_hermes_lite.delegation_store.process_alive", lambda _: False)
    restored = DelegationManager(runtime_paths.hermes_home, "compressed")
    results = notification({"delegation_manager": restored}, ids)
    assert [r["subagent_id"] for r in results] == ["child-1"]
    assert results[0]["status"] == "unknown"
    assert restored.store.list("parent", child_id="child-0")[0]["read"] is True


def test_failed_delivery_retries_only_unread_results_and_stop_still_suppresses(runtime_paths):
    manager, ids, session = seed(runtime_paths.hermes_home)
    for i in range(2):
        finish(manager, i)
    assert len(notification(session, ids)) == 2
    manager.control("result", "child-0")
    gateway.settle(session, accepted=False)
    assert [r["subagent_id"] for r in notification(session, ids, "retry")] == ["child-1"]
    manager.stop()
    gateway.settle(session, accepted=True)
    assert manager.store.list("parent", child_id="child-1")[0]["read"] is False
    assert notification(session, ids, "after-stop") == []
    assert manager.control("result", "child-1")["results"][0]["read"] is True
    assert manager.store.list("parent")[0]["delivery"] == "suppressed"


def test_completion_racing_with_read_never_consumes_a_later_snapshot(runtime_paths):
    manager, _, _ = seed(runtime_paths.hermes_home, count=16)
    writer = DelegationStore(runtime_paths.hermes_home)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i in range(16):
            start = threading.Barrier(2)
            def read():
                start.wait(timeout=5)
                return manager.control("result", f"child-{i}")["results"][0]
            def complete():
                start.wait(timeout=5)
                writer.finish(f"child-{i}", {"status": "completed", "summary": "final"})
            reading = pool.submit(read)
            writing = pool.submit(complete)
            result = reading.result(timeout=5)
            writing.result(timeout=5)
            saved = manager.store.list("parent", child_id=f"child-{i}")[0]
            assert saved["status"] == "completed"
            assert saved["read"] == (result["status"] == "completed")


def test_settle_only_marks_selected_results_and_rejects_old_claim(runtime_paths):
    manager, ids, session = seed(runtime_paths.hermes_home)
    for i in range(2):
        finish(manager, i)
    manager.store.claim("parent", ids, "old")
    with manager.store.connect() as db:
        db.execute("UPDATE units SET claimed_at=0")
    assert manager.store.claim("parent", ids, "current") == ids
    manager.store.settle("parent", "old", accepted=True)
    manager.store.settle("foreign", "current", accepted=True)
    assert not any(r["read"] for r in manager.store.list("parent"))
    manager.store.settle("parent", "current", accepted=True, child_ids=["child-0"])
    assert [r["read"] for r in manager.store.list("parent")] == [True, False]
    assert [r["subagent_id"] for r in notification(session, ids)] == ["child-1"]


def test_recovered_claim_keeps_active_read_and_only_retries_unread_sibling(runtime_paths, monkeypatch):
    manager, ids, session = seed(runtime_paths.hermes_home)
    for i in range(2):
        finish(manager, i)
    notification(session, ids)
    manager.control("result", "child-0")
    monkeypatch.setattr("potato_hermes_lite.delegation_store.process_alive", lambda _: False)
    restored = DelegationManager(runtime_paths.hermes_home, "parent")
    assert [r["subagent_id"] for r in notification({"delegation_manager": restored}, ids)] == ["child-1"]


def test_read_failure_rolls_back_flag_and_notification_format_failure_releases_claim(runtime_paths, monkeypatch):
    manager, ids, session = seed(runtime_paths.hermes_home, count=1)
    finish(manager, 0)
    with manager.store.connect() as db:
        db.execute("""CREATE TRIGGER fail_read BEFORE UPDATE OF read_at ON children
                      BEGIN SELECT RAISE(ABORT, 'write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="write failure"):
        manager.control("result", "child-0")
    assert manager.store.list("parent")[0]["read"] is False
    with manager.store.connect() as db:
        db.execute("DROP TRIGGER fail_read")
    def fail_format(_):
        raise ValueError("format failure")
    monkeypatch.setattr(gateway, "completion_text", fail_format)
    with pytest.raises(ValueError, match="format failure"):
        gateway.claim(session, ids, "failed-format")
    assert "delegation_delivery" not in session
    assert manager.store.pending("parent") == ids
    assert manager.store.list("parent")[0]["read"] is False


def test_automatic_delivery_does_not_drop_results_beyond_list_limit(runtime_paths):
    manager, ids, session = seed(runtime_paths.hermes_home, count=201)
    for i in range(201):
        finish(manager, i)
    assert len(manager.store.list("parent")) == 200
    assert len(notification(session, ids)) == 201
    gateway.settle(session, accepted=True)
    assert all(r["read"] for r in manager.store.list("parent", limit=None))


def test_read_and_notification_account_for_each_child_once(runtime_paths, monkeypatch):
    manager, ids, session = seed(runtime_paths.hermes_home)
    hooks, memories = [], []
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kw: hooks.append((name, kw)))
    session["agent"] = SimpleNamespace(session_estimated_cost_usd=1.0,
                                      _memory_manager=SimpleNamespace(on_delegation=lambda **kw: memories.append(kw)))
    finish(manager, 0)
    manager.control("result", "child-0")
    gateway.account(session)
    assert session["agent"].session_estimated_cost_usd == 1.25
    finish(manager, 1)
    notification(session, ids)
    gateway.account(session)
    gateway.settle(session, accepted=True)
    manager.control("result", "child-0")
    gateway.account(session)
    assert session["agent"].session_estimated_cost_usd == 1.5
    assert len(hooks) == len(memories) == 2
    session["delegation_manager"] = DelegationManager(runtime_paths.hermes_home, "parent")
    gateway.account(session)
    assert session["agent"].session_estimated_cost_usd == 1.5
    assert len(hooks) == len(memories) == 2


def test_legacy_database_migration_preserves_delivered_and_unread_results(tmp_path):
    with sqlite3.connect(tmp_path / "potato-delegations.db") as db:
        db.executescript("""
            CREATE TABLE units(id TEXT PRIMARY KEY, owner TEXT NOT NULL, worker TEXT NOT NULL,
                created REAL NOT NULL, finished REAL, delivery TEXT NOT NULL, claim TEXT, claimed_at REAL);
            CREATE TABLE children(id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units(id),
                task_index INTEGER NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL);
        """)
        for i, delivery in enumerate(("delivered", "pending", "suppressed")):
            db.execute("INSERT INTO units VALUES(?, 'parent', 'old-worker', 1, 2, ?, NULL, NULL)", (delivery, delivery))
            db.execute("INSERT INTO children VALUES(?, ?, ?, 'task', 'completed', '{}')", (f"old-{i}", delivery, i))
    # Opening concurrently must apply the additive migration only once.
    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(lambda _: DelegationStore(tmp_path), range(2)))
    records = {r["delivery"]: r["read"] for r in stores[0].list("parent")}
    assert records == {"delivered": True, "pending": False, "suppressed": False}
    assert stores[1].pending("parent") == ["pending"]

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_state import SessionDB as HermesSessionDB
from potato_hermes_lite.session_visibility import (
    SessionDB,
    is_internal_session,
    list_visible_sessions,
)


@pytest.mark.parametrize("operation", ["delete", "bulk_delete", "prune"])
def test_subagent_identity_survives_parent_cleanup_and_reopen(tmp_path, operation):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    db.create_session("parent", "tui")
    db.create_session("child", "tui", parent_session_id="parent")
    db.append_message("child", "user", "internal task")
    db.end_session("child", "compression")
    db.create_session("child-tip", "tui", parent_session_id="child")
    db.append_message("child-tip", "assistant", "internal result")
    db.end_session("parent", "done")
    assert [row["id"] for row in db.list_sessions_rich()] == ["parent"]
    if operation == "delete":
        db.delete_session("parent")
    elif operation == "bulk_delete":
        db.delete_sessions(["parent"])
    else:
        db._conn.execute(
            "UPDATE sessions SET started_at = 1, ended_at = 2 WHERE id = 'parent'"
        )
        db.prune_sessions(older_than_days=1)
    assert db.get_session("child")["parent_session_id"] is None
    db.close()

    reader = SessionDB(path, read_only=True)
    try:
        assert reader.get_internal_session_ids() == {"child", "child-tip"}
        assert reader.list_sessions_rich() == []
        assert reader.list_sessions_rich(include_children=True) == []
        assert reader.is_internal_session("child-tip")
        # Internal storage and the agent's model context are preserved.
        assert reader.get_messages("child-tip")[0]["content"] == "internal result"
    finally:
        reader.close()


def test_compression_branches_and_forks_remain_public(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("root", "tui")
        db.create_session("delegate", "tui", parent_session_id="root")
        db.end_session("root", "compression")
        db.create_session("tip", "tui", parent_session_id="root")
        db.create_session(
            "branch",
            "tui",
            parent_session_id="tip",
            model_config={"_branched_from": "tip"},
        )
        db.create_session("fork", "tui", model_config={"_potato_fork": {"s": "root"}})
        db.end_session("tip", "branched")
        db.create_session("legacy-branch", "tui", parent_session_id="tip")
        assert db.get_internal_session_ids() == {"delegate"}
        assert {row["id"] for row in db.list_sessions_rich()} == {
            "tip",
            "branch",
            "fork",
            "legacy-branch",
        }
        db.reopen_session("root")
        db.reopen_session("tip")
        assert not db.is_internal_session("tip")
        assert not db.is_internal_session("legacy-branch")
        assert db.get_internal_session_ids() == {"delegate"}
        db.delete_session("root")
        assert not db.is_internal_session("tip")
        assert db.is_internal_session("delegate")
    finally:
        db.close()


def test_legacy_backfill_and_writes_from_inherited_connections(tmp_path):
    path = tmp_path / "state.db"
    old = HermesSessionDB(path)
    old.create_session("root", "tui")
    old.create_session("child", "tui", parent_session_id="root")
    old.end_session("child", "compression")
    old.create_session("tip", "tui", parent_session_id="child")
    # Read-only access recognizes legacy ancestry without writing a migration.
    reader = SessionDB(path, read_only=True)
    assert reader.get_internal_session_ids() == {"child", "tip"}
    assert reader.is_internal_session("tip")
    assert (
        reader._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'potato_session_visibility'"
        ).fetchone()
        is None
    )
    reader.close()

    upgraded = SessionDB(path)
    upgraded.close()
    # A pre-existing upstream connection is covered by persistent DB triggers.
    old.create_session("new-child", "tui", parent_session_id="root")
    old.delete_sessions(["root", "child"])
    old.close()
    reader = SessionDB(path, read_only=True)
    try:
        assert reader.get_internal_session_ids() == {"child", "tip", "new-child"}
        assert reader.list_sessions_rich() == []
        assert reader.is_internal_session("child")  # deleted ID/cache tombstone
    finally:
        reader.close()


def test_visible_pagination_skips_orphans_before_limit_and_offset(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("parent", "tui")
        for i in range(80):
            db.create_session(f"child-{i}", "tui", parent_session_id="parent")
        db.create_session("visible-1", "tui")
        db.create_session("visible-2", "tui")
        db._conn.execute("UPDATE sessions SET started_at = 1 WHERE id = 'visible-1'")
        db._conn.execute("UPDATE sessions SET started_at = 2 WHERE id = 'visible-2'")
        db.delete_session("parent")
        assert [row["id"] for row in db.list_sessions_rich(source="tui", limit=1)] == [
            "visible-2"
        ]
        assert [row["id"] for row in db.list_sessions_rich("tui", None, 1, 1)] == [
            "visible-1"
        ]
        assert db.list_sessions_rich(source="tui", limit=1, offset=2) == []
    finally:
        db.close()


def test_reviewed_orphan_markers_propagate_and_are_atomic(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("orphan", "tui")
        db.end_session("orphan", "compression")
        db.create_session("tip", "tui", parent_session_id="orphan")
        db.create_session("normal", "tui")
        assert not db.is_internal_session("orphan")
        with pytest.raises(ValueError, match="Session not found"):
            db.mark_internal_sessions(["orphan", "missing"])
        assert not db.is_internal_session("orphan")
        db.mark_internal_sessions(["orphan"])
        db.delete_session("orphan")
        assert db.is_internal_session("tip")
        assert [row["id"] for row in db.list_sessions_rich()] == ["normal"]
    finally:
        db.close()


def test_review_cli_defaults_to_read_only(tmp_path):
    path = tmp_path / "state.db"
    db = HermesSessionDB(path)
    db.create_session("reviewed-orphan", "tui")
    db.close()
    command = [
        sys.executable,
        "-m",
        "potato_hermes_lite.session_visibility",
        "--db",
        str(path),
        "--mark-internal",
        "reviewed-orphan",
    ]
    result = subprocess.run(
        command,
        cwd=str(Path(__file__).parents[1]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["applied"] is False
    db = SessionDB(path, read_only=True)
    assert not is_internal_session(db, "reviewed-orphan")
    assert (
        db._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'potato_session_visibility'"
        ).fetchone()
        is None
    )
    db.close()
    subprocess.run(
        command + ["--apply"],
        cwd=str(Path(__file__).parents[1]),
        capture_output=True,
        text=True,
        check=True,
    )
    db = SessionDB(path, read_only=True)
    assert db.is_internal_session("reviewed-orphan")
    db.close()


def test_child_orphaned_during_list_read_is_filtered(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session("parent", "tui")
    first = True

    def racing_list(**kwargs):
        nonlocal first
        if first:
            first = False
            db.create_session("child", "tui", parent_session_id="parent")
            db.delete_session("parent")
        return HermesSessionDB.list_sessions_rich(db, **kwargs)

    try:
        assert list_visible_sessions(db, racing_list, source="tui") == []
    finally:
        db.close()


def test_gateway_cannot_resume_internal_session(tmp_path, monkeypatch, runtime_paths):
    from tests.test_tui_contract import _import_server, _request

    server = _import_server()
    db = SessionDB(tmp_path / "state.db")
    db.create_session("parent", "tui")
    db.create_session("child", "tui", parent_session_id="parent")
    db.delete_session("parent")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_profile_home", lambda profile: None)

    def unexpected_agent(*args, **kwargs):
        pytest.fail("Internal history must not be resumed")

    monkeypatch.setattr(server, "_make_agent", unexpected_agent)
    try:
        response = _request(server, "session.resume", {"session_id": "child"})
        assert response["error"]["code"] == 4007
    finally:
        db.close()

from __future__ import annotations

import sys
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = REPO_ROOT / "hermes-agent"
for path in (REPO_ROOT, HERMES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hermes_state import SessionDB

from interface.session_db_rpc import execute


class _FakeSessionDB:
    def __init__(self) -> None:
        self.list_kwargs = None

    def resolve_session_id(self, session_id: str):
        return session_id

    def get_session(self, session_id: str):
        return {
            "id": session_id,
            "source": "tui",
            "parent_session_id": None,
            "end_reason": None,
        }

    def get_compression_tip(self, session_id: str):
        return session_id

    def list_sessions_rich(self, **kwargs):
        self.list_kwargs = kwargs
        return [{"id": "session-1", "source": "tui"}]

    def get_messages(self, session_id: str):
        return []


def test_composite_context_preserves_compression_root_and_tip(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    writable = SessionDB(db_path=db_path)
    try:
        writable.create_session("parent-session", "tui")
        writable.end_session("parent-session", "compression")
        writable.create_session(
            "child-session",
            "tui",
            parent_session_id="parent-session",
        )
    finally:
        writable.close()

    read_only = SessionDB(db_path=db_path, read_only=True)
    try:
        result = execute(
            read_only,
            "get_logical_session_context",
            {"session_id": "child-session", "include_messages": True},
        )
    finally:
        read_only.close()

    assert result["logical_session_id"] == "parent-session"
    assert result["logical_session"]["id"] == "parent-session"
    assert result["tip_session_id"] == "child-session"
    assert result["projected_session"]["_lineage_root_id"] == "parent-session"
    assert result["messages"] == []


def test_composite_context_limits_projection_to_matching_tui_sessions() -> None:
    db = _FakeSessionDB()

    result = execute(
        db,
        "get_logical_session_context",
        {"session_id": "session-1", "include_messages": False},
    )

    assert result["logical_session_id"] == "session-1"
    assert db.list_kwargs == {
        "source": "tui",
        "limit": 20,
        "offset": 0,
        "order_by_last_active": True,
        "include_archived": True,
        "id_query": "session-1",
    }


def test_composite_context_uses_one_read_snapshot_during_compression(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    writable = SessionDB(db_path=db_path)
    writable.create_session("parent-session", "tui")
    read_only = SessionDB(db_path=db_path, read_only=True)
    writer_started = threading.Event()
    writer_finished = threading.Event()
    original_get_session = read_only.get_session
    parent_reads = 0

    def create_compression_child() -> None:
        writer_started.wait(timeout=5)
        writable.end_session("parent-session", "compression")
        writable.create_session(
            "child-session",
            "tui",
            parent_session_id="parent-session",
        )
        writer_finished.set()

    def coordinated_get_session(session_id: str):
        nonlocal parent_reads
        result = original_get_session(session_id)
        if session_id == "parent-session":
            parent_reads += 1
            if parent_reads == 2:
                writer_started.set()
                assert writer_finished.wait(timeout=5)
        return result

    read_only.get_session = coordinated_get_session
    writer = threading.Thread(target=create_compression_child)
    writer.start()
    try:
        result = execute(
            read_only,
            "get_logical_session_context",
            {"session_id": "parent-session", "include_messages": False},
        )
    finally:
        writer.join(timeout=5)
        read_only.close()
        writable.close()

    assert not writer.is_alive()
    assert result["logical_session"]["end_reason"] is None
    assert result["tip_session_id"] == "parent-session"

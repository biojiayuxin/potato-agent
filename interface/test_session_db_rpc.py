from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = REPO_ROOT / "hermes-lite"
for path in (REPO_ROOT, HERMES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hermes_state import SessionDB

from interface import session_db_rpc
from interface.mapping import HermesTarget
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


def test_main_reads_kwargs_from_stdin_not_argv(monkeypatch, capsys, tmp_path) -> None:
    sentinel = "SESSION_DB_RPC_ARGV_SENTINEL_session_1"
    received: list[tuple[str, dict[str, object]]] = []

    class FakeSessionDB:
        def __init__(self, *, db_path, read_only):
            self.db_path = db_path
            self.read_only = read_only

        def close(self) -> None:
            return None

    argv = ["session_db_rpc.py", str(tmp_path / "state.db"), "get_session"]
    monkeypatch.setattr(session_db_rpc.sys, "argv", argv)
    monkeypatch.setattr(
        session_db_rpc.sys,
        "stdin",
        io.StringIO(json.dumps({"session_id": sentinel})),
    )
    monkeypatch.setattr(session_db_rpc, "SessionDB", FakeSessionDB)
    monkeypatch.setattr(
        session_db_rpc,
        "execute",
        lambda db, method, kwargs: received.append((method, kwargs)) or {"id": sentinel},
    )

    assert session_db_rpc.main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "result": {"id": sentinel}}
    assert received == [("get_session", {"session_id": sentinel})]
    assert all(sentinel not in argument for argument in argv)


def test_web_session_db_proxy_direct_call_keeps_kwargs_out_of_argv(
    monkeypatch, tmp_path
) -> None:
    from interface import app as interface_app

    sentinel = "SESSION_DB_WEB_ARGV_SENTINEL_session_1"
    captured: list[tuple[list[str], dict[str, object]]] = []
    target = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=tmp_path,
        hermes_home=tmp_path / ".hermes",
        workdir=tmp_path,
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )

    def fake_run_process_group(command, **kwargs):
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "result": {"id": sentinel}}),
            stderr="",
        )

    monkeypatch.setattr(interface_app.os, "geteuid", lambda: 0)
    monkeypatch.delenv("INTERFACE_FORCE_PRIVILEGED_HELPER", raising=False)
    monkeypatch.setattr(interface_app, "run_process_group", fake_run_process_group)

    result = interface_app._UserSessionDBProxy(target)._call(
        "get_session", session_id=sentinel
    )

    assert result == {"id": sentinel}
    command, call_kwargs = captured[0]
    assert all(sentinel not in argument for argument in command)
    assert json.loads(str(call_kwargs["input_text"])) == {"session_id": sentinel}


def test_web_session_db_proxy_preserves_fork_marker_conflict_from_helper(
    monkeypatch, tmp_path
) -> None:
    from interface import app as interface_app
    from interface.privileged_client import PrivilegedClientError

    target = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=tmp_path,
        hermes_home=tmp_path / ".hermes",
        workdir=tmp_path,
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )

    def raise_marker_conflict(*args, **kwargs):
        raise PrivilegedClientError("Fork target marker conflict")

    monkeypatch.setattr(interface_app.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        interface_app.privileged_client,
        "session_db_call",
        raise_marker_conflict,
    )

    with pytest.raises(ValueError, match="Fork target marker conflict"):
        interface_app._UserSessionDBProxy(target)._call(
            "fork_session",
            target_session_id="fork-target",
        )

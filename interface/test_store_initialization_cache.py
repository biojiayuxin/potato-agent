from __future__ import annotations

import sqlite3

from interface import auth_db, display_store, runtime_state


def test_auth_connection_initializes_schema_once_per_database(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "interface.db"
    calls = 0
    original_ensure = auth_db.ensure_auth_db

    def counted_ensure(path=db_path):
        nonlocal calls
        calls += 1
        return original_ensure(path)

    monkeypatch.setattr(auth_db, "ensure_auth_db", counted_ensure)

    first = auth_db.connect_auth_db(db_path)
    first.close()
    second = auth_db.connect_auth_db(db_path)
    second.close()

    assert calls == 1


def test_runtime_store_initializes_schema_once_per_database(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "interface.db"
    calls = 0
    original_connect = runtime_state.connect_auth_db

    def counted_connect(path=db_path):
        nonlocal calls
        calls += 1
        return original_connect(path)

    monkeypatch.setattr(runtime_state, "connect_auth_db", counted_connect)

    runtime_state.ensure_runtime_state_store(db_path)
    runtime_state.ensure_runtime_state_store(db_path)

    assert calls == 1


def test_display_store_initializes_schema_once_per_database(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "interface.db"
    calls = 0
    original_initialize = display_store._initialize_display_store

    def counted_initialize(path):
        nonlocal calls
        calls += 1
        return original_initialize(path)

    monkeypatch.setattr(display_store, "_initialize_display_store", counted_initialize)

    display_store.ensure_display_store(db_path)
    display_store.ensure_display_store(db_path)

    assert calls == 1


def test_display_store_adds_workspace_event_cursor_to_existing_database(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE session_live_state (
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                live_session_id TEXT NOT NULL DEFAULT '',
                tip_session_id TEXT NOT NULL DEFAULT '',
                assistant_message_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                pending_approval_json TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                last_event_seq INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                started_at INTEGER NOT NULL DEFAULT 0,
                finished_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, session_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO session_live_state (
                user_id, session_id, run_id, last_event_seq, created_at, updated_at
            ) VALUES ('user-1', 'session-1', 'run-1', 7, 1, 1)
            """
        )
        conn.commit()

    display_store.ensure_display_store(db_path)

    state = display_store.get_live_session_state(
        "user-1", "session-1", db_path=db_path
    )
    assert state is not None
    assert state["last_event_seq"] == 7
    assert state["last_workspace_event_seq"] == 0

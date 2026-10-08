"""Persist model selections for logical Interface conversations."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from interface.auth_db import DEFAULT_AUTH_DB_PATH, connect_auth_db

_INIT_LOCK = threading.Lock()
_INITIALIZED_IDENTITIES: dict[str, tuple[int, int]] = {}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_model_state (
    model_revision INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    UNIQUE (user_id, session_id)
);

CREATE TRIGGER IF NOT EXISTS delete_user_session_models
AFTER DELETE ON users
BEGIN
    DELETE FROM session_model_state WHERE user_id = OLD.id;
END;

CREATE TABLE IF NOT EXISTS session_model_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    option_id TEXT NOT NULL,
    config_revision TEXT NOT NULL,
    upstream_model TEXT NOT NULL,
    display_name TEXT NOT NULL,
    api_mode TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    context_length INTEGER,
    admitted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_model_runs_session
ON session_model_runs(user_id, session_id, admitted_at);
CREATE TRIGGER IF NOT EXISTS delete_user_session_model_runs
AFTER DELETE ON users BEGIN
    DELETE FROM session_model_runs WHERE user_id = OLD.id;
END;
"""


def _database_identity(db_path: Path) -> tuple[int, int] | None:
    try:
        stat = db_path.stat()
    except OSError:
        return None
    return int(stat.st_dev), int(stat.st_ino)


def ensure_session_model_store(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    cache_key = str(db_path.expanduser().absolute())
    identity = _database_identity(db_path)
    if identity is not None and _INITIALIZED_IDENTITIES.get(cache_key) == identity:
        return db_path
    with _INIT_LOCK:
        identity = _database_identity(db_path)
        if identity is not None and _INITIALIZED_IDENTITIES.get(cache_key) == identity:
            return db_path
        with connect_auth_db(db_path) as conn:
            conn.executescript(SCHEMA_SQL)
        identity = _database_identity(db_path)
        if identity is not None:
            _INITIALIZED_IDENTITIES[cache_key] = identity
    return db_path


def _public_state(row: sqlite3.Row | None) -> dict[str, Any]:
    return {
        "model_id": str(row["model_id"]) if row is not None else "",
        "model_revision": int(row["model_revision"]) if row is not None else 0,
    }


def _read_state(
    conn: sqlite3.Connection, user_id: str, session_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT model_id, model_revision FROM session_model_state "
        "WHERE user_id = ? AND session_id = ?",
        (user_id, session_id),
    ).fetchone()


def _replace_state(
    conn: sqlite3.Connection, user_id: str, session_id: str, model_id: str
) -> dict[str, Any]:
    # Reinsert within the caller's write transaction to obtain a database-wide
    # monotonic revision, including when restoring a previous model selection.
    conn.execute(
        "DELETE FROM session_model_state WHERE user_id = ? AND session_id = ?",
        (user_id, session_id),
    )
    cursor = conn.execute(
        "INSERT INTO session_model_state (user_id, session_id, model_id) "
        "VALUES (?, ?, ?)",
        (user_id, session_id, model_id),
    )
    return {"model_id": model_id, "model_revision": int(cursor.lastrowid)}


def get_session_model_state(
    user_id: str, session_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> dict[str, Any]:
    ensure_session_model_store(db_path)
    with connect_auth_db(db_path) as conn:
        return _public_state(_read_state(conn, user_id, session_id))


def list_session_model_states(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> dict[str, dict[str, Any]]:
    ensure_session_model_store(db_path)
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(
            "SELECT session_id, model_id, model_revision FROM session_model_state "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {str(row["session_id"]): _public_state(row) for row in rows}


def set_session_model(
    user_id: str,
    session_id: str,
    model_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any]:
    ensure_session_model_store(db_path)
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _public_state(_read_state(conn, user_id, session_id))
        if current["model_id"] == model_id:
            return current
        return _replace_state(conn, user_id, session_id, model_id)


def initialize_session_model(
    user_id: str,
    session_id: str,
    model_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any]:
    """Set an initial selection without overwriting a retried draft or fork."""
    ensure_session_model_store(db_path)
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = _read_state(conn, user_id, session_id)
        if existing is not None:
            return _public_state(existing)
        return _replace_state(conn, user_id, session_id, model_id)


def restore_session_model(
    user_id: str,
    session_id: str,
    previous_model_id: str,
    *,
    expected_revision: int,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    """Compensate a failed update only if no subsequent update has replaced it.

    An empty previous model restores the default using a new revision. Keep
    that row so delayed responses cannot resurrect the failed selection.
    Return None when the expected revision is no longer current.
    """
    ensure_session_model_store(db_path)
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _public_state(_read_state(conn, user_id, session_id))
        if current["model_revision"] != expected_revision:
            return None
        return _replace_state(conn, user_id, session_id, previous_model_id)


def delete_session_model_state(
    user_id: str, session_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> None:
    ensure_session_model_store(db_path)
    with connect_auth_db(db_path) as conn:
        conn.execute(
            "DELETE FROM session_model_state WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )
        conn.execute("DELETE FROM session_model_runs WHERE user_id = ? AND session_id = ?", (user_id, session_id))


def record_model_run(user_id: str, session_id: str, run_id: str, snapshot: dict, db_path: Path = DEFAULT_AUTH_DB_PATH) -> None:
    """Append public execution metadata; never persist route tokens or secrets."""
    if not snapshot.get("config_revision"):
        return
    ensure_session_model_store(db_path)
    values = (run_id, user_id, session_id, snapshot["id"], snapshot["config_revision"],
              snapshot["upstream_model"], snapshot["display_name"], snapshot["api_mode"],
              snapshot["reasoning_effort"], snapshot["context_length"])
    with connect_auth_db(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO session_model_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, time.time()))
        row = conn.execute("SELECT run_id, user_id, session_id, option_id, config_revision, upstream_model, display_name, api_mode, reasoning_effort, context_length FROM session_model_runs WHERE run_id = ?", (run_id,)).fetchone()
        if tuple(row) != values:
            raise ValueError("Turn model snapshot conflicts with its original admission")

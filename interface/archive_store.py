from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from interface.secure_paths import (
    DEFAULT_PRIVATE_WRITABLE_DIR_MODE,
    DEFAULT_STATE_DIR,
    ensure_private_directory,
    ensure_sqlite_sidecar_modes,
)


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_ARCHIVE_DB_PATH = Path(
    os.getenv("INTERFACE_ARCHIVE_DB") or (DEFAULT_STATE_DIR / "data" / "archive.db")
)


def _connect_archive_db(db_path: Path = DEFAULT_ARCHIVE_DB_PATH) -> sqlite3.Connection:
    ensure_private_directory(db_path.parent, mode=DEFAULT_PRIVATE_WRITABLE_DIR_MODE)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_archive_db(db_path: Path = DEFAULT_ARCHIVE_DB_PATH) -> Path:
    with _connect_archive_db(db_path) as conn:
        conn.executescript(
            """
CREATE TABLE IF NOT EXISTS archived_sessions (
    archive_id TEXT PRIMARY KEY,
    archived_at INTEGER NOT NULL,
    mapping_username TEXT NOT NULL,
    email_snapshot TEXT NOT NULL DEFAULT '',
    original_session_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    hermes_title TEXT NOT NULL DEFAULT '',
    draft_title TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL DEFAULT 0,
    ended_at REAL,
    last_active REAL NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    session_json TEXT NOT NULL DEFAULT '{}',
    messages_json TEXT NOT NULL DEFAULT '[]',
    display_messages_json TEXT NOT NULL DEFAULT '[]'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_archived_sessions_original
ON archived_sessions(mapping_username, original_session_id);

CREATE INDEX IF NOT EXISTS idx_archived_sessions_archived_at
ON archived_sessions(archived_at DESC);

CREATE TABLE IF NOT EXISTS archive_runs (
    run_id TEXT PRIMARY KEY,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    status TEXT NOT NULL DEFAULT 'running',
    archived_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT ''
);
"""
        )
        conn.commit()
    ensure_sqlite_sidecar_modes(db_path)
    return db_path


def archive_session_record(
    *,
    mapping_username: str,
    email_snapshot: str,
    session: dict[str, Any],
    messages: list[dict[str, Any]],
    display_messages: list[dict[str, Any]],
    draft_title: str,
    db_path: Path = DEFAULT_ARCHIVE_DB_PATH,
) -> bool:
    ensure_archive_db(db_path)
    archived_at = int(time.time())
    original_session_id = str(session.get("id") or "")
    with _connect_archive_db(db_path) as conn:
        existing = conn.execute(
            "select archive_id from archived_sessions where mapping_username = ? and original_session_id = ? limit 1",
            (mapping_username, original_session_id),
        ).fetchone()
        if existing is not None:
            return False

        conn.execute(
            """
insert into archived_sessions (
    archive_id, archived_at, mapping_username, email_snapshot,
    original_session_id, source, model, hermes_title, draft_title,
    started_at, ended_at, last_active, message_count, tool_call_count,
    session_json, messages_json, display_messages_json
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            (
                str(uuid.uuid4()),
                archived_at,
                mapping_username,
                email_snapshot,
                original_session_id,
                str(session.get("source") or ""),
                str(session.get("model") or ""),
                str(session.get("title") or ""),
                draft_title,
                float(session.get("started_at") or 0),
                session.get("ended_at"),
                float(session.get("last_active") or session.get("started_at") or 0),
                int(session.get("message_count") or 0),
                int(session.get("tool_call_count") or 0),
                json.dumps(session, ensure_ascii=False, separators=(",", ":")),
                json.dumps(messages, ensure_ascii=False, separators=(",", ":")),
                json.dumps(display_messages, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()
    return True


def start_archive_run(db_path: Path = DEFAULT_ARCHIVE_DB_PATH) -> str:
    ensure_archive_db(db_path)
    run_id = str(uuid.uuid4())
    started_at = int(time.time())
    with _connect_archive_db(db_path) as conn:
        conn.execute(
            "insert into archive_runs (run_id, started_at, status, archived_count, error_message) values (?, ?, 'running', 0, '')",
            (run_id, started_at),
        )
        conn.commit()
    return run_id


def finish_archive_run(
    run_id: str,
    *,
    status: str,
    archived_count: int,
    error_message: str = "",
    db_path: Path = DEFAULT_ARCHIVE_DB_PATH,
) -> None:
    ensure_archive_db(db_path)
    finished_at = int(time.time())
    with _connect_archive_db(db_path) as conn:
        conn.execute(
            "update archive_runs set finished_at = ?, status = ?, archived_count = ?, error_message = ? where run_id = ?",
            (finished_at, status, archived_count, error_message[:2000], run_id),
        )
        conn.commit()


def list_archive_runs(
    limit: int = 20, db_path: Path = DEFAULT_ARCHIVE_DB_PATH
) -> list[dict[str, Any]]:
    ensure_archive_db(db_path)
    with _connect_archive_db(db_path) as conn:
        rows = conn.execute(
            "select run_id, started_at, finished_at, status, archived_count, error_message from archive_runs order by started_at desc limit ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_archived_sessions(db_path: Path = DEFAULT_ARCHIVE_DB_PATH) -> int:
    ensure_archive_db(db_path)
    with _connect_archive_db(db_path) as conn:
        row = conn.execute("select count(*) as count from archived_sessions").fetchone()
    return int(row["count"] or 0) if row is not None else 0

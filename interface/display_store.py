from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from interface.auth_db import DEFAULT_AUTH_DB_PATH, connect_auth_db


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_display_transcripts (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    messages_json TEXT NOT NULL DEFAULT '[]',
    draft_title TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_session_display_transcripts_updated_at
ON session_display_transcripts(updated_at);

ALTER TABLE session_display_transcripts ADD COLUMN draft_title TEXT NOT NULL DEFAULT '';
"""


def ensure_display_store(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    with connect_auth_db(db_path) as conn:
        conn.executescript(
            """
CREATE TABLE IF NOT EXISTS session_display_transcripts (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    messages_json TEXT NOT NULL DEFAULT '[]',
    draft_title TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_session_display_transcripts_updated_at
ON session_display_transcripts(updated_at);
"""
        )
        columns = {
            str(row[1])
            for row in conn.execute(
                "pragma table_info(session_display_transcripts)"
            ).fetchall()
        }
        if "draft_title" not in columns:
            conn.execute(
                "ALTER TABLE session_display_transcripts ADD COLUMN draft_title TEXT NOT NULL DEFAULT ''"
            )
        conn.commit()
    return db_path


def get_display_messages(
    user_id: str, session_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> list[dict[str, Any]] | None:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select messages_json from session_display_transcripts where user_id = ? and session_id = ? limit 1",
            (user_id, session_id),
        ).fetchone()

    if row is None:
        return None

    try:
        payload = json.loads(str(row["messages_json"] or "[]"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def get_display_session_meta(
    user_id: str, session_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> dict[str, Any] | None:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select messages_json, draft_title, created_at, updated_at from session_display_transcripts where user_id = ? and session_id = ? limit 1",
            (user_id, session_id),
        ).fetchone()

    if row is None:
        return None

    try:
        payload = json.loads(str(row["messages_json"] or "[]"))
    except json.JSONDecodeError:
        payload = []

    return {
        "messages": payload if isinstance(payload, list) else [],
        "draft_title": str(row["draft_title"] or ""),
        "created_at": int(row["created_at"] or 0),
        "updated_at": int(row["updated_at"] or 0),
    }


def save_display_messages(
    user_id: str,
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    draft_title: str | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> None:
    ensure_display_store(db_path)
    now = int(time.time())
    payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))

    with connect_auth_db(db_path) as conn:
        existing = conn.execute(
            "select draft_title from session_display_transcripts where user_id = ? and session_id = ? limit 1",
            (user_id, session_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                "insert into session_display_transcripts (user_id, session_id, messages_json, draft_title, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    session_id,
                    payload,
                    str(draft_title or ""),
                    now,
                    now,
                ),
            )
        else:
            next_draft_title = (
                str(draft_title)
                if draft_title is not None
                else str(existing["draft_title"] or "")
            )
            conn.execute(
                "update session_display_transcripts set messages_json = ?, draft_title = ?, updated_at = ? where user_id = ? and session_id = ?",
                (payload, next_draft_title, now, user_id, session_id),
            )
        conn.commit()


def set_display_draft_title(
    user_id: str,
    session_id: str,
    draft_title: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> None:
    ensure_display_store(db_path)
    now = int(time.time())
    with connect_auth_db(db_path) as conn:
        existing = conn.execute(
            "select messages_json, created_at from session_display_transcripts where user_id = ? and session_id = ? limit 1",
            (user_id, session_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                "insert into session_display_transcripts (user_id, session_id, messages_json, draft_title, created_at, updated_at) values (?, ?, '[]', ?, ?, ?)",
                (user_id, session_id, draft_title, now, now),
            )
        else:
            conn.execute(
                "update session_display_transcripts set draft_title = ?, updated_at = ? where user_id = ? and session_id = ?",
                (draft_title, now, user_id, session_id),
            )
        conn.commit()


def delete_display_messages(
    user_id: str, session_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "delete from session_display_transcripts where user_id = ? and session_id = ?",
            (user_id, session_id),
        )
        conn.commit()
        return cursor.rowcount > 0

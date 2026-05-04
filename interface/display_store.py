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

CREATE TABLE IF NOT EXISTS session_live_state (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    live_session_id TEXT NOT NULL DEFAULT '',
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
);

CREATE INDEX IF NOT EXISTS idx_session_live_state_status_updated_at
ON session_live_state(status, updated_at);

CREATE TABLE IF NOT EXISTS session_event_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_event_journal_lookup
ON session_event_journal(user_id, session_id, created_at);
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

CREATE TABLE IF NOT EXISTS session_live_state (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    live_session_id TEXT NOT NULL DEFAULT '',
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
);

CREATE INDEX IF NOT EXISTS idx_session_live_state_status_updated_at
ON session_live_state(status, updated_at);

CREATE TABLE IF NOT EXISTS session_event_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_event_journal_lookup
ON session_event_journal(user_id, session_id, created_at);
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


def list_display_session_metas(
    user_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, dict[str, Any]]:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(
            """
            select session_id, messages_json, draft_title, created_at, updated_at
            from session_display_transcripts
            where user_id = ?
            """,
            (user_id,),
        ).fetchall()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        session_id = str(row["session_id"] or "").strip()
        if not session_id:
            continue
        try:
            payload = json.loads(str(row["messages_json"] or "[]"))
        except json.JSONDecodeError:
            payload = []
        result[session_id] = {
            "messages": payload if isinstance(payload, list) else [],
            "draft_title": str(row["draft_title"] or ""),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }
    return result


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
            existing_draft_title = str(existing["draft_title"] or "")
            next_draft_title = (
                existing_draft_title
                if existing_draft_title.strip()
                else str(draft_title or "")
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


_MISSING = object()


def _normalize_live_state_row(row: Any) -> dict[str, Any]:
    pending_approval_raw = str(row["pending_approval_json"] or "")
    pending_approval: dict[str, Any] | None = None
    if pending_approval_raw:
        try:
            decoded = json.loads(pending_approval_raw)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            pending_approval = decoded

    return {
        "run_id": str(row["run_id"] or ""),
        "live_session_id": str(row["live_session_id"] or ""),
        "assistant_message_id": str(row["assistant_message_id"] or ""),
        "status": str(row["status"] or ""),
        "pending_approval": pending_approval,
        "last_error": str(row["last_error"] or ""),
        "last_event_seq": int(row["last_event_seq"] or 0),
        "created_at": int(row["created_at"] or 0),
        "updated_at": int(row["updated_at"] or 0),
        "started_at": int(row["started_at"] or 0),
        "finished_at": int(row["finished_at"] or 0),
    }


def get_live_session_state(
    user_id: str,
    session_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            """
            select run_id, live_session_id, assistant_message_id, status,
                   pending_approval_json, last_error, last_event_seq,
                   created_at, updated_at, started_at, finished_at
            from session_live_state
            where user_id = ? and session_id = ?
            limit 1
            """,
            (user_id, session_id),
        ).fetchone()
    if row is None:
        return None
    return _normalize_live_state_row(row)


def list_live_session_states(
    user_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, dict[str, Any]]:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(
            """
            select session_id, run_id, live_session_id, assistant_message_id, status,
                   pending_approval_json, last_error, last_event_seq,
                   created_at, updated_at, started_at, finished_at
            from session_live_state
            where user_id = ?
            """,
            (user_id,),
        ).fetchall()
    return {
        str(row["session_id"] or ""): _normalize_live_state_row(row)
        for row in rows
        if str(row["session_id"] or "").strip()
    }


def save_live_session_state(
    user_id: str,
    session_id: str,
    *,
    run_id: str | None = None,
    live_session_id: str | None = None,
    assistant_message_id: str | None = None,
    status: str | None = None,
    pending_approval: dict[str, Any] | None | object = _MISSING,
    last_error: str | None = None,
    last_event_seq: int | None = None,
    created_at: int | None = None,
    started_at: int | None = None,
    finished_at: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any]:
    ensure_display_store(db_path)
    now = int(time.time())
    created_at_value = int(created_at or now)
    with connect_auth_db(db_path) as conn:
        existing = conn.execute(
            """
            select run_id, live_session_id, assistant_message_id, status,
                   pending_approval_json, last_error, last_event_seq,
                   created_at, updated_at, started_at, finished_at
            from session_live_state
            where user_id = ? and session_id = ?
            limit 1
            """,
            (user_id, session_id),
        ).fetchone()

        existing_payload = _normalize_live_state_row(existing) if existing is not None else None
        next_payload = {
            "run_id": str(run_id if run_id is not None else (existing_payload or {}).get("run_id") or ""),
            "live_session_id": str(
                live_session_id
                if live_session_id is not None
                else (existing_payload or {}).get("live_session_id") or ""
            ),
            "assistant_message_id": str(
                assistant_message_id
                if assistant_message_id is not None
                else (existing_payload or {}).get("assistant_message_id") or ""
            ),
            "status": str(status if status is not None else (existing_payload or {}).get("status") or ""),
            "pending_approval": (
                pending_approval
                if pending_approval is not _MISSING
                else (existing_payload or {}).get("pending_approval")
            ),
            "last_error": str(
                last_error if last_error is not None else (existing_payload or {}).get("last_error") or ""
            ),
            "last_event_seq": int(
                last_event_seq
                if last_event_seq is not None
                else (existing_payload or {}).get("last_event_seq") or 0
            ),
            "created_at": int((existing_payload or {}).get("created_at") or created_at_value),
            "updated_at": now,
            "started_at": int(
                started_at
                if started_at is not None
                else (existing_payload or {}).get("started_at") or 0
            ),
            "finished_at": int(
                finished_at
                if finished_at is not None
                else (existing_payload or {}).get("finished_at") or 0
            ),
        }

        pending_approval_json = (
            json.dumps(next_payload["pending_approval"], ensure_ascii=False, separators=(",", ":"))
            if isinstance(next_payload["pending_approval"], dict)
            else ""
        )

        if existing is None:
            conn.execute(
                """
                insert into session_live_state (
                    user_id, session_id, run_id, live_session_id, assistant_message_id,
                    status, pending_approval_json, last_error, last_event_seq,
                    created_at, updated_at, started_at, finished_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    next_payload["run_id"],
                    next_payload["live_session_id"],
                    next_payload["assistant_message_id"],
                    next_payload["status"],
                    pending_approval_json,
                    next_payload["last_error"],
                    next_payload["last_event_seq"],
                    next_payload["created_at"],
                    next_payload["updated_at"],
                    next_payload["started_at"],
                    next_payload["finished_at"],
                ),
            )
        else:
            conn.execute(
                """
                update session_live_state
                set run_id = ?, live_session_id = ?, assistant_message_id = ?,
                    status = ?, pending_approval_json = ?, last_error = ?,
                    last_event_seq = ?, updated_at = ?, started_at = ?, finished_at = ?
                where user_id = ? and session_id = ?
                """,
                (
                    next_payload["run_id"],
                    next_payload["live_session_id"],
                    next_payload["assistant_message_id"],
                    next_payload["status"],
                    pending_approval_json,
                    next_payload["last_error"],
                    next_payload["last_event_seq"],
                    next_payload["updated_at"],
                    next_payload["started_at"],
                    next_payload["finished_at"],
                    user_id,
                    session_id,
                ),
            )
        conn.commit()

    return {
        **next_payload,
        "pending_approval": next_payload["pending_approval"]
        if isinstance(next_payload["pending_approval"], dict)
        else None,
    }


def delete_live_session_state(
    user_id: str,
    session_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "delete from session_live_state where user_id = ? and session_id = ?",
            (user_id, session_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def append_session_event(
    user_id: str,
    session_id: str,
    *,
    run_id: str = "",
    seq: int = 0,
    event_type: str,
    payload: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> None:
    ensure_display_store(db_path)
    now = int(time.time())
    payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    with connect_auth_db(db_path) as conn:
        conn.execute(
            """
            insert into session_event_journal (
                user_id, session_id, run_id, seq, event_type, payload_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, session_id, run_id, int(seq or 0), event_type, payload_json, now),
        )
        conn.commit()


def delete_session_events(
    user_id: str,
    session_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "delete from session_event_journal where user_id = ? and session_id = ?",
            (user_id, session_id),
        )
        conn.commit()
        return cursor.rowcount > 0

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from interface.auth_db import DEFAULT_AUTH_DB_PATH, connect_auth_db

TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS = 5 * 60
TURN_SUBMISSION_RECEIPT_RETENTION_SECONDS = 7 * 24 * 60 * 60
_DISPLAY_STORE_INIT_LOCK = threading.Lock()
_DISPLAY_STORE_IDENTITIES: dict[str, tuple[int, int]] = {}


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
    tip_session_id TEXT NOT NULL DEFAULT '',
    assistant_message_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    pending_approval_json TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_event_seq INTEGER NOT NULL DEFAULT 0,
    last_workspace_event_seq INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS turn_submission_receipts (
    user_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    requested_session_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT NOT NULL DEFAULT '',
    expires_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_turn_submission_receipts_updated_at
ON turn_submission_receipts(updated_at);

CREATE INDEX IF NOT EXISTS idx_turn_submission_receipts_status_expires
ON turn_submission_receipts(status, expires_at);
"""


def _display_store_identity(db_path: Path) -> tuple[int, int] | None:
    try:
        stat_result = db_path.stat()
    except OSError:
        return None
    return (int(stat_result.st_dev), int(stat_result.st_ino))


def _initialize_display_store(db_path: Path) -> Path:
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
    tip_session_id TEXT NOT NULL DEFAULT '',
    assistant_message_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    pending_approval_json TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_event_seq INTEGER NOT NULL DEFAULT 0,
    last_workspace_event_seq INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS turn_submission_receipts (
    user_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    requested_session_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT NOT NULL DEFAULT '',
    expires_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_turn_submission_receipts_updated_at
ON turn_submission_receipts(updated_at);

CREATE INDEX IF NOT EXISTS idx_turn_submission_receipts_status_expires
ON turn_submission_receipts(status, expires_at);
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
        live_state_columns = {
            str(row[1])
            for row in conn.execute("pragma table_info(session_live_state)").fetchall()
        }
        if "tip_session_id" not in live_state_columns:
            conn.execute(
                "ALTER TABLE session_live_state ADD COLUMN tip_session_id TEXT NOT NULL DEFAULT ''"
            )
        if "last_workspace_event_seq" not in live_state_columns:
            conn.execute(
                "ALTER TABLE session_live_state ADD COLUMN last_workspace_event_seq INTEGER NOT NULL DEFAULT 0"
            )
        receipt_columns = {
            str(row[1])
            for row in conn.execute(
                "pragma table_info(turn_submission_receipts)"
            ).fetchall()
        }
        if "expires_at" not in receipt_columns:
            conn.execute(
                "ALTER TABLE turn_submission_receipts ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute(
                """
                UPDATE turn_submission_receipts
                SET expires_at = created_at + ?
                WHERE status = 'pending' AND expires_at = 0
                """,
                (TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS,),
            )
        conn.commit()
    return db_path


def ensure_display_store(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    cache_key = str(db_path.expanduser().absolute())
    identity = _display_store_identity(db_path)
    if identity is not None and _DISPLAY_STORE_IDENTITIES.get(cache_key) == identity:
        return db_path
    with _DISPLAY_STORE_INIT_LOCK:
        identity = _display_store_identity(db_path)
        if identity is not None and _DISPLAY_STORE_IDENTITIES.get(cache_key) == identity:
            return db_path
        _initialize_display_store(db_path)
        identity = _display_store_identity(db_path)
        if identity is not None:
            _DISPLAY_STORE_IDENTITIES[cache_key] = identity
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


def get_live_poll_snapshot(
    user_id: str,
    session_id: str,
    *,
    after_run_id: str = "",
    after_event_seq: int = -1,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    """Read live state and optional changed transcript through one RO connection."""
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        live_row = conn.execute(
            """
            select session_id, run_id, live_session_id, tip_session_id,
                   assistant_message_id, status,
                   pending_approval_json, last_error, last_event_seq,
                   last_workspace_event_seq,
                   created_at, updated_at, started_at, finished_at
            from session_live_state
            where user_id = ? and session_id = ?
            limit 1
            """,
            (user_id, session_id),
        ).fetchone()
        live_state = (
            _normalize_live_state_row(live_row) if live_row is not None else None
        )
        current_run_id = str((live_state or {}).get("run_id") or "")
        current_event_seq = int((live_state or {}).get("last_event_seq") or 0)
        current_workspace_event_seq = int(
            (live_state or {}).get("last_workspace_event_seq") or 0
        )
        file_tree_refresh = bool(
            current_run_id
            and str(after_run_id or "") == current_run_id
            and current_workspace_event_seq > 0
            and int(after_event_seq) < current_workspace_event_seq
        )
        cursor_changed = bool(
            not str(after_run_id or "")
            or str(after_run_id or "") != current_run_id
            or int(after_event_seq) < current_event_seq
        )
        transcript_columns = (
            "messages_json, updated_at" if cursor_changed else "updated_at"
        )
        transcript_row = conn.execute(
            f"""
            select {transcript_columns}
            from session_display_transcripts
            where user_id = ? and session_id = ?
            limit 1
            """,
            (user_id, session_id),
        ).fetchone()

    if live_row is None and transcript_row is None:
        return None

    include_messages = bool(transcript_row is not None and cursor_changed)
    messages: list[dict[str, Any]] | None = None
    if include_messages and transcript_row is not None:
        try:
            parsed = json.loads(str(transcript_row["messages_json"] or "[]"))
        except json.JSONDecodeError:
            parsed = []
        messages = parsed if isinstance(parsed, list) else []

    return {
        "session_id": session_id,
        "messages": messages,
        "display_updated_at": int(
            transcript_row["updated_at"] if transcript_row is not None else 0
        ),
        "file_tree_refresh": file_tree_refresh,
        "live": live_state,
    }


def find_live_session_id_by_run_id(
    user_id: str,
    run_id: str,
    *,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> str:
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            """
            select session_id
            from session_live_state
            where user_id = ? and run_id = ?
            order by updated_at desc
            limit 1
            """,
            (user_id, run_id),
        ).fetchone()
    return str(row[0] or "").strip() if row is not None else ""


def create_turn_submission_receipt(
    user_id: str,
    request_id: str,
    *,
    requested_session_id: str,
    pending_timeout_seconds: int = TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any]:
    ensure_display_store(db_path)
    now = int(time.time())
    expires_at = now + max(int(pending_timeout_seconds), 1)
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT requested_session_id, session_id, status, last_error,
                   expires_at, created_at, updated_at
            FROM turn_submission_receipts
            WHERE user_id = ? AND request_id = ?
            LIMIT 1
            """,
            (user_id, request_id),
        ).fetchone()
        created = row is None
        if created:
            conn.execute(
                """
                INSERT INTO turn_submission_receipts (
                    user_id, request_id, requested_session_id, session_id,
                    status, last_error, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, '', 'pending', '', ?, ?, ?)
                """,
                (
                    user_id,
                    request_id,
                    requested_session_id,
                    expires_at,
                    now,
                    now,
                ),
            )
            conn.commit()
            return {
                "user_id": user_id,
                "request_id": request_id,
                "requested_session_id": requested_session_id,
                "session_id": "",
                "status": "pending",
                "last_error": "",
                "expires_at": expires_at,
                "created_at": now,
                "updated_at": now,
                "created": True,
            }
        if (
            str(row["status"] or "") == "pending"
            and (
                (
                    int(row["expires_at"] or 0) > 0
                    and int(row["expires_at"] or 0) <= now
                )
                or (
                    int(row["expires_at"] or 0) <= 0
                    and int(row["updated_at"] or 0)
                    <= now - TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS
                )
            )
        ):
            conn.execute(
                """
                UPDATE turn_submission_receipts
                SET status = 'failed',
                    last_error = 'Turn submission did not complete before its deadline',
                    updated_at = ?
                WHERE user_id = ? AND request_id = ? AND status = 'pending'
                """,
                (now, user_id, request_id),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT requested_session_id, session_id, status, last_error,
                       expires_at, created_at, updated_at
                FROM turn_submission_receipts
                WHERE user_id = ? AND request_id = ?
                LIMIT 1
                """,
                (user_id, request_id),
            ).fetchone()
        else:
            conn.rollback()

    return {
        "user_id": user_id,
        "request_id": request_id,
        **dict(row),
        "created": False,
    }


def get_turn_submission_receipt(
    user_id: str,
    request_id: str,
    *,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    now = int(time.time())
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT requested_session_id, session_id, status, last_error,
                   expires_at, created_at, updated_at
            FROM turn_submission_receipts
            WHERE user_id = ? AND request_id = ?
            LIMIT 1
            """,
            (user_id, request_id),
        ).fetchone()
        if (
            row is not None
            and str(row["status"] or "") == "pending"
            and (
                (
                    int(row["expires_at"] or 0) > 0
                    and int(row["expires_at"] or 0) <= now
                )
                or (
                    int(row["expires_at"] or 0) <= 0
                    and int(row["updated_at"] or 0)
                    <= now - TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS
                )
            )
        ):
            cursor = conn.execute(
                """
                UPDATE turn_submission_receipts
                SET status = 'failed',
                    last_error = 'Turn submission did not complete before its deadline',
                    updated_at = ?
                WHERE user_id = ? AND request_id = ?
                  AND status = 'pending'
                  AND (
                      (expires_at > 0 AND expires_at <= ?)
                      OR (expires_at = 0 AND updated_at <= ?)
                  )
                """,
                (
                    now,
                    user_id,
                    request_id,
                    now,
                    now - TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS,
                ),
            )
            if cursor.rowcount > 0:
                conn.commit()
                row = conn.execute(
                    """
                    SELECT requested_session_id, session_id, status, last_error,
                           expires_at, created_at, updated_at
                    FROM turn_submission_receipts
                    WHERE user_id = ? AND request_id = ?
                    LIMIT 1
                    """,
                    (user_id, request_id),
                ).fetchone()
    if row is None:
        return None
    return {
        "user_id": user_id,
        "request_id": request_id,
        **dict(row),
    }


def finish_turn_submission_receipt(
    user_id: str,
    request_id: str,
    *,
    session_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    ensure_display_store(db_path)
    now = int(time.time())
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE turn_submission_receipts
            SET session_id = ?, status = 'submitted', last_error = '', updated_at = ?
            WHERE user_id = ? AND request_id = ? AND status = 'pending'
            """,
            (session_id, now, user_id, request_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def heartbeat_turn_submission_receipt(
    user_id: str,
    request_id: str,
    *,
    pending_timeout_seconds: int = TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    ensure_display_store(db_path)
    now = int(time.time())
    expires_at = now + max(int(pending_timeout_seconds), 1)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE turn_submission_receipts
            SET expires_at = ?, updated_at = ?
            WHERE user_id = ? AND request_id = ? AND status = 'pending'
            """,
            (expires_at, now, user_id, request_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def fail_turn_submission_receipt(
    user_id: str,
    request_id: str,
    *,
    error_message: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    ensure_display_store(db_path)
    now = int(time.time())
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE turn_submission_receipts
            SET status = 'failed', last_error = ?, updated_at = ?
            WHERE user_id = ? AND request_id = ? AND status = 'pending'
            """,
            (str(error_message or ""), now, user_id, request_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def cleanup_turn_submission_receipts(
    *,
    retention_seconds: int = TURN_SUBMISSION_RECEIPT_RETENTION_SECONDS,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, int]:
    ensure_display_store(db_path)
    checked_at = int(time.time()) if now is None else int(now)
    retention_cutoff = checked_at - max(int(retention_seconds), 1)
    with connect_auth_db(db_path) as conn:
        expired_cursor = conn.execute(
            """
            UPDATE turn_submission_receipts
            SET status = 'failed',
                last_error = 'Turn submission did not complete before its deadline',
                updated_at = ?
            WHERE status = 'pending'
              AND (
                  (expires_at > 0 AND expires_at <= ?)
                  OR (expires_at = 0 AND updated_at <= ?)
              )
            """,
            (
                checked_at,
                checked_at,
                checked_at - TURN_SUBMISSION_PENDING_TIMEOUT_SECONDS,
            ),
        )
        deleted_cursor = conn.execute(
            """
            DELETE FROM turn_submission_receipts
            WHERE status != 'pending' AND updated_at <= ?
            """,
            (retention_cutoff,),
        )
        conn.commit()
    return {
        "expired": int(expired_cursor.rowcount or 0),
        "deleted": int(deleted_cursor.rowcount or 0),
    }


def list_display_session_metas(
    user_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
    *,
    include_messages: bool = True,
) -> dict[str, dict[str, Any]]:
    ensure_display_store(db_path)
    with connect_auth_db(db_path) as conn:
        if include_messages:
            rows = conn.execute(
                """
                select session_id, messages_json, draft_title, created_at, updated_at
                from session_display_transcripts
                where user_id = ?
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select session_id, draft_title, created_at, updated_at
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
        payload: list[Any] = []
        if include_messages:
            try:
                raw_payload = json.loads(str(row["messages_json"] or "[]"))
            except json.JSONDecodeError:
                raw_payload = []
            payload = raw_payload if isinstance(raw_payload, list) else []
        result[session_id] = {
            "messages": payload,
            "message_count": len(payload),
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
ACTIVE_LIVE_STATE_STATUSES = ("queued", "starting", "running", "awaiting_approval")


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
        "tip_session_id": str(row["tip_session_id"] or ""),
        "assistant_message_id": str(row["assistant_message_id"] or ""),
        "status": str(row["status"] or ""),
        "pending_approval": pending_approval,
        "last_error": str(row["last_error"] or ""),
        "last_event_seq": int(row["last_event_seq"] or 0),
        "last_workspace_event_seq": int(row["last_workspace_event_seq"] or 0),
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
            select run_id, live_session_id, tip_session_id, assistant_message_id, status,
                   pending_approval_json, last_error, last_event_seq,
                   last_workspace_event_seq,
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
            select session_id, run_id, live_session_id, tip_session_id,
                   assistant_message_id, status,
                   pending_approval_json, last_error, last_event_seq,
                   last_workspace_event_seq,
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


def mark_active_live_session_states_failed(
    error_message: str,
    *,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> int:
    ensure_display_store(db_path)
    now = int(time.time())
    placeholders = ",".join("?" for _ in ACTIVE_LIVE_STATE_STATUSES)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            f"""
            update session_live_state
            set status = 'failed',
                pending_approval_json = '',
                last_error = ?,
                updated_at = ?,
                finished_at = case when finished_at > 0 then finished_at else ? end
            where status in ({placeholders})
            """,
            (str(error_message or ""), now, now, *ACTIVE_LIVE_STATE_STATUSES),
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def save_live_session_state(
    user_id: str,
    session_id: str,
    *,
    run_id: str | None = None,
    live_session_id: str | None = None,
    tip_session_id: str | None = None,
    assistant_message_id: str | None = None,
    status: str | None = None,
    pending_approval: dict[str, Any] | None | object = _MISSING,
    last_error: str | None = None,
    last_event_seq: int | None = None,
    last_workspace_event_seq: int | None = None,
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
            select run_id, live_session_id, tip_session_id, assistant_message_id, status,
                   pending_approval_json, last_error, last_event_seq,
                   last_workspace_event_seq,
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
            "tip_session_id": str(
                tip_session_id
                if tip_session_id is not None
                else (existing_payload or {}).get("tip_session_id") or ""
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
            "last_workspace_event_seq": int(
                last_workspace_event_seq
                if last_workspace_event_seq is not None
                else (existing_payload or {}).get("last_workspace_event_seq") or 0
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
                    user_id, session_id, run_id, live_session_id, tip_session_id,
                    assistant_message_id, status, pending_approval_json, last_error,
                    last_event_seq, last_workspace_event_seq,
                    created_at, updated_at, started_at, finished_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    next_payload["run_id"],
                    next_payload["live_session_id"],
                    next_payload["tip_session_id"],
                    next_payload["assistant_message_id"],
                    next_payload["status"],
                    pending_approval_json,
                    next_payload["last_error"],
                    next_payload["last_event_seq"],
                    next_payload["last_workspace_event_seq"],
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
                set run_id = ?, live_session_id = ?, tip_session_id = ?,
                    assistant_message_id = ?, status = ?, pending_approval_json = ?,
                    last_error = ?, last_event_seq = ?, last_workspace_event_seq = ?,
                    updated_at = ?,
                    started_at = ?, finished_at = ?
                where user_id = ? and session_id = ?
                """,
                (
                    next_payload["run_id"],
                    next_payload["live_session_id"],
                    next_payload["tip_session_id"],
                    next_payload["assistant_message_id"],
                    next_payload["status"],
                    pending_approval_json,
                    next_payload["last_error"],
                    next_payload["last_event_seq"],
                    next_payload["last_workspace_event_seq"],
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


def delete_display_user_data(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> dict[str, int]:
    ensure_display_store(db_path)
    normalized_user_id = user_id.strip()
    with connect_auth_db(db_path) as conn:
        display_cursor = conn.execute(
            "delete from session_display_transcripts where user_id = ?",
            (normalized_user_id,),
        )
        live_cursor = conn.execute(
            "delete from session_live_state where user_id = ?",
            (normalized_user_id,),
        )
        event_cursor = conn.execute(
            "delete from session_event_journal where user_id = ?",
            (normalized_user_id,),
        )
        receipt_cursor = conn.execute(
            "delete from turn_submission_receipts where user_id = ?",
            (normalized_user_id,),
        )
        conn.commit()
    return {
        "display_messages": int(display_cursor.rowcount or 0),
        "live_states": int(live_cursor.rowcount or 0),
        "events": int(event_cursor.rowcount or 0),
        "turn_submission_receipts": int(receipt_cursor.rowcount or 0),
    }

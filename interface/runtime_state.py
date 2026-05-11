from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from interface.auth_db import DEFAULT_AUTH_DB_PATH, connect_auth_db


FOREGROUND_CHAT_LEASE = "foreground_chat"
BACKGROUND_JOB_LEASE = "background_job"


def ensure_runtime_state_store(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    with connect_auth_db(db_path) as conn:
        conn.executescript(
            """
CREATE TABLE IF NOT EXISTS runtime_state (
    user_id TEXT PRIMARY KEY,
    runtime_started_at INTEGER NOT NULL DEFAULT 0,
    last_user_message_at INTEGER NOT NULL DEFAULT 0,
    last_background_activity_at INTEGER NOT NULL DEFAULT 0,
    session_revoked_after INTEGER NOT NULL DEFAULT 0,
    last_sleep_at INTEGER NOT NULL DEFAULT 0,
    last_sleep_reason TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_leases (
    lease_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    lease_type TEXT NOT NULL,
    resource_id TEXT NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL,
    heartbeat_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_leases_user_id
ON runtime_leases(user_id);

CREATE INDEX IF NOT EXISTS idx_runtime_leases_expires_at
ON runtime_leases(expires_at);

CREATE INDEX IF NOT EXISTS idx_runtime_leases_user_type
ON runtime_leases(user_id, lease_type);
"""
        )
        columns = {
            str(row[1])
            for row in conn.execute("pragma table_info(runtime_state)").fetchall()
        }
        if "last_background_activity_at" not in columns:
            conn.execute(
                "ALTER TABLE runtime_state ADD COLUMN last_background_activity_at INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()
    return db_path


def _now() -> int:
    return int(time.time())


def _ensure_runtime_state_row(user_id: str, conn: sqlite3.Connection) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO runtime_state (
            user_id,
            runtime_started_at,
            last_user_message_at,
            last_background_activity_at,
            session_revoked_after,
            last_sleep_at,
            last_sleep_reason,
            updated_at
        ) VALUES (?, 0, 0, 0, 0, 0, '', ?)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, now),
    )


def mark_runtime_started(user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> None:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_sleep_reason = '',
                updated_at = ?
            WHERE user_id = ?
            """,
            (now, now, user_id),
        )
        conn.commit()


def mark_foreground_activity(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> None:
    """Record foreground user activity for idle-timeout calculations.

    This intentionally reuses last_user_message_at to avoid a schema change; the
    field is already part of the runtime idle baseline.
    """
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        conn.execute(
            """
            UPDATE runtime_state
            SET last_user_message_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (now, now, user_id),
        )
        conn.commit()


def mark_user_message_activity(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> None:
    mark_foreground_activity(user_id, db_path=db_path)


def mark_background_activity(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> None:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        conn.execute(
            """
            UPDATE runtime_state
            SET last_background_activity_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (now, now, user_id),
        )
        conn.commit()


def revoke_runtime_session(
    user_id: str,
    *,
    reason: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> None:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        conn.execute(
            """
            UPDATE runtime_state
            SET session_revoked_after = ?,
                runtime_started_at = 0,
                last_user_message_at = 0,
                last_background_activity_at = 0,
                last_sleep_at = ?,
                last_sleep_reason = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (now, now, reason, now, user_id),
        )
        conn.execute(
            "DELETE FROM runtime_leases WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()


def clear_session_revocation(user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> None:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        conn.execute(
            """
            UPDATE runtime_state
            SET session_revoked_after = 0,
                last_sleep_reason = '',
                updated_at = ?
            WHERE user_id = ?
            """,
            (now, user_id),
        )
        conn.commit()


def get_runtime_state(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> dict[str, Any] | None:
    ensure_runtime_state_store(db_path)
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT user_id, runtime_started_at, last_user_message_at,
                   last_background_activity_at,
                   session_revoked_after, last_sleep_at, last_sleep_reason,
                   updated_at
            FROM runtime_state
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def create_runtime_lease(
    user_id: str,
    *,
    lease_type: str,
    ttl_seconds: int,
    resource_id: str = "",
    meta: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> str:
    ensure_runtime_state_store(db_path)
    now = _now()
    lease_id = str(uuid.uuid4())
    expires_at = now + max(int(ttl_seconds), 1)
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        conn.execute(
            """
            INSERT INTO runtime_leases (
                lease_id,
                user_id,
                lease_type,
                resource_id,
                started_at,
                heartbeat_at,
                expires_at,
                meta_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lease_id,
                user_id,
                lease_type,
                resource_id,
                now,
                now,
                expires_at,
                json.dumps(meta or {}, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        conn.commit()
    return lease_id


def heartbeat_runtime_lease(
    lease_id: str,
    *,
    ttl_seconds: int,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    ensure_runtime_state_store(db_path)
    now = _now()
    expires_at = now + max(int(ttl_seconds), 1)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE runtime_leases
            SET heartbeat_at = ?, expires_at = ?, updated_at = ?
            WHERE lease_id = ?
            """,
            (now, expires_at, now, lease_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def release_runtime_lease(
    lease_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    ensure_runtime_state_store(db_path)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM runtime_leases WHERE lease_id = ?",
            (lease_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def cleanup_expired_runtime_leases(db_path: Path = DEFAULT_AUTH_DB_PATH) -> int:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM runtime_leases WHERE expires_at <= ?",
            (now,),
        )
        conn.commit()
        return cursor.rowcount


def has_active_runtime_leases(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM runtime_leases
            WHERE user_id = ? AND expires_at > ?
            LIMIT 1
            """,
            (user_id, now),
        ).fetchone()
    return row is not None


def list_idle_runtime_candidates(
    *,
    idle_timeout_seconds: int,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> list[dict[str, Any]]:
    ensure_runtime_state_store(db_path)
    now = _now()
    cutoff = now - max(int(idle_timeout_seconds), 1)
    query = """
        SELECT rs.user_id,
               rs.runtime_started_at,
               rs.last_user_message_at,
               rs.last_background_activity_at,
               rs.session_revoked_after,
               rs.last_sleep_at,
               rs.last_sleep_reason,
               rs.updated_at,
               max(
                   rs.runtime_started_at,
                   rs.last_user_message_at,
                   rs.last_background_activity_at
               ) AS idle_since
        FROM runtime_state rs
        WHERE rs.runtime_started_at > 0
          AND max(
                rs.runtime_started_at,
                rs.last_user_message_at,
                rs.last_background_activity_at
          ) > 0
          AND max(
                rs.runtime_started_at,
                rs.last_user_message_at,
                rs.last_background_activity_at
          ) <= ?
          AND NOT EXISTS (
                SELECT 1
                FROM runtime_leases rl
                WHERE rl.user_id = rs.user_id AND rl.expires_at > ?
          )
        ORDER BY idle_since ASC
    """
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(query, (cutoff, now)).fetchall()
    return [dict(row) for row in rows]

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from interface.auth_db import (
    DEFAULT_AUTH_DB_PATH,
    TEMPORARY_USER_STATUS_ACTIVE,
    TEMPORARY_USER_STATUS_CLEANING,
    TEMPORARY_USER_STATUS_FAILED,
    connect_auth_db,
)


FOREGROUND_CHAT_LEASE = "foreground_chat"
BACKGROUND_JOB_LEASE = "background_job"
DEFAULT_RUNTIME_IDLE_TIMEOUT_SECONDS = 30 * 60
RUNTIME_SLEEP_CLAIM_TTL_SECONDS = 5 * 60
_RUNTIME_STORE_INIT_LOCK = threading.Lock()
_RUNTIME_STORE_IDENTITIES: dict[str, tuple[int, int]] = {}


def _runtime_store_identity(db_path: Path) -> tuple[int, int] | None:
    try:
        stat_result = db_path.stat()
    except OSError:
        return None
    return (int(stat_result.st_dev), int(stat_result.st_ino))


def ensure_runtime_state_store(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    cache_key = str(db_path.expanduser().absolute())
    identity = _runtime_store_identity(db_path)
    if identity is not None and _RUNTIME_STORE_IDENTITIES.get(cache_key) == identity:
        return db_path
    with _RUNTIME_STORE_INIT_LOCK:
        identity = _runtime_store_identity(db_path)
        if identity is not None and _RUNTIME_STORE_IDENTITIES.get(cache_key) == identity:
            return db_path
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

CREATE TABLE IF NOT EXISTS runtime_sleep_claims (
    user_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    claimed_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_sleep_claims_claimed_at
ON runtime_sleep_claims(claimed_at);
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
        identity = _runtime_store_identity(db_path)
        if identity is not None:
            _RUNTIME_STORE_IDENTITIES[cache_key] = identity
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
) -> bool:
    """Record foreground user activity for idle-timeout calculations.

    This intentionally reuses last_user_message_at to avoid a schema change; the
    field is already part of the runtime idle baseline.
    """
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        cursor = conn.execute(
            """
            UPDATE runtime_state
            SET last_user_message_at = ?, updated_at = ?
            WHERE user_id = ?
              AND NOT EXISTS (
                    SELECT 1
                    FROM temporary_users tu
                    WHERE tu.user_id = runtime_state.user_id
                      AND tu.cleanup_status = ?
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM runtime_sleep_claims rsc
                    WHERE rsc.user_id = runtime_state.user_id
                      AND rsc.claimed_at > ?
              )
            """,
            (
                now,
                now,
                user_id,
                TEMPORARY_USER_STATUS_CLEANING,
                now - RUNTIME_SLEEP_CLAIM_TTL_SECONDS,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def mark_user_message_activity(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    return mark_foreground_activity(user_id, db_path=db_path)


def mark_background_activity(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        _ensure_runtime_state_row(user_id, conn)
        cursor = conn.execute(
            """
            UPDATE runtime_state
            SET last_background_activity_at = ?, updated_at = ?
            WHERE user_id = ?
              AND NOT EXISTS (
                    SELECT 1
                    FROM temporary_users tu
                    WHERE tu.user_id = runtime_state.user_id
                      AND tu.cleanup_status = ?
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM runtime_sleep_claims rsc
                    WHERE rsc.user_id = runtime_state.user_id
                      AND rsc.claimed_at > ?
              )
            """,
            (
                now,
                now,
                user_id,
                TEMPORARY_USER_STATUS_CLEANING,
                now - RUNTIME_SLEEP_CLAIM_TTL_SECONDS,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


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
        conn.execute(
            "DELETE FROM runtime_sleep_claims WHERE user_id = ?",
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


def delete_runtime_state(user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> None:
    ensure_runtime_state_store(db_path)
    normalized_user_id = user_id.strip()
    with connect_auth_db(db_path) as conn:
        conn.execute("DELETE FROM runtime_leases WHERE user_id = ?", (normalized_user_id,))
        conn.execute("DELETE FROM runtime_sleep_claims WHERE user_id = ?", (normalized_user_id,))
        conn.execute("DELETE FROM runtime_state WHERE user_id = ?", (normalized_user_id,))
        conn.commit()


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
        conn.execute("BEGIN IMMEDIATE")
        _ensure_runtime_state_row(user_id, conn)
        conn.execute(
            "DELETE FROM runtime_sleep_claims WHERE user_id = ? AND claimed_at <= ?",
            (user_id, now - RUNTIME_SLEEP_CLAIM_TTL_SECONDS),
        )
        sleep_claim = conn.execute(
            "SELECT 1 FROM runtime_sleep_claims WHERE user_id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        temporary_cleanup = conn.execute(
            """
            SELECT 1
            FROM temporary_users
            WHERE user_id = ? AND cleanup_status = ?
            LIMIT 1
            """,
            (user_id, TEMPORARY_USER_STATUS_CLEANING),
        ).fetchone()
        if sleep_claim is not None or temporary_cleanup is not None:
            conn.rollback()
            raise RuntimeError("runtime cleanup or sleep is in progress")
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
              AND NOT EXISTS (
                    SELECT 1
                    FROM runtime_sleep_claims rsc
                    WHERE rsc.user_id = runtime_leases.user_id
                      AND rsc.claimed_at > ?
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM temporary_users tu
                    WHERE tu.user_id = runtime_leases.user_id
                      AND tu.cleanup_status = ?
              )
            """,
            (
                now,
                expires_at,
                now,
                lease_id,
                now - RUNTIME_SLEEP_CLAIM_TTL_SECONDS,
                TEMPORARY_USER_STATUS_CLEANING,
            ),
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


def finish_runtime_lease(
    lease_id: str,
    *,
    user_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    """Atomically reset the idle baseline and release a foreground lease."""
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_runtime_state_row(user_id, conn)
        lease = conn.execute(
            "SELECT 1 FROM runtime_leases WHERE lease_id = ? AND user_id = ?",
            (lease_id, user_id),
        ).fetchone()
        if lease is None:
            conn.commit()
            return False
        sleep_claim = conn.execute(
            """
            SELECT 1
            FROM runtime_sleep_claims
            WHERE user_id = ? AND claimed_at > ?
            LIMIT 1
            """,
            (user_id, now - RUNTIME_SLEEP_CLAIM_TTL_SECONDS),
        ).fetchone()
        if sleep_claim is not None:
            conn.rollback()
            raise RuntimeError("runtime sleep is in progress")
        temporary_cleanup = conn.execute(
            """
            SELECT 1
            FROM temporary_users
            WHERE user_id = ? AND cleanup_status = ?
            LIMIT 1
            """,
            (user_id, TEMPORARY_USER_STATUS_CLEANING),
        ).fetchone()
        if temporary_cleanup is not None:
            conn.rollback()
            raise RuntimeError("temporary runtime cleanup is in progress")
        conn.execute(
            """
            UPDATE runtime_state
            SET last_user_message_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (now, now, user_id),
        )
        cursor = conn.execute(
            "DELETE FROM runtime_leases WHERE lease_id = ? AND user_id = ?",
            (lease_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def claim_runtime_sleep(
    user_id: str,
    *,
    idle_timeout_seconds: int,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> str | None:
    """Atomically claim an idle regular-user runtime before stopping it."""
    ensure_runtime_state_store(db_path)
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        return None
    checked_at = _now() if now is None else int(now)
    cutoff = checked_at - max(int(idle_timeout_seconds), 1)
    stale_claim_cutoff = checked_at - RUNTIME_SLEEP_CLAIM_TTL_SECONDS
    claim_id = uuid.uuid4().hex
    query = """
        SELECT rs.runtime_started_at,
               max(
                   rs.runtime_started_at,
                   rs.last_user_message_at,
                   rs.last_background_activity_at
               ) AS latest_activity_at,
               EXISTS (
                   SELECT 1
                   FROM runtime_leases rl
                   WHERE rl.user_id = rs.user_id AND rl.expires_at > ?
               ) AS has_active_runtime_lease,
               EXISTS (
                   SELECT 1
                   FROM temporary_users tu
                   WHERE tu.user_id = rs.user_id
               ) AS is_temporary
        FROM runtime_state rs
        WHERE rs.user_id = ?
        LIMIT 1
    """
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM runtime_sleep_claims WHERE user_id = ? AND claimed_at <= ?",
            (normalized_user_id, stale_claim_cutoff),
        )
        row = conn.execute(query, (checked_at, normalized_user_id)).fetchone()
        if row is None:
            conn.rollback()
            return None
        runtime_started_at = int(row["runtime_started_at"] or 0)
        latest_activity_at = int(row["latest_activity_at"] or 0)
        if (
            runtime_started_at <= 0
            or latest_activity_at <= 0
            or latest_activity_at > cutoff
            or bool(row["has_active_runtime_lease"])
            or bool(row["is_temporary"])
        ):
            conn.rollback()
            return None
        conn.execute(
            "DELETE FROM runtime_leases WHERE user_id = ? AND expires_at <= ?",
            (normalized_user_id, checked_at),
        )
        try:
            conn.execute(
                """
                INSERT INTO runtime_sleep_claims (user_id, claim_id, claimed_at)
                VALUES (?, ?, ?)
                """,
                (normalized_user_id, claim_id, checked_at),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return None
        conn.commit()
    return claim_id


def runtime_sleep_claim_is_valid(
    user_id: str,
    *,
    claim_id: str,
    idle_timeout_seconds: int,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    """Recheck ownership, activity, and leases immediately before sleep."""
    ensure_runtime_state_store(db_path)
    normalized_user_id = user_id.strip()
    normalized_claim_id = claim_id.strip()
    if not normalized_user_id or not normalized_claim_id:
        return False
    checked_at = _now() if now is None else int(now)
    cutoff = checked_at - max(int(idle_timeout_seconds), 1)
    query = """
        SELECT rsc.claimed_at,
               rs.runtime_started_at,
               max(
                   rs.runtime_started_at,
                   rs.last_user_message_at,
                   rs.last_background_activity_at
               ) AS latest_activity_at,
               EXISTS (
                   SELECT 1
                   FROM runtime_leases rl
                   WHERE rl.user_id = rs.user_id AND rl.expires_at > ?
               ) AS has_active_runtime_lease,
               EXISTS (
                   SELECT 1
                   FROM temporary_users tu
                   WHERE tu.user_id = rs.user_id
               ) AS is_temporary
        FROM runtime_sleep_claims rsc
        JOIN runtime_state rs ON rs.user_id = rsc.user_id
        WHERE rsc.user_id = ? AND rsc.claim_id = ?
        LIMIT 1
    """
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            query,
            (checked_at, normalized_user_id, normalized_claim_id),
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        valid = bool(
            int(row["claimed_at"] or 0)
            > checked_at - RUNTIME_SLEEP_CLAIM_TTL_SECONDS
            and int(row["runtime_started_at"] or 0) > 0
            and int(row["latest_activity_at"] or 0) > 0
            and int(row["latest_activity_at"] or 0) <= cutoff
            and not bool(row["has_active_runtime_lease"])
            and not bool(row["is_temporary"])
        )
        if not valid:
            conn.rollback()
            return False
        cursor = conn.execute(
            """
            UPDATE runtime_sleep_claims
            SET claimed_at = ?
            WHERE user_id = ? AND claim_id = ?
            """,
            (checked_at, normalized_user_id, normalized_claim_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def release_runtime_sleep_claim(
    user_id: str,
    *,
    claim_id: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    """Release only the sleep claim owned by the current worker."""
    ensure_runtime_state_store(db_path)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM runtime_sleep_claims WHERE user_id = ? AND claim_id = ?",
            (user_id.strip(), claim_id.strip()),
        )
        conn.commit()
        return cursor.rowcount == 1


def cleanup_expired_runtime_leases(db_path: Path = DEFAULT_AUTH_DB_PATH) -> int:
    ensure_runtime_state_store(db_path)
    now = _now()
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM runtime_leases WHERE expires_at <= ?",
            (now,),
        )
        conn.execute(
            "DELETE FROM runtime_sleep_claims WHERE claimed_at <= ?",
            (now - RUNTIME_SLEEP_CLAIM_TTL_SECONDS,),
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


def get_runtime_idle_eligibility(
    user_id: str,
    *,
    idle_timeout_seconds: int,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    """Read the latest activity and active-lease state from one DB snapshot."""
    ensure_runtime_state_store(db_path)
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        return None
    checked_at = _now() if now is None else int(now)
    cutoff = checked_at - max(int(idle_timeout_seconds), 1)
    query = """
        SELECT rs.user_id,
               rs.runtime_started_at,
               rs.last_user_message_at,
               rs.last_background_activity_at,
               max(
                   rs.runtime_started_at,
                   rs.last_user_message_at,
                   rs.last_background_activity_at
               ) AS latest_activity_at,
               EXISTS (
                   SELECT 1
                   FROM runtime_leases rl
                   WHERE rl.user_id = rs.user_id AND rl.expires_at > ?
               ) AS has_active_runtime_lease,
               EXISTS (
                   SELECT 1
                   FROM runtime_sleep_claims rsc
                   WHERE rsc.user_id = rs.user_id AND rsc.claimed_at > ?
               ) AS has_active_sleep_claim
        FROM runtime_state rs
        WHERE rs.user_id = ?
        LIMIT 1
    """
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            query,
            (
                checked_at,
                checked_at - RUNTIME_SLEEP_CLAIM_TTL_SECONDS,
                normalized_user_id,
            ),
        ).fetchone()
    if row is None:
        return None

    result = dict(row)
    runtime_started_at = int(result.get("runtime_started_at") or 0)
    latest_activity_at = int(result.get("latest_activity_at") or 0)
    has_active_runtime_lease = bool(result.get("has_active_runtime_lease"))
    has_active_sleep_claim = bool(result.get("has_active_sleep_claim"))
    eligible = bool(
        runtime_started_at > 0
        and latest_activity_at > 0
        and latest_activity_at <= cutoff
        and not has_active_runtime_lease
        and not has_active_sleep_claim
    )
    result["checked_at"] = checked_at
    result["cutoff"] = cutoff
    result["has_active_runtime_lease"] = has_active_runtime_lease
    result["has_active_sleep_claim"] = has_active_sleep_claim
    result["eligible"] = eligible
    if has_active_sleep_claim:
        result["reason"] = "sleep_in_progress"
    elif has_active_runtime_lease:
        result["reason"] = "active_lease"
    elif not eligible:
        result["reason"] = "recent_activity"
    else:
        result["reason"] = ""
    return result


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
          AND NOT EXISTS (
                SELECT 1
                FROM runtime_sleep_claims rsc
                WHERE rsc.user_id = rs.user_id AND rsc.claimed_at > ?
          )
        ORDER BY idle_since ASC
    """
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(
            query,
            (cutoff, now, now - RUNTIME_SLEEP_CLAIM_TTL_SECONDS),
        ).fetchall()
    return [dict(row) for row in rows]


def list_idle_temporary_user_candidates(
    *,
    idle_timeout_seconds: int,
    cleanup_retry_seconds: int,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> list[dict[str, Any]]:
    ensure_runtime_state_store(db_path)
    now = _now()
    cutoff = now - max(int(idle_timeout_seconds), 1)
    retry_cutoff = now - max(int(cleanup_retry_seconds), 1)
    query = """
        SELECT *
        FROM (
            SELECT tu.user_id,
                   tu.mapping_username,
                   tu.created_at,
                   tu.last_cleanup_attempt_at,
                   tu.cleanup_status,
                   tu.cleanup_error,
                   rs.runtime_started_at,
                   rs.last_user_message_at,
                   rs.last_background_activity_at,
                   max(
                       tu.created_at,
                       coalesce(rs.runtime_started_at, 0),
                       coalesce(rs.last_user_message_at, 0),
                       coalesce(rs.last_background_activity_at, 0)
                   ) AS idle_since
            FROM temporary_users tu
            LEFT JOIN runtime_state rs ON rs.user_id = tu.user_id
            WHERE tu.cleanup_status IN (?, ?, ?)
        ) candidate
        WHERE idle_since > 0
          AND idle_since <= ?
          AND (
                cleanup_status = ?
                OR last_cleanup_attempt_at <= ?
          )
          AND NOT EXISTS (
                SELECT 1
                FROM runtime_leases rl
                WHERE rl.user_id = candidate.user_id AND rl.expires_at > ?
          )
        ORDER BY idle_since ASC
    """
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(
            query,
            (
                TEMPORARY_USER_STATUS_ACTIVE,
                TEMPORARY_USER_STATUS_CLEANING,
                TEMPORARY_USER_STATUS_FAILED,
                cutoff,
                TEMPORARY_USER_STATUS_ACTIVE,
                retry_cutoff,
                now,
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_temporary_user_cleanup(
    user_id: str,
    *,
    idle_timeout_seconds: int,
    cleanup_retry_seconds: int,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    """Atomically recheck and claim an expired temporary user for cleanup."""
    ensure_runtime_state_store(db_path)
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        return None
    checked_at = _now() if now is None else int(now)
    cutoff = checked_at - max(int(idle_timeout_seconds), 1)
    retry_cutoff = checked_at - max(int(cleanup_retry_seconds), 1)
    query = """
        SELECT tu.user_id,
               tu.mapping_username,
               tu.created_at,
               tu.last_cleanup_attempt_at,
               tu.cleanup_status,
               tu.cleanup_error,
               coalesce(rs.runtime_started_at, 0) AS runtime_started_at,
               coalesce(rs.last_user_message_at, 0) AS last_user_message_at,
               coalesce(rs.last_background_activity_at, 0)
                   AS last_background_activity_at,
               max(
                   tu.created_at,
                   coalesce(rs.runtime_started_at, 0),
                   coalesce(rs.last_user_message_at, 0),
                   coalesce(rs.last_background_activity_at, 0)
               ) AS latest_activity_at,
               EXISTS (
                   SELECT 1
                   FROM runtime_leases rl
                   WHERE rl.user_id = tu.user_id AND rl.expires_at > ?
               ) AS has_active_runtime_lease
        FROM temporary_users tu
        LEFT JOIN runtime_state rs ON rs.user_id = tu.user_id
        WHERE tu.user_id = ?
        LIMIT 1
    """
    with connect_auth_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(query, (checked_at, normalized_user_id)).fetchone()
        if row is None:
            conn.rollback()
            return None

        result = dict(row)
        cleanup_status = str(result.get("cleanup_status") or "")
        last_cleanup_attempt_at = int(result.get("last_cleanup_attempt_at") or 0)
        latest_activity_at = int(result.get("latest_activity_at") or 0)
        has_active_runtime_lease = bool(result.get("has_active_runtime_lease"))
        retry_eligible = bool(
            cleanup_status == TEMPORARY_USER_STATUS_ACTIVE
            or last_cleanup_attempt_at <= retry_cutoff
        )
        eligible = bool(
            cleanup_status
            in {
                TEMPORARY_USER_STATUS_ACTIVE,
                TEMPORARY_USER_STATUS_CLEANING,
                TEMPORARY_USER_STATUS_FAILED,
            }
            and retry_eligible
            and latest_activity_at > 0
            and latest_activity_at <= cutoff
            and not has_active_runtime_lease
        )
        if not eligible:
            conn.rollback()
            return None

        conn.execute(
            "DELETE FROM runtime_leases WHERE user_id = ? AND expires_at <= ?",
            (normalized_user_id, checked_at),
        )

        cursor = conn.execute(
            """
            UPDATE temporary_users
            SET cleanup_status = ?,
                cleanup_error = '',
                last_cleanup_attempt_at = ?
            WHERE user_id = ?
              AND cleanup_status = ?
              AND last_cleanup_attempt_at = ?
            """,
            (
                TEMPORARY_USER_STATUS_CLEANING,
                checked_at,
                normalized_user_id,
                cleanup_status,
                last_cleanup_attempt_at,
            ),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()

    result["previous_cleanup_status"] = cleanup_status
    result["cleanup_status"] = TEMPORARY_USER_STATUS_CLEANING
    result["cleanup_error"] = ""
    result["last_cleanup_attempt_at"] = checked_at
    result["checked_at"] = checked_at
    result["cutoff"] = cutoff
    result["has_active_runtime_lease"] = False
    return result


def temporary_cleanup_claim_is_valid(
    user_id: str,
    *,
    claimed_at: int,
    idle_timeout_seconds: int,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    """Recheck a cleanup claim against activity and leases before deletion."""
    ensure_runtime_state_store(db_path)
    normalized_user_id = user_id.strip()
    if not normalized_user_id or int(claimed_at or 0) <= 0:
        return False
    checked_at = _now() if now is None else int(now)
    cutoff = checked_at - max(int(idle_timeout_seconds), 1)
    query = """
        SELECT tu.cleanup_status,
               tu.last_cleanup_attempt_at,
               max(
                   tu.created_at,
                   coalesce(rs.runtime_started_at, 0),
                   coalesce(rs.last_user_message_at, 0),
                   coalesce(rs.last_background_activity_at, 0)
               ) AS latest_activity_at,
               EXISTS (
                   SELECT 1
                   FROM runtime_leases rl
                   WHERE rl.user_id = tu.user_id AND rl.expires_at > ?
               ) AS has_active_runtime_lease
        FROM temporary_users tu
        LEFT JOIN runtime_state rs ON rs.user_id = tu.user_id
        WHERE tu.user_id = ?
        LIMIT 1
    """
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (checked_at, normalized_user_id)).fetchone()
    if row is None:
        return False
    return bool(
        str(row["cleanup_status"] or "") == TEMPORARY_USER_STATUS_CLEANING
        and int(row["last_cleanup_attempt_at"] or 0) == int(claimed_at)
        and int(row["latest_activity_at"] or 0) > 0
        and int(row["latest_activity_at"] or 0) <= cutoff
        and not bool(row["has_active_runtime_lease"])
    )


def release_temporary_cleanup_claim(
    user_id: str,
    *,
    claimed_at: int,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    """Restore only the exact cleanup claim owned by the current worker."""
    ensure_runtime_state_store(db_path)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE temporary_users
            SET cleanup_status = ?, cleanup_error = ''
            WHERE user_id = ?
              AND cleanup_status = ?
              AND last_cleanup_attempt_at = ?
            """,
            (
                TEMPORARY_USER_STATUS_ACTIVE,
                user_id.strip(),
                TEMPORARY_USER_STATUS_CLEANING,
                int(claimed_at),
            ),
        )
        conn.commit()
        return cursor.rowcount == 1


def get_temporary_user_idle_status(
    user_id: str,
    *,
    idle_timeout_seconds: int,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    ensure_runtime_state_store(db_path)
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        return None
    now = _now()
    cutoff = now - max(int(idle_timeout_seconds), 1)
    query = """
        SELECT tu.user_id,
               tu.mapping_username,
               tu.created_at,
               tu.cleanup_status,
               rs.runtime_started_at,
               rs.last_user_message_at,
               rs.last_background_activity_at,
               max(
                   tu.created_at,
                   coalesce(rs.runtime_started_at, 0),
                   coalesce(rs.last_user_message_at, 0),
                   coalesce(rs.last_background_activity_at, 0)
               ) AS idle_since,
               EXISTS (
                   SELECT 1
                   FROM runtime_leases rl
                   WHERE rl.user_id = tu.user_id AND rl.expires_at > ?
               ) AS has_active_runtime_lease
        FROM temporary_users tu
        LEFT JOIN runtime_state rs ON rs.user_id = tu.user_id
        WHERE tu.user_id = ?
        LIMIT 1
    """
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (now, normalized_user_id)).fetchone()
    if row is None:
        return None
    result = dict(row)
    idle_since = int(result.get("idle_since") or 0)
    has_active_runtime_lease = bool(result.get("has_active_runtime_lease"))
    result["now"] = now
    result["idle_elapsed_seconds"] = max(now - idle_since, 0) if idle_since else 0
    result["has_active_runtime_lease"] = has_active_runtime_lease
    result["is_expired"] = bool(
        idle_since and idle_since <= cutoff and not has_active_runtime_lease
    )
    return result

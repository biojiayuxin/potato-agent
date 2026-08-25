from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Iterable

from interface.auth_db import DEFAULT_AUTH_DB_PATH, connect_auth_db


ADMIN_SIGNIN_WINDOW_SECONDS = 15 * 60
ADMIN_LOGIN_IP_FAILURE_LIMIT = 5
ADMIN_IP_FAILURE_LIMIT = 20
PRINCIPAL_CATALOG_MIGRATION = "principal_catalog_v1"
LEGACY_TEMPORARY_PRINCIPAL_RE = re.compile(r"^temp_\d{10}_[0-9a-f]{8}$")
SERVICE_PRINCIPALS = frozenset({"daily-updates-service"})


def signin_retry_after(
    *,
    login_ip_key: str,
    ip_key: str,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> int:
    timestamp = int(time.time()) if now is None else int(now)
    limits = {
        login_ip_key: ADMIN_LOGIN_IP_FAILURE_LIMIT,
        ip_key: ADMIN_IP_FAILURE_LIMIT,
    }
    retry_after = 0
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(
            "select key_hash, window_started_at, attempt_count "
            "from admin_signin_limits where key_hash in (?, ?)",
            (login_ip_key, ip_key),
        ).fetchall()
    for row in rows:
        key_hash = str(row["key_hash"])
        elapsed = timestamp - int(row["window_started_at"])
        if elapsed < ADMIN_SIGNIN_WINDOW_SECONDS and int(row["attempt_count"]) >= limits[key_hash]:
            retry_after = max(retry_after, ADMIN_SIGNIN_WINDOW_SECONDS - max(0, elapsed))
    return retry_after


def record_signin_failure(
    *,
    login_ip_key: str,
    ip_key: str,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> int:
    timestamp = int(time.time()) if now is None else int(now)
    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
        for scope, key_hash in (("login_ip", login_ip_key), ("ip", ip_key)):
            row = conn.execute(
                "select window_started_at from admin_signin_limits where key_hash = ?",
                (key_hash,),
            ).fetchone()
            if row is None or timestamp - int(row["window_started_at"]) >= ADMIN_SIGNIN_WINDOW_SECONDS:
                conn.execute(
                    "insert into admin_signin_limits "
                    "(key_hash, scope, window_started_at, attempt_count, last_attempt_at) "
                    "values (?, ?, ?, 1, ?) "
                    "on conflict(key_hash) do update set scope = excluded.scope, "
                    "window_started_at = excluded.window_started_at, attempt_count = 1, "
                    "last_attempt_at = excluded.last_attempt_at",
                    (key_hash, scope, timestamp, timestamp),
                )
            else:
                conn.execute(
                    "update admin_signin_limits set attempt_count = attempt_count + 1, "
                    "last_attempt_at = ? where key_hash = ?",
                    (timestamp, key_hash),
                )
        conn.execute(
            "delete from admin_signin_limits where last_attempt_at < ?",
            (timestamp - 7 * 24 * 3600,),
        )
        conn.commit()
    return signin_retry_after(
        login_ip_key=login_ip_key,
        ip_key=ip_key,
        now=timestamp,
        db_path=db_path,
    )


def clear_login_signin_failures(
    login_ip_key: str, *, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> None:
    with connect_auth_db(db_path) as conn:
        conn.execute(
            "delete from admin_signin_limits where key_hash = ? and scope = 'login_ip'",
            (login_ip_key,),
        )
        conn.commit()


def list_storage_snapshot_candidates(
    *, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> list[dict[str, Any]]:
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(
            """
            select u.id as user_id, u.mapping_username,
                   case when t.user_id is null then 'formal' else 'temporary' end as account_type
            from users u
            join user_usage_identities i
              on i.user_id = u.id
             and i.mapping_username = u.mapping_username
             and i.retired_at is null
            left join temporary_users t on t.user_id = u.id
            where u.active = 1
              and (t.user_id is null or t.cleanup_status = 'active')
            order by u.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def save_storage_snapshot(
    *,
    user_id: str,
    mapping_username: str,
    snapshot_day: str,
    sampled_at: int,
    allocated_bytes: int | None,
    status: str,
    error_code: str = "",
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    normalized_status = status.strip().lower()
    if normalized_status not in {"ok", "error"}:
        raise ValueError("snapshot status must be 'ok' or 'error'")
    if normalized_status == "ok" and (allocated_bytes is None or int(allocated_bytes) < 0):
        raise ValueError("successful snapshots require non-negative allocated_bytes")
    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
        current = conn.execute(
            """
            select 1
            from users u
            join user_usage_identities i
              on i.user_id = u.id
             and i.mapping_username = u.mapping_username
             and i.retired_at is null
            left join temporary_users t on t.user_id = u.id
            where u.id = ? and u.mapping_username = ? and u.active = 1
              and (t.user_id is null or t.cleanup_status = 'active')
            limit 1
            """,
            (user_id.strip(), mapping_username.strip()),
        ).fetchone()
        if current is None:
            conn.rollback()
            return False
        conn.execute(
            """
            insert into user_storage_snapshots
                (user_id, snapshot_day, sampled_at, allocated_bytes, status, error_code)
            values (?, ?, ?, ?, ?, ?)
            on conflict(user_id, snapshot_day) do update set
                sampled_at = excluded.sampled_at,
                allocated_bytes = excluded.allocated_bytes,
                status = excluded.status,
                error_code = excluded.error_code
            """,
            (
                user_id.strip(),
                snapshot_day,
                int(sampled_at),
                int(allocated_bytes) if allocated_bytes is not None else None,
                normalized_status,
                error_code.strip()[:64],
            ),
        )
        conn.commit()
    return True


def list_overview_entities(
    *, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> list[dict[str, Any]]:
    with connect_auth_db(db_path) as conn:
        current_rows = conn.execute(
            """
            select u.id as user_id, u.username, u.email, u.name, u.role,
                   u.mapping_username, u.active, u.created_at,
                   case when t.user_id is null then 'formal' else 'temporary' end as account_type,
                   null as retired_at
            from users u
            left join temporary_users t on t.user_id = u.id
            order by u.created_at, u.username
            """
        ).fetchall()
        retired_rows = conn.execute(
            """
            select i.user_id, '' as username, '' as email, '' as name, '' as role,
                   i.mapping_username, 0 as active, i.created_at,
                   i.account_type, i.retired_at
            from user_usage_identities i
            left join users u on u.id = i.user_id
            where i.account_type = 'temporary'
              and i.retired_at is not null
              and u.id is null
            order by i.retired_at desc, i.mapping_username
            """
        ).fetchall()
        snapshot_rows = conn.execute(
            """
            select s.user_id, s.sampled_at, s.allocated_bytes, s.status, s.error_code
            from user_storage_snapshots s
            join (
                select user_id, max(sampled_at) as sampled_at
                from user_storage_snapshots
                group by user_id
            ) latest on latest.user_id = s.user_id and latest.sampled_at = s.sampled_at
            """
        ).fetchall()
    snapshots = {str(row["user_id"]): dict(row) for row in snapshot_rows}
    entities: list[dict[str, Any]] = []
    for row in (*current_rows, *retired_rows):
        entity = dict(row)
        entity["active"] = bool(int(entity["active"] or 0))
        entity["is_retired"] = entity["retired_at"] is not None
        entity["storage"] = snapshots.get(str(entity.get("user_id") or ""))
        entities.append(entity)
    return entities


def migration_completed(
    migration_key: str, *, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select 1 from admin_migrations where migration_key = ? limit 1",
            (migration_key,),
        ).fetchone()
    return row is not None


def reconcile_principal_catalog(
    principals: Iterable[dict[str, Any]],
    *,
    migration_key: str = PRINCIPAL_CATALOG_MIGRATION,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
        completed = conn.execute(
            "select 1 from admin_migrations where migration_key = ? limit 1",
            (migration_key,),
        ).fetchone()
        if completed is not None:
            conn.rollback()
            return False
        for principal in principals:
            mapping_username = str(principal.get("mapping_username") or "").strip()
            if not mapping_username:
                continue
            existing = conn.execute(
                "select 1 from user_usage_identities where mapping_username = ? limit 1",
                (mapping_username,),
            ).fetchone()
            if existing is not None:
                continue
            first_used_at = int(float(principal.get("first_used_at") or timestamp))
            last_used_at = int(float(principal.get("last_used_at") or first_used_at))
            if mapping_username in SERVICE_PRINCIPALS:
                account_type = "service"
                retired_at = None
            elif LEGACY_TEMPORARY_PRINCIPAL_RE.fullmatch(mapping_username):
                account_type = "temporary"
                retired_at = max(last_used_at, first_used_at)
            else:
                account_type = "unknown"
                retired_at = max(last_used_at, first_used_at)
            conn.execute(
                "insert into user_usage_identities "
                "(mapping_username, user_id, account_type, created_at, retired_at, source) "
                "values (?, null, ?, ?, ?, ?)",
                (
                    mapping_username,
                    account_type,
                    first_used_at,
                    retired_at,
                    migration_key,
                ),
            )
        conn.execute(
            "insert into admin_migrations (migration_key, completed_at) values (?, ?)",
            (migration_key, timestamp),
        )
        conn.commit()
    return True

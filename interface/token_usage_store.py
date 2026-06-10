from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from interface import auth_db


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS model_proxy_usage_requests (
    id TEXT PRIMARY KEY,
    mapping_username TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    route_model TEXT NOT NULL,
    upstream_model TEXT NOT NULL,
    provider TEXT NOT NULL,
    api_mode TEXT NOT NULL DEFAULT '',
    status_code INTEGER NOT NULL,
    streaming INTEGER NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    completed_at REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    usage_status TEXT NOT NULL,
    raw_usage_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_model_proxy_usage_user_started
ON model_proxy_usage_requests(mapping_username, started_at);

CREATE INDEX IF NOT EXISTS idx_model_proxy_usage_started
ON model_proxy_usage_requests(started_at);

CREATE INDEX IF NOT EXISTS idx_model_proxy_usage_route_model_started
ON model_proxy_usage_requests(route_model, started_at);

CREATE TABLE IF NOT EXISTS model_proxy_user_quotas (
    mapping_username TEXT PRIMARY KEY,
    input_token_limit INTEGER,
    output_token_limit INTEGER,
    cache_read_token_limit INTEGER,
    cache_write_token_limit INTEGER,
    total_token_limit INTEGER,
    period TEXT NOT NULL DEFAULT 'monthly',
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def _default_db_path() -> Path:
    return Path(os.getenv("INTERFACE_AUTH_DB") or auth_db.DEFAULT_AUTH_DB_PATH)


def _resolve_db_path(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else _default_db_path()


def ensure_token_usage_store(db_path: Path | str | None = None) -> Path:
    resolved = _resolve_db_path(db_path)
    with auth_db.connect_auth_db(resolved) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    return resolved


def _usage_totals_from_row(row: sqlite3.Row | None) -> dict[str, int]:
    result = {
        "request_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
    }
    if row is None:
        return result
    result["request_count"] = int(row["request_count"] or 0)
    for field in TOKEN_FIELDS:
        result[field] = int(row[field] or 0)
    result["total_tokens"] = sum(result[field] for field in TOKEN_FIELDS)
    return result


def _time_filter(
    *,
    start_at: float | int | None,
    end_at: float | int | None,
) -> tuple[str, list[float]]:
    clauses: list[str] = []
    params: list[float] = []
    if start_at is not None:
        clauses.append("started_at >= ?")
        params.append(float(start_at))
    if end_at is not None:
        clauses.append("started_at < ?")
        params.append(float(end_at))
    return (" and " + " and ".join(clauses)) if clauses else "", params


def record_usage_request(
    *,
    mapping_username: str,
    endpoint: str,
    route_model: str,
    upstream_model: str,
    provider: str,
    api_mode: str | None = None,
    status_code: int,
    streaming: bool,
    started_at: float,
    completed_at: float,
    duration_ms: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    usage_status: str,
    raw_usage: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> str:
    ensure_token_usage_store(db_path)
    request_id = str(uuid.uuid4())
    raw_usage_json = json.dumps(
        raw_usage or {}, ensure_ascii=False, separators=(",", ":")
    )
    with auth_db.connect_auth_db(_resolve_db_path(db_path)) as conn:
        conn.execute(
            """
            insert into model_proxy_usage_requests (
                id, mapping_username, endpoint, route_model, upstream_model,
                provider, api_mode, status_code, streaming, started_at,
                completed_at, duration_ms, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, usage_status,
                raw_usage_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                mapping_username.strip(),
                endpoint.strip(),
                route_model.strip(),
                upstream_model.strip(),
                provider.strip(),
                (api_mode or "").strip(),
                int(status_code),
                1 if streaming else 0,
                float(started_at),
                float(completed_at),
                int(duration_ms),
                max(0, int(input_tokens or 0)),
                max(0, int(output_tokens or 0)),
                max(0, int(cache_read_tokens or 0)),
                max(0, int(cache_write_tokens or 0)),
                usage_status.strip() or "missing",
                raw_usage_json,
            ),
        )
        conn.commit()
    return request_id


def get_user_usage(
    mapping_username: str,
    start_at: float | int | None = None,
    end_at: float | int | None = None,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    ensure_token_usage_store(db_path)
    time_sql, time_params = _time_filter(start_at=start_at, end_at=end_at)
    with auth_db.connect_auth_db(_resolve_db_path(db_path)) as conn:
        row = conn.execute(
            f"""
            select
                count(*) as request_count,
                coalesce(sum(input_tokens), 0) as input_tokens,
                coalesce(sum(output_tokens), 0) as output_tokens,
                coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
                coalesce(sum(cache_write_tokens), 0) as cache_write_tokens
            from model_proxy_usage_requests
            where mapping_username = ?{time_sql}
            """,
            (mapping_username.strip(), *time_params),
        ).fetchone()
    return {
        "mapping_username": mapping_username.strip(),
        **_usage_totals_from_row(row),
    }


def get_usage_by_user(
    start_at: float | int | None = None,
    end_at: float | int | None = None,
    *,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    ensure_token_usage_store(db_path)
    time_sql, time_params = _time_filter(start_at=start_at, end_at=end_at)
    where_sql = f"where {time_sql[5:]}" if time_sql else ""
    with auth_db.connect_auth_db(_resolve_db_path(db_path)) as conn:
        rows = conn.execute(
            f"""
            select
                mapping_username,
                count(*) as request_count,
                coalesce(sum(input_tokens), 0) as input_tokens,
                coalesce(sum(output_tokens), 0) as output_tokens,
                coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
                coalesce(sum(cache_write_tokens), 0) as cache_write_tokens
            from model_proxy_usage_requests
            {where_sql}
            group by mapping_username
            order by mapping_username
            """,
            tuple(time_params),
        ).fetchall()
    return [
        {
            "mapping_username": str(row["mapping_username"]),
            **_usage_totals_from_row(row),
        }
        for row in rows
    ]


def _row_to_quota(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "mapping_username": str(row["mapping_username"]),
        "input_token_limit": row["input_token_limit"],
        "output_token_limit": row["output_token_limit"],
        "cache_read_token_limit": row["cache_read_token_limit"],
        "cache_write_token_limit": row["cache_write_token_limit"],
        "total_token_limit": row["total_token_limit"],
        "period": str(row["period"] or "monthly"),
        "enabled": bool(int(row["enabled"] or 0)),
        "created_at": int(row["created_at"] or 0),
        "updated_at": int(row["updated_at"] or 0),
    }


def get_user_quota(
    mapping_username: str, *, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    ensure_token_usage_store(db_path)
    with auth_db.connect_auth_db(_resolve_db_path(db_path)) as conn:
        row = conn.execute(
            """
            select
                mapping_username, input_token_limit, output_token_limit,
                cache_read_token_limit, cache_write_token_limit,
                total_token_limit, period, enabled, created_at, updated_at
            from model_proxy_user_quotas
            where mapping_username = ?
            limit 1
            """,
            (mapping_username.strip(),),
        ).fetchone()
    return _row_to_quota(row)


def set_user_quota(
    mapping_username: str,
    *,
    input_token_limit: int | None = None,
    output_token_limit: int | None = None,
    cache_read_token_limit: int | None = None,
    cache_write_token_limit: int | None = None,
    total_token_limit: int | None = None,
    period: str = "monthly",
    enabled: bool = False,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    ensure_token_usage_store(db_path)
    now = int(time.time())
    normalized_username = mapping_username.strip()
    with auth_db.connect_auth_db(_resolve_db_path(db_path)) as conn:
        conn.execute(
            """
            insert into model_proxy_user_quotas (
                mapping_username, input_token_limit, output_token_limit,
                cache_read_token_limit, cache_write_token_limit,
                total_token_limit, period, enabled, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(mapping_username) do update set
                input_token_limit = excluded.input_token_limit,
                output_token_limit = excluded.output_token_limit,
                cache_read_token_limit = excluded.cache_read_token_limit,
                cache_write_token_limit = excluded.cache_write_token_limit,
                total_token_limit = excluded.total_token_limit,
                period = excluded.period,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                normalized_username,
                input_token_limit,
                output_token_limit,
                cache_read_token_limit,
                cache_write_token_limit,
                total_token_limit,
                period.strip() or "monthly",
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()
    quota = get_user_quota(normalized_username, db_path=db_path)
    if quota is None:
        raise RuntimeError("Failed to persist model proxy user quota")
    return quota


def get_quota_snapshot(
    *, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    ensure_token_usage_store(db_path)
    with auth_db.connect_auth_db(_resolve_db_path(db_path)) as conn:
        rows = conn.execute(
            """
            select
                mapping_username, input_token_limit, output_token_limit,
                cache_read_token_limit, cache_write_token_limit,
                total_token_limit, period, enabled, created_at, updated_at
            from model_proxy_user_quotas
            order by mapping_username
            """
        ).fetchall()
    return [quota for quota in (_row_to_quota(row) for row in rows) if quota is not None]

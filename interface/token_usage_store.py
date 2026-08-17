from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from interface.secure_paths import (
    DEFAULT_PRIVATE_FILE_MODE,
    DEFAULT_PRIVATE_WRITABLE_DIR_MODE,
    DEFAULT_STATE_DIR,
    ensure_private_directory,
    ensure_sqlite_sidecar_modes,
)


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

CREATE TABLE IF NOT EXISTS web_search_usage_requests (
    id TEXT PRIMARY KEY,
    mapping_username TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    status_code INTEGER NOT NULL,
    started_at REAL NOT NULL,
    completed_at REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    credits_used REAL,
    error_code TEXT,
    provider_request_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_web_search_usage_user_started
ON web_search_usage_requests(mapping_username, started_at);

CREATE INDEX IF NOT EXISTS idx_web_search_usage_started
ON web_search_usage_requests(started_at);

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
VALID_QUOTA_PERIODS = {"daily", "weekly", "monthly"}
DEFAULT_MODEL_PROXY_USAGE_DB_PATH = DEFAULT_STATE_DIR / "model-proxy" / "usage.db"
SQLITE_BUSY_TIMEOUT_SECONDS = 5.0


def _default_db_path() -> Path:
    return Path(
        os.getenv("POTATO_MODEL_PROXY_USAGE_DB")
        or DEFAULT_MODEL_PROXY_USAGE_DB_PATH
    )


def _resolve_db_path(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else _default_db_path()


def _connect_usage_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_token_usage_store(db_path: Path | str | None = None) -> Path:
    resolved = _resolve_db_path(db_path)
    ensure_private_directory(
        resolved.parent, mode=DEFAULT_PRIVATE_WRITABLE_DIR_MODE
    )
    with _connect_usage_db(resolved) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    ensure_sqlite_sidecar_modes(resolved, mode=DEFAULT_PRIVATE_FILE_MODE)
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
    del raw_usage
    normalized_tokens = {
        "input_tokens": max(0, int(input_tokens or 0)),
        "output_tokens": max(0, int(output_tokens or 0)),
        "cache_read_tokens": max(0, int(cache_read_tokens or 0)),
        "cache_write_tokens": max(0, int(cache_write_tokens or 0)),
    }
    raw_usage_json = json.dumps(
        normalized_tokens if usage_status.strip() == "present" else {},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with _connect_usage_db(_resolve_db_path(db_path)) as conn:
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
                normalized_tokens["input_tokens"],
                normalized_tokens["output_tokens"],
                normalized_tokens["cache_read_tokens"],
                normalized_tokens["cache_write_tokens"],
                usage_status.strip() or "missing",
                raw_usage_json,
            ),
        )
        conn.commit()
    return request_id


def record_web_search_usage(
    *,
    mapping_username: str,
    topic: str,
    status_code: int,
    started_at: float,
    completed_at: float,
    duration_ms: int,
    result_count: int,
    credits_used: float | int | None,
    error_code: str | None,
    provider_request_id: str | None,
    db_path: Path | str | None = None,
) -> str:
    ensure_token_usage_store(db_path)
    request_id = str(uuid.uuid4())
    normalized_credits = None
    if credits_used is not None:
        normalized_credits = float(credits_used)
        if not math.isfinite(normalized_credits) or normalized_credits < 0:
            raise ValueError("credits_used must be a finite non-negative number")
    with _connect_usage_db(_resolve_db_path(db_path)) as conn:
        conn.execute(
            """
            insert into web_search_usage_requests (
                id, mapping_username, topic, status_code, started_at,
                completed_at, duration_ms, result_count, credits_used,
                error_code, provider_request_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                mapping_username.strip(),
                topic.strip(),
                int(status_code),
                float(started_at),
                float(completed_at),
                max(0, int(duration_ms)),
                max(0, int(result_count)),
                normalized_credits,
                (error_code or "").strip() or None,
                (provider_request_id or "").strip() or None,
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
    with _connect_usage_db(_resolve_db_path(db_path)) as conn:
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
    with _connect_usage_db(_resolve_db_path(db_path)) as conn:
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
    with _connect_usage_db(_resolve_db_path(db_path)) as conn:
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
    if not normalized_username:
        raise ValueError("mapping_username is required")
    normalized_period = period.strip().lower() or "monthly"
    if normalized_period not in VALID_QUOTA_PERIODS:
        raise ValueError(
            "period must be one of: " + ", ".join(sorted(VALID_QUOTA_PERIODS))
        )
    limits = (
        input_token_limit,
        output_token_limit,
        cache_read_token_limit,
        cache_write_token_limit,
        total_token_limit,
    )
    if any(limit is not None and int(limit) < 0 for limit in limits):
        raise ValueError("token limits must be non-negative")
    with _connect_usage_db(_resolve_db_path(db_path)) as conn:
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
                normalized_period,
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
    with _connect_usage_db(_resolve_db_path(db_path)) as conn:
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


def _quota_period_bounds(period: str, now: float) -> tuple[float, float]:
    current = datetime.fromtimestamp(float(now), tz=timezone.utc)
    if period == "daily":
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "weekly":
        start = (current - timedelta(days=current.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(days=7)
    elif period == "monthly":
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    else:
        raise ValueError(f"Unsupported quota period: {period}")
    return start.timestamp(), end.timestamp()


def get_user_quota_status(
    mapping_username: str,
    *,
    now: float | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    quota = get_user_quota(mapping_username, db_path=db_path)
    if quota is None or not quota["enabled"]:
        return {
            "allowed": True,
            "quota": quota,
            "usage": None,
            "exceeded": [],
            "reset_at": None,
        }

    current = time.time() if now is None else float(now)
    start_at, reset_at = _quota_period_bounds(str(quota["period"]), current)
    usage = get_user_usage(
        mapping_username,
        start_at=start_at,
        end_at=reset_at,
        db_path=db_path,
    )
    fields = {
        "input_tokens": "input_token_limit",
        "output_tokens": "output_token_limit",
        "cache_read_tokens": "cache_read_token_limit",
        "cache_write_tokens": "cache_write_token_limit",
        "total_tokens": "total_token_limit",
    }
    exceeded = [
        usage_field
        for usage_field, limit_field in fields.items()
        if quota[limit_field] is not None
        and int(usage[usage_field]) >= int(quota[limit_field])
    ]
    return {
        "allowed": not exceeded,
        "quota": quota,
        "usage": usage,
        "exceeded": exceeded,
        "reset_at": reset_at,
    }

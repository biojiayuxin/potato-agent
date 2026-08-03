from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query


DEFAULT_DB_PATH = Path("/srv/daily_updates/data/daily_updates.sqlite")
DATABASE_UNAVAILABLE_DETAIL = "Daily updates are unavailable."
INVALID_CURSOR_DETAIL = "Invalid daily updates cursor."
MAX_PAGE_SIZE = 50
REQUIRED_TABLES = frozenset({"papers", "processing_state", "job_runs"})

LOGGER = logging.getLogger("potato_interface.daily_updates")
router = APIRouter()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    pmid TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    title_zh TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    journal TEXT NOT NULL DEFAULT '',
    publication_date TEXT NOT NULL DEFAULT '',
    pubmed_date TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_state (
    pmid TEXT PRIMARY KEY REFERENCES papers(pmid) ON DELETE CASCADE,
    relevance_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (relevance_status IN ('pending', 'relevant', 'irrelevant', 'retry')),
    summary_en TEXT NOT NULL DEFAULT '',
    translation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (translation_status IN ('pending', 'complete', 'retry', 'not_applicable')),
    summary_zh TEXT NOT NULL DEFAULT '',
    relevance_attempts INTEGER NOT NULL DEFAULT 0 CHECK (relevance_attempts >= 0),
    translation_attempts INTEGER NOT NULL DEFAULT 0 CHECK (translation_attempts >= 0),
    next_retry_at TEXT,
    last_error_category TEXT,
    processed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_date TEXT NOT NULL,
    search_start_date TEXT NOT NULL,
    search_end_date TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    candidate_count INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    relevant_count INTEGER NOT NULL DEFAULT 0 CHECK (relevant_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    checkpoint_date TEXT,
    error_category TEXT
);

CREATE INDEX IF NOT EXISTS idx_daily_updates_public
    ON papers(pubmed_date DESC, pmid DESC);
CREATE INDEX IF NOT EXISTS idx_processing_retry
    ON processing_state(relevance_status, translation_status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_job_runs_completed
    ON job_runs(completed_at DESC, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_runs_one_running
    ON job_runs((1)) WHERE status = 'running';
"""


def get_database_path() -> Path:
    return Path(os.getenv("DAILY_UPDATES_DB_PATH") or DEFAULT_DB_PATH).resolve()


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path), timeout=10.0) as conn:
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.executescript(SCHEMA_SQL)
        conn.commit()


def connect_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA trusted_schema = OFF")
    return conn


def connect_read_only(path: Path | None = None) -> sqlite3.Connection:
    db_path = (path or get_database_path()).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)

    conn = sqlite3.connect(
        f"{db_path.as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("PRAGMA busy_timeout = 5000")
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not REQUIRED_TABLES.issubset(tables):
            raise ValueError("daily updates schema is incomplete")
        return conn
    except Exception:
        conn.close()
        raise


def _encode_cursor(sort_date: str, pmid: str) -> str:
    payload = json.dumps([sort_date, pmid], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    if not cursor or len(cursor) > 512:
        raise ValueError("invalid cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        value = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(item, str) and item for item in value)
        or len(value[0]) > 64
        or len(value[1]) > 64
    ):
        raise ValueError("invalid cursor")
    return value[0], value[1]


def _last_run_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT status, completed_at
        FROM job_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return {"status": "never", "completedAt": None}
    completed_at = row["completed_at"]
    return {
        "status": str(row["status"]),
        "completedAt": str(completed_at) if completed_at is not None else None,
    }


def list_daily_updates(
    *,
    limit: int = 10,
    cursor: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

    cursor_values = _decode_cursor(cursor) if cursor else None
    try:
        with closing(connect_read_only(db_path)) as conn:
            params: list[Any] = []
            cursor_clause = ""
            if cursor_values is not None:
                cursor_clause = """
                    WHERE sort_date < ? OR (sort_date = ? AND pmid < ?)
                """
                params.extend(
                    [cursor_values[0], cursor_values[0], cursor_values[1]]
                )
            params.append(limit + 1)
            rows = conn.execute(
                f"""
                WITH public_updates AS (
                    SELECT
                        p.pmid,
                        p.title,
                        p.title_zh,
                        s.summary_en,
                        s.summary_zh,
                        p.journal,
                        p.publication_date,
                        p.pubmed_date,
                        p.doi,
                        p.url,
                        COALESCE(
                            NULLIF(p.pubmed_date, ''),
                            NULLIF(p.publication_date, ''),
                            substr(p.first_seen_at, 1, 10)
                        ) AS sort_date
                    FROM papers AS p
                    JOIN processing_state AS s ON s.pmid = p.pmid
                    WHERE s.relevance_status = 'relevant'
                      AND trim(s.summary_en) <> ''
                )
                SELECT * FROM public_updates
                {cursor_clause}
                ORDER BY sort_date DESC, pmid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            has_more = len(rows) > limit
            visible_rows = rows[:limit]
            next_cursor = None
            if has_more and visible_rows:
                last = visible_rows[-1]
                next_cursor = _encode_cursor(
                    str(last["sort_date"]), str(last["pmid"])
                )
            items = [
                {
                    "pmid": str(row["pmid"]),
                    "title": str(row["title"] or ""),
                    "titleZh": str(row["title_zh"] or ""),
                    "summary": str(row["summary_en"] or ""),
                    "summaryZh": str(row["summary_zh"] or ""),
                    "journal": str(row["journal"] or ""),
                    "publicationDate": str(row["publication_date"] or ""),
                    "pubmedDate": str(row["pubmed_date"] or ""),
                    "doi": str(row["doi"] or ""),
                    "url": str(row["url"] or ""),
                }
                for row in visible_rows
            ]
            return {
                "items": items,
                "nextCursor": next_cursor,
                "hasMore": has_more,
                "lastRun": _last_run_payload(conn),
            }
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        LOGGER.warning("Daily updates query failed: %s", type(exc).__name__)
        raise RuntimeError(DATABASE_UNAVAILABLE_DETAIL) from exc


@router.get("/api/daily-updates")
async def daily_updates_api(
    limit: int = Query(default=10, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            list_daily_updates,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=INVALID_CURSOR_DETAIL) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_DETAIL) from exc

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from interface.secure_paths import (
    DEFAULT_PRIVATE_WRITABLE_DIR_MODE,
    DEFAULT_STATE_DIR,
    ensure_private_directory,
    ensure_sqlite_sidecar_modes,
)


DEFAULT_FEEDBACK_DB_PATH = Path(
    os.getenv("INTERFACE_FEEDBACK_DB")
    or (DEFAULT_STATE_DIR / "data" / "feedback.db")
)
FEEDBACK_HOURLY_LIMIT = 20
FEEDBACK_RATE_WINDOW_SECONDS = 60 * 60
FEEDBACK_RETENTION_SECONDS = 30 * 24 * 60 * 60
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
_STORE_INIT_LOCK = threading.RLock()

CREATE_FEEDBACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS feedback_submissions (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('pending', 'sent', 'failed')),
    resend_email_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""

CREATE_FEEDBACK_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_feedback_submissions_created_at
ON feedback_submissions(created_at)
"""


@dataclass(frozen=True)
class FeedbackClaim:
    submission_id: str | None
    retry_after: int = 0

    @property
    def accepted(self) -> bool:
        return self.submission_id is not None


def _resolve_db_path(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_FEEDBACK_DB_PATH


def _connect_feedback_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
    conn.execute("PRAGMA secure_delete = ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_feedback_store(db_path: Path | str | None = None) -> Path:
    resolved = _resolve_db_path(db_path)
    with _STORE_INIT_LOCK:
        ensure_private_directory(
            resolved.parent, mode=DEFAULT_PRIVATE_WRITABLE_DIR_MODE
        )
        with _connect_feedback_db(resolved) as conn:
            conn.execute("begin immediate")
            conn.execute(CREATE_FEEDBACK_TABLE_SQL)
            columns = {
                str(row["name"])
                for row in conn.execute("pragma table_info(feedback_submissions)")
            }
            if "message" not in columns:
                conn.execute(
                    "ALTER TABLE feedback_submissions "
                    "ADD COLUMN message TEXT NOT NULL DEFAULT ''"
                )
            if "contact_email" not in columns:
                conn.execute(
                    "ALTER TABLE feedback_submissions "
                    "ADD COLUMN contact_email TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(CREATE_FEEDBACK_INDEX_SQL)
            conn.commit()
        ensure_sqlite_sidecar_modes(resolved)
    return resolved


def cleanup_feedback_submissions(
    *,
    now: int | None = None,
    retention_seconds: int = FEEDBACK_RETENTION_SECONDS,
    db_path: Path | str | None = None,
) -> int:
    if int(retention_seconds) < 1:
        raise ValueError("feedback retention must be at least one second")
    timestamp = int(time.time()) if now is None else int(now)
    cutoff = timestamp - int(retention_seconds)
    resolved = ensure_feedback_store(db_path)
    with _connect_feedback_db(resolved) as conn:
        cursor = conn.execute(
            "delete from feedback_submissions where created_at < ?",
            (cutoff,),
        )
        conn.commit()
        deleted = max(0, int(cursor.rowcount))
    ensure_sqlite_sidecar_modes(resolved)
    return deleted


def claim_feedback_submission(
    *,
    message: str,
    contact_email: str,
    now: int | None = None,
    hourly_limit: int = FEEDBACK_HOURLY_LIMIT,
    window_seconds: int = FEEDBACK_RATE_WINDOW_SECONDS,
    retention_seconds: int = FEEDBACK_RETENTION_SECONDS,
    db_path: Path | str | None = None,
) -> FeedbackClaim:
    normalized_limit = int(hourly_limit)
    normalized_window = int(window_seconds)
    normalized_retention = int(retention_seconds)
    if normalized_limit < 1:
        raise ValueError("feedback hourly limit must be at least one")
    if normalized_window < 1:
        raise ValueError("feedback rate window must be at least one second")
    if normalized_retention < normalized_window:
        raise ValueError("feedback retention must cover the rate window")

    timestamp = int(time.time()) if now is None else int(now)
    window_start = timestamp - normalized_window
    retention_cutoff = timestamp - normalized_retention
    resolved = ensure_feedback_store(db_path)

    with _connect_feedback_db(resolved) as conn:
        conn.execute("begin immediate")
        conn.execute(
            "delete from feedback_submissions where created_at < ?",
            (retention_cutoff,),
        )
        row = conn.execute(
            """
            select count(*) as submission_count, min(created_at) as earliest_created_at
            from feedback_submissions
            where created_at > ?
            """,
            (window_start,),
        ).fetchone()
        submission_count = int(row["submission_count"] or 0)
        if submission_count >= normalized_limit:
            earliest_created_at = int(row["earliest_created_at"] or timestamp)
            retry_after = max(
                1,
                earliest_created_at + normalized_window - timestamp,
            )
            conn.commit()
            return FeedbackClaim(submission_id=None, retry_after=retry_after)

        submission_id = str(uuid.uuid4())
        conn.execute(
            """
            insert into feedback_submissions (
                id, message, contact_email, status, resend_email_id,
                created_at, updated_at
            ) values (?, ?, ?, 'pending', '', ?, ?)
            """,
            (submission_id, message, contact_email, timestamp, timestamp),
        )
        conn.commit()

    ensure_sqlite_sidecar_modes(resolved)
    return FeedbackClaim(submission_id=submission_id)


def finish_feedback_submission(
    submission_id: str,
    *,
    status: str,
    resend_email_id: str = "",
    now: int | None = None,
    db_path: Path | str | None = None,
) -> None:
    normalized_id = submission_id.strip()
    normalized_status = status.strip()
    if not normalized_id:
        raise ValueError("feedback submission id cannot be empty")
    if normalized_status not in {"sent", "failed"}:
        raise ValueError("feedback submission status must be sent or failed")

    timestamp = int(time.time()) if now is None else int(now)
    normalized_resend_id = resend_email_id.strip()[:512]
    resolved = ensure_feedback_store(db_path)
    with _connect_feedback_db(resolved) as conn:
        cursor = conn.execute(
            """
            update feedback_submissions
            set status = ?, resend_email_id = ?, updated_at = ?
            where id = ?
            """,
            (
                normalized_status,
                normalized_resend_id,
                timestamp,
                normalized_id,
            ),
        )
        conn.commit()
        if cursor.rowcount != 1:
            raise LookupError("feedback submission not found")
    ensure_sqlite_sidecar_modes(resolved)

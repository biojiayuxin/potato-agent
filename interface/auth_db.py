from __future__ import annotations

import hmac
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bcrypt

from interface.secure_paths import (
    DEFAULT_PRIVATE_WRITABLE_DIR_MODE,
    DEFAULT_STATE_DIR,
    ensure_private_directory,
    ensure_sqlite_sidecar_modes,
)


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_AUTH_DB_PATH = Path(
    os.getenv("INTERFACE_AUTH_DB") or (DEFAULT_STATE_DIR / "data" / "interface.db")
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    mapping_username TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    auth_session_version INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interface_users_mapping_username
ON users(mapping_username);

CREATE TABLE IF NOT EXISTS temporary_users (
    user_id TEXT PRIMARY KEY,
    mapping_username TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    last_cleanup_attempt_at INTEGER NOT NULL DEFAULT 0,
    cleanup_status TEXT NOT NULL DEFAULT 'active',
    cleanup_error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_temporary_users_cleanup
ON temporary_users(cleanup_status, last_cleanup_attempt_at);

CREATE TABLE IF NOT EXISTS signup_jobs (
    job_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    email_verification_id TEXT,
    email_verified_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signup_jobs_username
ON signup_jobs(username)
WHERE status IN ('pending', 'provisioning');

CREATE UNIQUE INDEX IF NOT EXISTS idx_signup_jobs_email
ON signup_jobs(email)
WHERE status IN ('pending', 'provisioning');

CREATE TABLE IF NOT EXISTS email_verifications (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    purpose TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    resend_email_id TEXT NOT NULL DEFAULT '',
    last_sent_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    verified_at INTEGER,
    consumed_at INTEGER,
    client_ip_hash TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_verifications_email_purpose_status_sent
ON email_verifications(email, purpose, status, last_sent_at);

CREATE INDEX IF NOT EXISTS idx_email_verifications_client_ip_sent
ON email_verifications(client_ip_hash, last_sent_at);
"""

ACTIVE_SIGNUP_JOB_STATUSES = ("pending", "provisioning")
TERMINAL_SIGNUP_JOB_STATUSES = ("completed", "failed")
DEFAULT_SIGNUP_JOB_RETENTION_SECONDS = 3600
EMAIL_VERIFICATION_PURPOSE_SIGNUP = "signup"
EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET = "password_reset"
EMAIL_VERIFICATION_STATUS_PENDING = "pending"
EMAIL_VERIFICATION_STATUS_EXPIRED = "expired"
EMAIL_VERIFICATION_STATUS_CONSUMED = "consumed"
EMAIL_VERIFICATION_STATUS_FAILED = "failed"
DEFAULT_EMAIL_VERIFICATION_MAX_ATTEMPTS = 5
TEMPORARY_USER_STATUS_ACTIVE = "active"
TEMPORARY_USER_STATUS_CLEANING = "cleaning"
TEMPORARY_USER_STATUS_FAILED = "failed"


@dataclass(frozen=True)
class InterfaceUser:
    id: str
    username: str
    email: str
    name: str
    role: str
    mapping_username: str
    active: bool
    auth_session_version: int
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class EmailVerificationSendStats:
    last_email_sent_at: int | None
    email_hourly_count: int
    ip_hourly_count: int


class EmailVerificationError(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _row_to_user(row: sqlite3.Row | None) -> InterfaceUser | None:
    if row is None:
        return None
    return InterfaceUser(
        id=str(row["id"]),
        username=str(row["username"]),
        email=str(row["email"]),
        name=str(row["name"]),
        role=str(row["role"]),
        mapping_username=str(row["mapping_username"]),
        active=bool(int(row["active"] or 0)),
        auth_session_version=int(
            row["auth_session_version"]
            if "auth_session_version" in row.keys()
            else 0
        ),
        created_at=int(row["created_at"] or 0),
        updated_at=int(row["updated_at"] or 0),
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"pragma table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _add_column_if_missing(
    conn: sqlite3.Connection, table_name: str, column_name: str, definition: str
) -> None:
    if column_name in _table_columns(conn, table_name):
        return
    conn.execute(f"alter table {table_name} add column {column_name} {definition}")


def ensure_auth_db(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    ensure_private_directory(db_path.parent, mode=DEFAULT_PRIVATE_WRITABLE_DIR_MODE)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
        _add_column_if_missing(
            conn,
            "users",
            "auth_session_version",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _add_column_if_missing(conn, "signup_jobs", "email_verification_id", "TEXT")
        _add_column_if_missing(conn, "signup_jobs", "email_verified_at", "INTEGER")
        for index_name, column_name in (
            ("idx_signup_jobs_username", "username"),
            ("idx_signup_jobs_email", "email"),
        ):
            row = conn.execute(
                "select sql from sqlite_master where type = 'index' and name = ? limit 1",
                (index_name,),
            ).fetchone()
            existing_sql = str(row[0] or "") if row is not None else ""
            desired_marker = "where status in ('pending', 'provisioning')"
            if desired_marker not in existing_sql.lower():
                conn.execute(f"drop index if exists {index_name}")
                conn.execute(
                    f"create unique index if not exists {index_name} on signup_jobs({column_name}) "
                    "where status in ('pending', 'provisioning')"
                )
        conn.commit()
    ensure_sqlite_sidecar_modes(db_path)
    return db_path


def connect_auth_db(db_path: Path = DEFAULT_AUTH_DB_PATH) -> sqlite3.Connection:
    ensure_auth_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def username_exists(username: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> bool:
    normalized_username = username.strip()
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select 1 from users where username = ? limit 1",
            (normalized_username,),
        ).fetchone()
        pending = conn.execute(
            "select 1 from signup_jobs where username = ? and status in ('pending','provisioning') limit 1",
            (normalized_username,),
        ).fetchone()
    return row is not None or pending is not None


def email_exists(email: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> bool:
    normalized_email = email.strip().lower()
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select 1 from users where lower(email) = lower(?) limit 1",
            (normalized_email,),
        ).fetchone()
        pending = conn.execute(
            "select 1 from signup_jobs where lower(email) = lower(?) and status in ('pending','provisioning') limit 1",
            (normalized_email,),
        ).fetchone()
    return row is not None or pending is not None


def email_verification_send_stats(
    *,
    email: str,
    purpose: str = EMAIL_VERIFICATION_PURPOSE_SIGNUP,
    client_ip_hash: str = "",
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> EmailVerificationSendStats:
    normalized_email = email.strip().lower()
    normalized_purpose = purpose.strip() or EMAIL_VERIFICATION_PURPOSE_SIGNUP
    timestamp = int(time.time()) if now is None else int(now)
    minute_cutoff = timestamp - 60
    hour_cutoff = timestamp - 3600
    with connect_auth_db(db_path) as conn:
        last_row = conn.execute(
            "select max(last_sent_at) as last_sent_at from email_verifications "
            "where lower(email) = lower(?) and purpose = ? and status != ? and last_sent_at >= ?",
            (
                normalized_email,
                normalized_purpose,
                EMAIL_VERIFICATION_STATUS_FAILED,
                minute_cutoff,
            ),
        ).fetchone()
        email_row = conn.execute(
            "select count(*) as count from email_verifications "
            "where lower(email) = lower(?) and purpose = ? and status != ? and last_sent_at >= ?",
            (
                normalized_email,
                normalized_purpose,
                EMAIL_VERIFICATION_STATUS_FAILED,
                hour_cutoff,
            ),
        ).fetchone()
        if client_ip_hash:
            ip_row = conn.execute(
                "select count(*) as count from email_verifications "
                "where client_ip_hash = ? and status != ? and last_sent_at >= ?",
                (
                    client_ip_hash,
                    EMAIL_VERIFICATION_STATUS_FAILED,
                    hour_cutoff,
                ),
            ).fetchone()
        else:
            ip_row = None
    last_sent_at = (
        int(last_row["last_sent_at"])
        if last_row is not None and last_row["last_sent_at"] is not None
        else None
    )
    return EmailVerificationSendStats(
        last_email_sent_at=last_sent_at,
        email_hourly_count=int(email_row["count"] if email_row is not None else 0),
        ip_hourly_count=int(ip_row["count"] if ip_row is not None else 0),
    )


def create_pending_email_verification(
    *,
    email: str,
    code_hash: str,
    purpose: str = EMAIL_VERIFICATION_PURPOSE_SIGNUP,
    client_ip_hash: str = "",
    expires_at: int,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> str:
    normalized_email = email.strip().lower()
    normalized_purpose = purpose.strip() or EMAIL_VERIFICATION_PURPOSE_SIGNUP
    timestamp = int(time.time()) if now is None else int(now)
    verification_id = str(uuid.uuid4())

    with connect_auth_db(db_path) as conn:
        conn.execute(
            "update email_verifications set status = ?, updated_at = ? "
            "where lower(email) = lower(?) and purpose = ? and status = ?",
            (
                EMAIL_VERIFICATION_STATUS_EXPIRED,
                timestamp,
                normalized_email,
                normalized_purpose,
                EMAIL_VERIFICATION_STATUS_PENDING,
            ),
        )
        conn.execute(
            "insert into email_verifications "
            "(id, email, purpose, code_hash, status, attempt_count, resend_email_id, last_sent_at, expires_at, verified_at, consumed_at, client_ip_hash, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, 0, '', ?, ?, null, null, ?, ?, ?)",
            (
                verification_id,
                normalized_email,
                normalized_purpose,
                code_hash,
                EMAIL_VERIFICATION_STATUS_PENDING,
                timestamp,
                int(expires_at),
                client_ip_hash,
                timestamp,
                timestamp,
            ),
        )
        conn.commit()
    return verification_id


def record_email_verification_sent(
    verification_id: str,
    *,
    resend_email_id: str = "",
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "update email_verifications set resend_email_id = ?, updated_at = ? where id = ?",
            (resend_email_id, timestamp, verification_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def mark_email_verification_failed(
    verification_id: str,
    *,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "update email_verifications set status = ?, updated_at = ? where id = ?",
            (EMAIL_VERIFICATION_STATUS_FAILED, timestamp, verification_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def cleanup_terminal_signup_jobs(
    *,
    retention_seconds: int = DEFAULT_SIGNUP_JOB_RETENTION_SECONDS,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> int:
    cutoff = int(time.time()) - max(int(retention_seconds), 0)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "delete from signup_jobs where status in (?, ?) and updated_at <= ?",
            (*TERMINAL_SIGNUP_JOB_STATUSES, cutoff),
        )
        conn.commit()
        return cursor.rowcount


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def get_user_by_login(
    login: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> InterfaceUser | None:
    query = (
        "select id, username, email, name, role, mapping_username, active, auth_session_version, created_at, updated_at, password_hash "
        "from users where lower(email) = lower(?) or username = ? limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (login, login)).fetchone()
    return _row_to_user(row)


def get_user_by_email(
    email: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> InterfaceUser | None:
    normalized_email = email.strip().lower()
    query = (
        "select id, username, email, name, role, mapping_username, active, auth_session_version, created_at, updated_at "
        "from users where lower(email) = lower(?) limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (normalized_email,)).fetchone()
    return _row_to_user(row)


def get_user_with_password_by_login(
    login: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> tuple[InterfaceUser | None, str | None]:
    query = (
        "select id, username, email, name, role, mapping_username, active, auth_session_version, created_at, updated_at, password_hash "
        "from users where lower(email) = lower(?) or username = ? limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (login, login)).fetchone()
    if row is None:
        return None, None
    return _row_to_user(row), str(row["password_hash"])


def get_user_with_password_by_id(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> tuple[InterfaceUser | None, str | None]:
    query = (
        "select id, username, email, name, role, mapping_username, active, auth_session_version, created_at, updated_at, password_hash "
        "from users where id = ? limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (user_id,)).fetchone()
    if row is None:
        return None, None
    return _row_to_user(row), str(row["password_hash"])


def get_user_by_id(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> InterfaceUser | None:
    query = (
        "select id, username, email, name, role, mapping_username, active, auth_session_version, created_at, updated_at "
        "from users where id = ? limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (user_id,)).fetchone()
    return _row_to_user(row)


def list_users(db_path: Path = DEFAULT_AUTH_DB_PATH) -> list[InterfaceUser]:
    query = (
        "select id, username, email, name, role, mapping_username, active, auth_session_version, created_at, updated_at "
        "from users order by username"
    )
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_user(row) for row in rows if row is not None]


def create_temporary_user(
    *,
    username: str,
    email: str,
    password: str,
    mapping_username: str,
    name: str | None = None,
    db_path: Path | None = None,
) -> InterfaceUser:
    resolved_db_path = db_path or DEFAULT_AUTH_DB_PATH
    normalized_email = email.strip().lower()
    normalized_username = username.strip()
    normalized_mapping_username = mapping_username.strip()
    display_name = (name or username).strip() or username
    password_hash = hash_password(password)
    now = int(time.time())
    user_id = str(uuid.uuid4())

    with connect_auth_db(resolved_db_path) as conn:
        conn.execute("begin immediate")
        conn.execute(
            "insert into users (id, username, email, password_hash, name, role, mapping_username, active, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, 'user', ?, 1, ?, ?)",
            (
                user_id,
                normalized_username,
                normalized_email,
                password_hash,
                display_name,
                normalized_mapping_username,
                now,
                now,
            ),
        )
        conn.execute(
            "insert into temporary_users (user_id, mapping_username, created_at, last_cleanup_attempt_at, cleanup_status, cleanup_error) "
            "values (?, ?, ?, 0, ?, '')",
            (
                user_id,
                normalized_mapping_username,
                now,
                TEMPORARY_USER_STATUS_ACTIVE,
            ),
        )
        conn.commit()

    user = get_user_by_id(user_id, resolved_db_path)
    if user is None:
        raise RuntimeError("Failed to create temporary user")
    return user


def get_temporary_user(
    user_id: str, db_path: Path | None = None
) -> dict[str, Any] | None:
    resolved_db_path = db_path or DEFAULT_AUTH_DB_PATH
    normalized_user_id = user_id.strip()
    with connect_auth_db(resolved_db_path) as conn:
        row = conn.execute(
            """
            select user_id, mapping_username, created_at,
                   last_cleanup_attempt_at, cleanup_status, cleanup_error
            from temporary_users
            where user_id = ?
            limit 1
            """,
            (normalized_user_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def is_temporary_user(
    user_id: str, db_path: Path | None = None
) -> bool:
    return get_temporary_user(user_id, db_path=db_path) is not None


def mark_temporary_user_cleanup_attempt(
    user_id: str,
    *,
    status: str,
    error_message: str = "",
    now: int | None = None,
    db_path: Path | None = None,
) -> bool:
    resolved_db_path = db_path or DEFAULT_AUTH_DB_PATH
    normalized_user_id = user_id.strip()
    timestamp = int(time.time()) if now is None else int(now)
    normalized_status = status.strip() or TEMPORARY_USER_STATUS_FAILED
    with connect_auth_db(resolved_db_path) as conn:
        cursor = conn.execute(
            """
            update temporary_users
            set cleanup_status = ?,
                cleanup_error = ?,
                last_cleanup_attempt_at = ?
            where user_id = ?
            """,
            (
                normalized_status,
                str(error_message or "")[:2000],
                timestamp,
                normalized_user_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_temporary_user_record(
    user_id: str, db_path: Path | None = None
) -> bool:
    resolved_db_path = db_path or DEFAULT_AUTH_DB_PATH
    normalized_user_id = user_id.strip()
    with connect_auth_db(resolved_db_path) as conn:
        cursor = conn.execute(
            "delete from temporary_users where user_id = ?",
            (normalized_user_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def upsert_user(
    *,
    username: str,
    email: str,
    password: str,
    mapping_username: str,
    name: str | None = None,
    role: str = "user",
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> InterfaceUser:
    normalized_email = email.strip().lower()
    normalized_username = username.strip()
    display_name = (name or username).strip() or username
    password_hash = hash_password(password)
    now = int(time.time())

    query_existing = (
        "select id from users where lower(email) = lower(?) or username = ? limit 1"
    )

    with connect_auth_db(db_path) as conn:
        existing = conn.execute(
            query_existing, (normalized_email, normalized_username)
        ).fetchone()
        if existing is None:
            user_id = str(uuid.uuid4())
            conn.execute(
                "insert into users (id, username, email, password_hash, name, role, mapping_username, active, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    user_id,
                    normalized_username,
                    normalized_email,
                    password_hash,
                    display_name,
                    role,
                    mapping_username,
                    now,
                    now,
                ),
            )
        else:
            user_id = str(existing["id"])
            conn.execute(
                "update users set username = ?, email = ?, password_hash = ?, name = ?, role = ?, mapping_username = ?, active = 1, updated_at = ? "
                "where id = ?",
                (
                    normalized_username,
                    normalized_email,
                    password_hash,
                    display_name,
                    role,
                    mapping_username,
                    now,
                    user_id,
                ),
            )
        conn.commit()

    user = get_user_by_id(user_id, db_path)
    if user is None:
        raise RuntimeError("Failed to load interface user after upsert")
    return user


def update_user_password(
    user_id: str,
    new_password: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> InterfaceUser | None:
    normalized_user_id = user_id.strip()
    password_hash = hash_password(new_password)
    now = int(time.time())
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "update users set password_hash = ?, auth_session_version = coalesce(auth_session_version, 0) + 1, updated_at = ? "
            "where id = ?",
            (password_hash, now, normalized_user_id),
        )
        conn.commit()
        if cursor.rowcount <= 0:
            return None
    return get_user_by_id(normalized_user_id, db_path)


def _consume_email_verification_in_conn(
    conn: sqlite3.Connection,
    *,
    email: str,
    email_verification_id: str,
    email_verification_code_hash: str,
    purpose: str,
    max_attempts: int,
    timestamp: int,
) -> int:
    normalized_email = email.strip().lower()
    normalized_purpose = purpose.strip() or EMAIL_VERIFICATION_PURPOSE_SIGNUP
    verification_id = email_verification_id.strip()
    row = conn.execute(
        "select id, email, purpose, code_hash, status, attempt_count, expires_at "
        "from email_verifications where id = ? limit 1",
        (verification_id,),
    ).fetchone()
    if row is None:
        raise EmailVerificationError(
            "not_found", "Invalid or expired verification code."
        )

    status = str(row["status"])
    if status == EMAIL_VERIFICATION_STATUS_CONSUMED:
        raise EmailVerificationError(
            "consumed", "This verification code has already been used."
        )
    if status != EMAIL_VERIFICATION_STATUS_PENDING:
        raise EmailVerificationError(status, "Invalid or expired verification code.")
    if (
        str(row["purpose"]) != normalized_purpose
        or str(row["email"]).lower() != normalized_email
    ):
        raise EmailVerificationError(
            "email_mismatch", "Verification code does not match this email."
        )

    expires_at = int(row["expires_at"] or 0)
    if expires_at < timestamp:
        conn.execute(
            "update email_verifications set status = ?, updated_at = ? where id = ?",
            (EMAIL_VERIFICATION_STATUS_EXPIRED, timestamp, verification_id),
        )
        conn.commit()
        raise EmailVerificationError(
            "expired", "Verification code has expired. Send a new code."
        )

    attempt_count = int(row["attempt_count"] or 0)
    if attempt_count >= max_attempts:
        conn.execute(
            "update email_verifications set status = ?, updated_at = ? where id = ?",
            (EMAIL_VERIFICATION_STATUS_FAILED, timestamp, verification_id),
        )
        conn.commit()
        raise EmailVerificationError(
            "too_many_attempts",
            "Too many incorrect verification attempts. Send a new code.",
        )

    if not hmac.compare_digest(
        str(row["code_hash"]), email_verification_code_hash.strip()
    ):
        attempt_count += 1
        new_status = (
            EMAIL_VERIFICATION_STATUS_FAILED
            if attempt_count >= max_attempts
            else EMAIL_VERIFICATION_STATUS_PENDING
        )
        conn.execute(
            "update email_verifications set attempt_count = ?, status = ?, updated_at = ? where id = ?",
            (attempt_count, new_status, timestamp, verification_id),
        )
        conn.commit()
        if new_status == EMAIL_VERIFICATION_STATUS_FAILED:
            raise EmailVerificationError(
                "too_many_attempts",
                "Too many incorrect verification attempts. Send a new code.",
            )
        raise EmailVerificationError("invalid_code", "Invalid verification code.")

    conn.execute(
        "update email_verifications set status = ?, verified_at = ?, consumed_at = ?, updated_at = ? where id = ?",
        (
            EMAIL_VERIFICATION_STATUS_CONSUMED,
            timestamp,
            timestamp,
            timestamp,
            verification_id,
        ),
    )
    return timestamp


def reset_user_password_with_email_verification(
    *,
    email: str,
    new_password: str,
    email_verification_id: str,
    email_verification_code_hash: str,
    purpose: str = EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET,
    max_attempts: int = DEFAULT_EMAIL_VERIFICATION_MAX_ATTEMPTS,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> InterfaceUser:
    normalized_email = email.strip().lower()
    normalized_purpose = purpose.strip() or EMAIL_VERIFICATION_PURPOSE_PASSWORD_RESET
    timestamp = int(time.time()) if now is None else int(now)

    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
        row = conn.execute(
            "select id from users where lower(email) = lower(?) and active = 1 limit 1",
            (normalized_email,),
        ).fetchone()
        if row is None:
            raise EmailVerificationError(
                "not_found", "Invalid or expired verification code."
            )

        user_id = str(row["id"])
        _consume_email_verification_in_conn(
            conn,
            email=normalized_email,
            email_verification_id=email_verification_id,
            email_verification_code_hash=email_verification_code_hash,
            purpose=normalized_purpose,
            max_attempts=max_attempts,
            timestamp=timestamp,
        )
        password_hash = hash_password(new_password)
        conn.execute(
            "update users set password_hash = ?, auth_session_version = coalesce(auth_session_version, 0) + 1, updated_at = ? "
            "where id = ?",
            (password_hash, timestamp, user_id),
        )
        conn.commit()

    user = get_user_by_id(user_id, db_path)
    if user is None:
        raise RuntimeError("Failed to load interface user after password reset")
    return user


def create_signup_job(
    *,
    username: str,
    email: str,
    password: str,
    display_name: str,
    email_verification_id: str | None = None,
    email_verified_at: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> str:
    normalized_username = username.strip()
    normalized_email = email.strip().lower()
    now = int(time.time())
    job_id = str(uuid.uuid4())
    password_hash = hash_password(password)

    with connect_auth_db(db_path) as conn:
        conn.execute(
            "insert into signup_jobs "
            "(job_id, username, email, password_hash, display_name, status, error_message, email_verification_id, email_verified_at, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, 'pending', '', ?, ?, ?, ?)",
            (
                job_id,
                normalized_username,
                normalized_email,
                password_hash,
                display_name.strip() or normalized_username,
                email_verification_id,
                email_verified_at,
                now,
                now,
            ),
        )
        conn.commit()
    return job_id


def create_signup_job_with_email_verification(
    *,
    username: str,
    email: str,
    password: str,
    display_name: str,
    email_verification_id: str,
    email_verification_code_hash: str,
    purpose: str = EMAIL_VERIFICATION_PURPOSE_SIGNUP,
    max_attempts: int = DEFAULT_EMAIL_VERIFICATION_MAX_ATTEMPTS,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> str:
    normalized_username = username.strip()
    normalized_email = email.strip().lower()
    normalized_purpose = purpose.strip() or EMAIL_VERIFICATION_PURPOSE_SIGNUP
    timestamp = int(time.time()) if now is None else int(now)
    job_id = str(uuid.uuid4())

    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
        _consume_email_verification_in_conn(
            conn,
            email=normalized_email,
            email_verification_id=email_verification_id,
            email_verification_code_hash=email_verification_code_hash,
            purpose=normalized_purpose,
            max_attempts=max_attempts,
            timestamp=timestamp,
        )
        password_hash = hash_password(password)
        conn.execute(
            "insert into signup_jobs "
            "(job_id, username, email, password_hash, display_name, status, error_message, email_verification_id, email_verified_at, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, 'pending', '', ?, ?, ?, ?)",
            (
                job_id,
                normalized_username,
                normalized_email,
                password_hash,
                display_name.strip() or normalized_username,
                email_verification_id.strip(),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        conn.commit()
    return job_id


def get_signup_job(job_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> dict | None:
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select job_id, username, email, display_name, status, error_message, email_verification_id, email_verified_at, created_at, updated_at from signup_jobs where job_id = ? limit 1",
            (job_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def get_next_pending_signup_job(db_path: Path = DEFAULT_AUTH_DB_PATH) -> dict | None:
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select job_id, username, email, password_hash, display_name, status, error_message, email_verification_id, email_verified_at, created_at, updated_at from signup_jobs where status = 'pending' order by created_at asc limit 1"
        ).fetchone()
    return dict(row) if row is not None else None


def set_signup_job_status(
    job_id: str,
    *,
    status: str,
    error_message: str = "",
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> None:
    now = int(time.time())
    with connect_auth_db(db_path) as conn:
        conn.execute(
            "update signup_jobs set status = ?, error_message = ?, updated_at = ? where job_id = ?",
            (status, error_message[:2000], now, job_id),
        )
        conn.commit()


def activate_signup_user(
    job_id: str,
    *,
    mapping_username: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> InterfaceUser:
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select username, email, password_hash, display_name from signup_jobs where job_id = ? limit 1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Signup job not found")

    now = int(time.time())
    user_id = str(uuid.uuid4())
    with connect_auth_db(db_path) as conn:
        conn.execute(
            "insert into users (id, username, email, password_hash, name, role, mapping_username, active, created_at, updated_at) values (?, ?, ?, ?, ?, 'user', ?, 1, ?, ?)",
            (
                user_id,
                str(row["username"]),
                str(row["email"]),
                str(row["password_hash"]),
                str(row["display_name"]),
                mapping_username,
                now,
                now,
            ),
        )
        conn.commit()

    user = get_user_by_id(user_id, db_path)
    if user is None:
        raise RuntimeError("Failed to create signup user")
    return user


def delete_signup_job(job_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> bool:
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "delete from signup_jobs where job_id = ?",
            (job_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_user_by_mapping_username(
    mapping_username: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    normalized_mapping_username = mapping_username.strip()
    with connect_auth_db(db_path) as conn:
        conn.execute(
            """
            delete from temporary_users
            where mapping_username = ?
               or user_id in (
                    select id from users
                    where mapping_username = ? or username = ?
               )
            """,
            (
                normalized_mapping_username,
                normalized_mapping_username,
                normalized_mapping_username,
            ),
        )
        cursor = conn.execute(
            "delete from users where mapping_username = ? or username = ?",
            (normalized_mapping_username, normalized_mapping_username),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_user_by_id(user_id: str, db_path: Path | None = None) -> bool:
    resolved_db_path = db_path or DEFAULT_AUTH_DB_PATH
    normalized_user_id = user_id.strip()
    with connect_auth_db(resolved_db_path) as conn:
        conn.execute(
            "delete from temporary_users where user_id = ?",
            (normalized_user_id,),
        )
        cursor = conn.execute(
            "delete from users where id = ?",
            (normalized_user_id,),
        )
        conn.commit()
        return cursor.rowcount > 0

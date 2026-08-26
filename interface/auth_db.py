from __future__ import annotations

import hmac
import os
import sqlite3
import threading
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
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
_AUTH_DB_INIT_LOCK = threading.RLock()
_AUTH_DB_IDENTITIES: dict[str, tuple[int, int]] = {}

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

CREATE TABLE IF NOT EXISTS user_usage_identities (
    mapping_username TEXT PRIMARY KEY,
    user_id TEXT UNIQUE,
    account_type TEXT NOT NULL CHECK (
        account_type IN ('formal', 'temporary', 'unknown', 'service')
    ),
    created_at INTEGER NOT NULL,
    retired_at INTEGER,
    source TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_usage_identities_user
ON user_usage_identities(user_id);

CREATE INDEX IF NOT EXISTS idx_user_usage_identities_type_retired
ON user_usage_identities(account_type, retired_at);

CREATE TABLE IF NOT EXISTS user_storage_snapshots (
    user_id TEXT NOT NULL,
    snapshot_day TEXT NOT NULL,
    sampled_at INTEGER NOT NULL,
    allocated_bytes INTEGER,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    error_code TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(user_id, snapshot_day)
);

CREATE INDEX IF NOT EXISTS idx_user_storage_snapshots_latest
ON user_storage_snapshots(user_id, sampled_at DESC);

CREATE TABLE IF NOT EXISTS admin_signin_limits (
    key_hash TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('login_ip', 'ip')),
    window_started_at INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_attempt_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_signin_limits_last_attempt
ON admin_signin_limits(last_attempt_at);

CREATE TABLE IF NOT EXISTS admin_migrations (
    migration_key TEXT PRIMARY KEY,
    completed_at INTEGER NOT NULL
);

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
    agreement_version TEXT NOT NULL DEFAULT '',
    agreement_document_sha256 TEXT NOT NULL DEFAULT '',
    agreement_accepted_at INTEGER,
    agreement_source TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS agreement_acceptances (
    user_id TEXT NOT NULL,
    agreement_version TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    accepted_at INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('signup', 'signin', 'temporary')),
    user_deleted_at INTEGER,
    retain_until INTEGER,
    PRIMARY KEY(user_id, agreement_version)
);

CREATE INDEX IF NOT EXISTS idx_agreement_acceptances_retention
ON agreement_acceptances(user_deleted_at, retain_until);
"""

ACTIVE_SIGNUP_JOB_STATUSES = ("pending", "provisioning")
TERMINAL_SIGNUP_JOB_STATUSES = ("completed", "failed")
DEFAULT_SIGNUP_JOB_RETENTION_SECONDS = 3600
DEFAULT_AGREEMENT_AUDIT_RETENTION_SECONDS = 3 * 365 * 24 * 60 * 60
AGREEMENT_ACCEPTANCE_SOURCES = frozenset({"signup", "signin", "temporary"})
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


class MappingUsernameConflictError(RuntimeError):
    pass


class PrincipalReuseError(RuntimeError):
    pass


class RoleManagementError(RuntimeError):
    pass


class LastAdministratorError(RoleManagementError):
    pass


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


def _ensure_unique_mapping_username_index(conn: sqlite3.Connection) -> None:
    duplicates = conn.execute(
        "select mapping_username, count(*) as user_count "
        "from users group by mapping_username having count(*) > 1 "
        "order by mapping_username"
    ).fetchall()
    if duplicates:
        summary = ", ".join(
            f"{str(row[0])!r} ({int(row[1])} users)" for row in duplicates
        )
        raise MappingUsernameConflictError(
            "Auth DB contains duplicate mapping_username assignments. Resolve them "
            f"before starting the interface: {summary}"
        )

    existing = conn.execute(
        "select sql from sqlite_master where type = 'index' "
        "and name = 'idx_interface_users_mapping_username' limit 1"
    ).fetchone()
    existing_sql = str(existing[0] or "") if existing is not None else ""
    if "create unique index" in existing_sql.lower():
        return
    conn.execute("drop index if exists idx_interface_users_mapping_username")
    conn.execute(
        "create unique index idx_interface_users_mapping_username "
        "on users(mapping_username)"
    )


def _claim_usage_identity(
    conn: sqlite3.Connection,
    *,
    mapping_username: str,
    user_id: str | None,
    account_type: str,
    created_at: int,
    source: str,
) -> None:
    normalized_mapping_username = mapping_username.strip()
    if not normalized_mapping_username:
        raise ValueError("mapping_username is required")
    existing = conn.execute(
        "select user_id, account_type, retired_at from user_usage_identities "
        "where mapping_username = ? limit 1",
        (normalized_mapping_username,),
    ).fetchone()
    if existing is None:
        try:
            conn.execute(
                "insert into user_usage_identities "
                "(mapping_username, user_id, account_type, created_at, retired_at, source) "
                "values (?, ?, ?, ?, null, ?)",
                (
                    normalized_mapping_username,
                    user_id,
                    account_type,
                    int(created_at),
                    source.strip() or "unknown",
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PrincipalReuseError(
                f"Usage principal is already assigned: {normalized_mapping_username}"
            ) from exc
        return

    existing_user_id = str(existing[0] or "") or None
    if (
        existing_user_id != user_id
        or existing[2] is not None
        or str(existing[1]) != account_type
    ):
        raise PrincipalReuseError(
            f"Usage principal cannot be reused: {normalized_mapping_username}"
        )


def _backfill_current_usage_identities(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        select u.id, u.mapping_username, u.created_at,
               case when t.user_id is null then 'formal' else 'temporary' end as account_type
        from users u
        left join temporary_users t on t.user_id = u.id
        order by u.created_at, u.id
        """
    ).fetchall()
    for row in rows:
        _claim_usage_identity(
            conn,
            mapping_username=str(row[1]),
            user_id=str(row[0]),
            account_type=str(row[3]),
            created_at=int(row[2] or 0),
            source="current_user_backfill_v1",
        )


def _assert_admin_deletion_allowed(
    conn: sqlite3.Connection, user_ids: list[str]
) -> None:
    if not user_ids:
        return
    placeholders = ",".join("?" for _ in user_ids)
    target_admins = int(
        conn.execute(
            f"select count(*) from users where active = 1 and role = 'admin' "
            f"and id in ({placeholders})",
            tuple(user_ids),
        ).fetchone()[0]
        or 0
    )
    if target_admins <= 0:
        return
    active_admins = int(
        conn.execute(
            "select count(*) from users where active = 1 and role = 'admin'"
        ).fetchone()[0]
        or 0
    )
    if active_admins - target_admins <= 0:
        raise LastAdministratorError("Refusing to delete the last administrator")


def _database_identity(db_path: Path) -> tuple[int, int] | None:
    try:
        stat_result = db_path.stat()
    except OSError:
        return None
    return (int(stat_result.st_dev), int(stat_result.st_ino))


def _database_cache_key(db_path: Path) -> str:
    return str(db_path.expanduser().absolute())


def _auth_db_is_initialized(db_path: Path) -> bool:
    identity = _database_identity(db_path)
    if identity is None:
        return False
    return _AUTH_DB_IDENTITIES.get(_database_cache_key(db_path)) == identity


def ensure_auth_db(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    with _AUTH_DB_INIT_LOCK:
        ensure_private_directory(db_path.parent, mode=DEFAULT_PRIVATE_WRITABLE_DIR_MODE)
        with sqlite3.connect(
            str(db_path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS
        ) as conn:
            conn.execute(
                f"PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}"
            )
            conn.executescript(SCHEMA_SQL)
            _add_column_if_missing(
                conn,
                "users",
                "auth_session_version",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _add_column_if_missing(conn, "signup_jobs", "email_verification_id", "TEXT")
            _add_column_if_missing(conn, "signup_jobs", "email_verified_at", "INTEGER")
            _add_column_if_missing(
                conn,
                "signup_jobs",
                "agreement_version",
                "TEXT NOT NULL DEFAULT ''",
            )
            _add_column_if_missing(
                conn,
                "signup_jobs",
                "agreement_document_sha256",
                "TEXT NOT NULL DEFAULT ''",
            )
            _add_column_if_missing(
                conn, "signup_jobs", "agreement_accepted_at", "INTEGER"
            )
            _add_column_if_missing(
                conn,
                "signup_jobs",
                "agreement_source",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_unique_mapping_username_index(conn)
            _backfill_current_usage_identities(conn)
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
        identity = _database_identity(db_path)
        if identity is not None:
            _AUTH_DB_IDENTITIES[_database_cache_key(db_path)] = identity
    return db_path


def connect_auth_db(db_path: Path = DEFAULT_AUTH_DB_PATH) -> sqlite3.Connection:
    if not _auth_db_is_initialized(db_path):
        with _AUTH_DB_INIT_LOCK:
            if not _auth_db_is_initialized(db_path):
                ensure_auth_db(db_path)
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
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


def _record_agreement_acceptance_in_conn(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    agreement_version: str,
    document_sha256: str,
    accepted_at: int,
    source: str,
) -> bool:
    normalized_user_id = user_id.strip()
    normalized_version = agreement_version.strip()
    normalized_sha256 = document_sha256.strip().lower()
    normalized_source = source.strip().lower()
    if not normalized_user_id or not normalized_version or len(normalized_sha256) != 64:
        raise ValueError("Complete agreement acceptance evidence is required")
    if normalized_source not in AGREEMENT_ACCEPTANCE_SOURCES:
        raise ValueError("Invalid agreement acceptance source")
    cursor = conn.execute(
        "insert or ignore into agreement_acceptances "
        "(user_id, agreement_version, document_sha256, accepted_at, source, user_deleted_at, retain_until) "
        "values (?, ?, ?, ?, ?, null, null)",
        (
            normalized_user_id,
            normalized_version,
            normalized_sha256,
            int(accepted_at),
            normalized_source,
        ),
    )
    if cursor.rowcount > 0:
        return True
    existing = conn.execute(
        "select document_sha256 from agreement_acceptances "
        "where user_id = ? and agreement_version = ? limit 1",
        (normalized_user_id, normalized_version),
    ).fetchone()
    if existing is None or str(existing[0]).lower() != normalized_sha256:
        raise ValueError("Agreement version and document digest do not match")
    return False


def record_agreement_acceptance(
    *,
    user_id: str,
    agreement_version: str,
    document_sha256: str,
    source: str,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    with connect_auth_db(db_path) as conn:
        inserted = _record_agreement_acceptance_in_conn(
            conn,
            user_id=user_id,
            agreement_version=agreement_version,
            document_sha256=document_sha256,
            accepted_at=timestamp,
            source=source,
        )
        conn.commit()
    return inserted


def has_agreement_acceptance(
    user_id: str,
    agreement_version: str,
    *,
    document_sha256: str | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> bool:
    normalized_user_id = user_id.strip()
    normalized_version = agreement_version.strip()
    params: list[Any] = [normalized_user_id, normalized_version]
    query = (
        "select 1 from agreement_acceptances "
        "where user_id = ? and agreement_version = ?"
    )
    if document_sha256 is not None:
        query += " and document_sha256 = ?"
        params.append(document_sha256.strip().lower())
    query += " limit 1"
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, tuple(params)).fetchone()
    return row is not None


def get_agreement_acceptance(
    user_id: str,
    agreement_version: str,
    *,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, Any] | None:
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select user_id, agreement_version, document_sha256, accepted_at, source, user_deleted_at, retain_until "
            "from agreement_acceptances where user_id = ? and agreement_version = ? limit 1",
            (user_id.strip(), agreement_version.strip()),
        ).fetchone()
    return dict(row) if row is not None else None


def _mark_agreement_acceptances_deleted_in_conn(
    conn: sqlite3.Connection,
    user_ids: list[str],
    *,
    deleted_at: int,
    retention_seconds: int = DEFAULT_AGREEMENT_AUDIT_RETENTION_SECONDS,
) -> None:
    if not user_ids:
        return
    placeholders = ",".join("?" for _ in user_ids)
    retain_until = int(deleted_at) + max(int(retention_seconds), 0)
    conn.execute(
        f"update agreement_acceptances set user_deleted_at = coalesce(user_deleted_at, ?), "
        f"retain_until = coalesce(retain_until, ?) where user_id in ({placeholders})",
        (int(deleted_at), retain_until, *user_ids),
    )


def cleanup_expired_agreement_acceptances(
    *,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> int:
    timestamp = int(time.time()) if now is None else int(now)
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "delete from agreement_acceptances "
            "where user_deleted_at is not null and retain_until is not null and retain_until <= ?",
            (timestamp,),
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
    agreement_version: str = "",
    agreement_document_sha256: str = "",
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
        _claim_usage_identity(
            conn,
            mapping_username=normalized_mapping_username,
            user_id=user_id,
            account_type="temporary",
            created_at=now,
            source="temporary_user",
        )
        if agreement_version or agreement_document_sha256:
            _record_agreement_acceptance_in_conn(
                conn,
                user_id=user_id,
                agreement_version=agreement_version,
                document_sha256=agreement_document_sha256,
                accepted_at=now,
                source="temporary",
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


def retire_temporary_user_identity(
    user_id: str,
    *,
    now: int | None = None,
    db_path: Path | None = None,
) -> bool:
    resolved_db_path = db_path or DEFAULT_AUTH_DB_PATH
    normalized_user_id = user_id.strip()
    timestamp = int(time.time()) if now is None else int(now)
    with connect_auth_db(resolved_db_path) as conn:
        conn.execute("begin immediate")
        temporary = conn.execute(
            "select mapping_username from temporary_users where user_id = ? limit 1",
            (normalized_user_id,),
        ).fetchone()
        if temporary is None:
            conn.rollback()
            return False
        cursor = conn.execute(
            "update user_usage_identities set retired_at = coalesce(retired_at, ?) "
            "where user_id = ? and account_type = 'temporary'",
            (timestamp, normalized_user_id),
        )
        conn.execute(
            "delete from user_storage_snapshots where user_id = ?",
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
    normalized_mapping_username = mapping_username.strip()
    normalized_role = role.strip().lower()
    if normalized_role not in {"admin", "user"}:
        raise ValueError("role must be 'admin' or 'user'")

    query_existing = (
        "select id from users where lower(email) = lower(?) or username = ? limit 1"
    )

    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
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
                    normalized_role,
                    normalized_mapping_username,
                    now,
                    now,
                ),
            )
        else:
            user_id = str(existing["id"])
            conn.execute(
                "update users set username = ?, email = ?, password_hash = ?, name = ?, mapping_username = ?, active = 1, updated_at = ? "
                "where id = ?",
                (
                    normalized_username,
                    normalized_email,
                    password_hash,
                    display_name,
                    normalized_mapping_username,
                    now,
                    user_id,
                ),
            )
        _claim_usage_identity(
            conn,
            mapping_username=normalized_mapping_username,
            user_id=user_id,
            account_type="formal",
            created_at=now,
            source="managed_user",
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


def set_user_role(
    login: str,
    role: str,
    *,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> InterfaceUser:
    normalized_login = login.strip()
    normalized_role = role.strip().lower()
    if normalized_role not in {"admin", "user"}:
        raise RoleManagementError("Role must be 'admin' or 'user'")
    now = int(time.time())
    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
        row = conn.execute(
            "select id, role from users where lower(email) = lower(?) or username = ? limit 1",
            (normalized_login, normalized_login),
        ).fetchone()
        if row is None:
            raise RoleManagementError(f"User not found: {normalized_login}")
        user_id = str(row["id"])
        current_role = str(row["role"])
        if normalized_role == "admin":
            temporary = conn.execute(
                "select 1 from temporary_users where user_id = ? limit 1",
                (user_id,),
            ).fetchone()
            if temporary is not None:
                raise RoleManagementError("Temporary users cannot be administrators")
        if current_role == normalized_role:
            conn.commit()
        else:
            if current_role == "admin" and normalized_role == "user":
                active_admins = int(
                    conn.execute(
                        "select count(*) from users where active = 1 and role = 'admin'"
                    ).fetchone()[0]
                    or 0
                )
                if active_admins <= 1:
                    raise LastAdministratorError(
                        "Refusing to demote the last administrator"
                    )
            conn.execute(
                "update users set role = ?, "
                "auth_session_version = coalesce(auth_session_version, 0) + 1, "
                "updated_at = ? where id = ?",
                (normalized_role, now, user_id),
            )
            conn.commit()
    user = get_user_by_id(user_id, db_path)
    if user is None:
        raise RoleManagementError("User disappeared during role update")
    return user


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
    agreement_version: str = "",
    agreement_document_sha256: str = "",
    agreement_accepted_at: int | None = None,
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
            "(job_id, username, email, password_hash, display_name, status, error_message, email_verification_id, email_verified_at, agreement_version, agreement_document_sha256, agreement_accepted_at, agreement_source, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, 'pending', '', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                normalized_username,
                normalized_email,
                password_hash,
                display_name.strip() or normalized_username,
                email_verification_id,
                email_verified_at,
                agreement_version.strip(),
                agreement_document_sha256.strip().lower(),
                (
                    int(agreement_accepted_at)
                    if agreement_accepted_at is not None
                    else (now if agreement_version and agreement_document_sha256 else None)
                ),
                "signup" if agreement_version and agreement_document_sha256 else "",
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
    agreement_version: str = "",
    agreement_document_sha256: str = "",
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
            "(job_id, username, email, password_hash, display_name, status, error_message, email_verification_id, email_verified_at, agreement_version, agreement_document_sha256, agreement_accepted_at, agreement_source, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, 'pending', '', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                normalized_username,
                normalized_email,
                password_hash,
                display_name.strip() or normalized_username,
                email_verification_id.strip(),
                timestamp,
                agreement_version.strip(),
                agreement_document_sha256.strip().lower(),
                timestamp if agreement_version and agreement_document_sha256 else None,
                "signup" if agreement_version and agreement_document_sha256 else "",
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
            "select job_id, username, email, password_hash, display_name, status, error_message, email_verification_id, email_verified_at, agreement_version, agreement_document_sha256, agreement_accepted_at, agreement_source, created_at, updated_at from signup_jobs where status = 'pending' order by created_at asc limit 1"
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
    now = int(time.time())
    user_id = str(uuid.uuid4())
    normalized_mapping_username = mapping_username.strip()
    with connect_auth_db(db_path) as conn:
        conn.execute("begin immediate")
        row = conn.execute(
            "select username, email, password_hash, display_name, agreement_version, agreement_document_sha256, agreement_accepted_at, agreement_source from signup_jobs where job_id = ? limit 1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Signup job not found")
        conn.execute(
            "insert into users (id, username, email, password_hash, name, role, mapping_username, active, created_at, updated_at) values (?, ?, ?, ?, ?, 'user', ?, 1, ?, ?)",
            (
                user_id,
                str(row["username"]),
                str(row["email"]),
                str(row["password_hash"]),
                str(row["display_name"]),
                normalized_mapping_username,
                now,
                now,
            ),
        )
        _claim_usage_identity(
            conn,
            mapping_username=normalized_mapping_username,
            user_id=user_id,
            account_type="formal",
            created_at=now,
            source="signup",
        )
        if str(row["agreement_version"] or ""):
            _record_agreement_acceptance_in_conn(
                conn,
                user_id=user_id,
                agreement_version=str(row["agreement_version"]),
                document_sha256=str(row["agreement_document_sha256"]),
                accepted_at=int(row["agreement_accepted_at"] or now),
                source=str(row["agreement_source"] or "signup"),
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
        conn.execute("begin immediate")
        users = conn.execute(
            "select id from users where mapping_username = ? or username = ?",
            (normalized_mapping_username, normalized_mapping_username),
        ).fetchall()
        user_ids = [str(row["id"]) for row in users]
        _assert_admin_deletion_allowed(conn, user_ids)
        now = int(time.time())
        _mark_agreement_acceptances_deleted_in_conn(
            conn, user_ids, deleted_at=now
        )
        for target_user_id in user_ids:
            conn.execute(
                "update user_usage_identities set retired_at = coalesce(retired_at, ?) "
                "where user_id = ?",
                (now, target_user_id),
            )
            temporary = conn.execute(
                "select 1 from temporary_users where user_id = ? limit 1",
                (target_user_id,),
            ).fetchone()
            if temporary is not None:
                conn.execute(
                    "delete from user_storage_snapshots where user_id = ?",
                    (target_user_id,),
                )
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
        conn.execute("begin immediate")
        row = conn.execute(
            "select id from users where id = ? limit 1", (normalized_user_id,)
        ).fetchone()
        _assert_admin_deletion_allowed(
            conn, [normalized_user_id] if row is not None else []
        )
        temporary = conn.execute(
            "select 1 from temporary_users where user_id = ? limit 1",
            (normalized_user_id,),
        ).fetchone()
        deleted_at = int(time.time())
        _mark_agreement_acceptances_deleted_in_conn(
            conn,
            [normalized_user_id] if row is not None else [],
            deleted_at=deleted_at,
        )
        conn.execute(
            "update user_usage_identities set retired_at = coalesce(retired_at, ?) "
            "where user_id = ?",
            (deleted_at, normalized_user_id),
        )
        if temporary is not None:
            conn.execute(
                "delete from user_storage_snapshots where user_id = ?",
                (normalized_user_id,),
            )
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

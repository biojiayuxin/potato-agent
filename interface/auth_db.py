from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

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
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interface_users_mapping_username
ON users(mapping_username);

CREATE TABLE IF NOT EXISTS signup_jobs (
    job_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signup_jobs_username
ON signup_jobs(username)
WHERE status IN ('pending', 'provisioning');

CREATE UNIQUE INDEX IF NOT EXISTS idx_signup_jobs_email
ON signup_jobs(email)
WHERE status IN ('pending', 'provisioning');
"""

ACTIVE_SIGNUP_JOB_STATUSES = ("pending", "provisioning")
TERMINAL_SIGNUP_JOB_STATUSES = ("completed", "failed")
DEFAULT_SIGNUP_JOB_RETENTION_SECONDS = 3600


@dataclass(frozen=True)
class InterfaceUser:
    id: str
    username: str
    email: str
    name: str
    role: str
    mapping_username: str
    active: bool
    created_at: int
    updated_at: int


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
        created_at=int(row["created_at"] or 0),
        updated_at=int(row["updated_at"] or 0),
    )


def ensure_auth_db(db_path: Path = DEFAULT_AUTH_DB_PATH) -> Path:
    ensure_private_directory(db_path.parent, mode=DEFAULT_PRIVATE_WRITABLE_DIR_MODE)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
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
        "select id, username, email, name, role, mapping_username, active, created_at, updated_at, password_hash "
        "from users where lower(email) = lower(?) or username = ? limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (login, login)).fetchone()
    return _row_to_user(row)


def get_user_with_password_by_login(
    login: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> tuple[InterfaceUser | None, str | None]:
    query = (
        "select id, username, email, name, role, mapping_username, active, created_at, updated_at, password_hash "
        "from users where lower(email) = lower(?) or username = ? limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (login, login)).fetchone()
    if row is None:
        return None, None
    return _row_to_user(row), str(row["password_hash"])


def get_user_by_id(
    user_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> InterfaceUser | None:
    query = (
        "select id, username, email, name, role, mapping_username, active, created_at, updated_at "
        "from users where id = ? limit 1"
    )
    with connect_auth_db(db_path) as conn:
        row = conn.execute(query, (user_id,)).fetchone()
    return _row_to_user(row)


def list_users(db_path: Path = DEFAULT_AUTH_DB_PATH) -> list[InterfaceUser]:
    query = (
        "select id, username, email, name, role, mapping_username, active, created_at, updated_at "
        "from users order by username"
    )
    with connect_auth_db(db_path) as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_user(row) for row in rows if row is not None]


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


def create_signup_job(
    *,
    username: str,
    email: str,
    password: str,
    display_name: str,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> str:
    normalized_username = username.strip()
    normalized_email = email.strip().lower()
    now = int(time.time())
    job_id = str(uuid.uuid4())
    password_hash = hash_password(password)

    with connect_auth_db(db_path) as conn:
        conn.execute(
            "insert into signup_jobs (job_id, username, email, password_hash, display_name, status, error_message, created_at, updated_at) values (?, ?, ?, ?, ?, 'pending', '', ?, ?)",
            (
                job_id,
                normalized_username,
                normalized_email,
                password_hash,
                display_name.strip() or normalized_username,
                now,
                now,
            ),
        )
        conn.commit()
    return job_id


def get_signup_job(job_id: str, db_path: Path = DEFAULT_AUTH_DB_PATH) -> dict | None:
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select job_id, username, email, display_name, status, error_message, created_at, updated_at from signup_jobs where job_id = ? limit 1",
            (job_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def get_next_pending_signup_job(db_path: Path = DEFAULT_AUTH_DB_PATH) -> dict | None:
    with connect_auth_db(db_path) as conn:
        row = conn.execute(
            "select job_id, username, email, password_hash, display_name, status, error_message, created_at, updated_at from signup_jobs where status = 'pending' order by created_at asc limit 1"
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
    with connect_auth_db(db_path) as conn:
        cursor = conn.execute(
            "delete from users where mapping_username = ? or username = ?",
            (mapping_username, mapping_username),
        )
        conn.commit()
        return cursor.rowcount > 0

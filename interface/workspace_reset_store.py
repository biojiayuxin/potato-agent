"""Durable, account-preserving workspace reset requests and confirmation tokens."""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

from interface.auth_db import DEFAULT_AUTH_DB_PATH, connect_auth_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_reset_jobs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    mapping_username TEXT NOT NULL,
    generation INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    error_code TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, request_id),
    UNIQUE(user_id, generation)
);
CREATE UNIQUE INDEX IF NOT EXISTS workspace_reset_active
ON workspace_reset_jobs(mapping_username) WHERE status IN ('queued','running','blocked');
CREATE TABLE IF NOT EXISTS workspace_reset_confirmations (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""
ACTIVE = ('queued', 'running', 'blocked')


def _db_path(db_path: Path | None = None) -> Path:
    return db_path or Path(os.getenv('INTERFACE_AUTH_DB') or DEFAULT_AUTH_DB_PATH)


class ResetConflict(ValueError):
    pass


def ensure_reset_store(db_path: Path | None = None) -> None:
    with connect_auth_db(_db_path(db_path)) as conn:
        conn.executescript(SCHEMA)


def workspace_state(user_id: str, db_path: Path | None = None) -> dict:
    db_path = _db_path(db_path)
    if not db_path.exists():
        return {'generation': 0, 'job': None}
    with sqlite3.connect(f'{db_path.as_uri()}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        # Older CLI tools can run before the new Interface's first startup.
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='workspace_reset_jobs'").fetchone():
            return {'generation': 0, 'job': None}
        row = conn.execute(
            'SELECT * FROM workspace_reset_jobs WHERE user_id=? ORDER BY generation DESC LIMIT 1',
            (user_id,),
        ).fetchone()
    return {'generation': int(row['generation']) if row else 0,
            'job': public_job(dict(row)) if row and row['status'] in ACTIVE else None}


def maintenance_active(username: str, db_path: Path | None = None) -> bool:
    db_path = _db_path(db_path)
    if not db_path.exists():
        return False
    with sqlite3.connect(f'{db_path.as_uri()}?mode=ro', uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='workspace_reset_jobs'").fetchone():
            return False
        return conn.execute(
            "SELECT 1 FROM workspace_reset_jobs WHERE mapping_username=? AND status IN ('queued','running','blocked')",
            (username,),
        ).fetchone() is not None


def public_job(job: dict) -> dict:
    return {key: job[key] for key in ('job_id', 'generation', 'status', 'phase', 'error_code')}


def prepare_reset(user_id: str, db_path: Path | None = None) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    state = workspace_state(user_id, db_path)
    if state['job']:
        raise ResetConflict('A workspace reset is already in progress.')
    with connect_auth_db(_db_path(db_path)) as conn:
        conn.execute('DELETE FROM workspace_reset_confirmations WHERE expires_at<=? OR user_id=?', (now, user_id))
        conn.execute('INSERT INTO workspace_reset_confirmations VALUES (?,?,?,?)',
                     (hashlib.sha256(token.encode()).hexdigest(), user_id, state['generation'], now + 300))
        conn.commit()
    return token


def enqueue_reset(user_id: str, username: str, token: str, request_id: str,
                  db_path: Path | None = None) -> dict:
    now = int(time.time())
    with connect_auth_db(_db_path(db_path)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT * FROM workspace_reset_jobs WHERE user_id=? AND request_id=?',
                                (user_id, request_id)).fetchone()
        if existing:
            return dict(existing)
        if conn.execute("SELECT 1 FROM workspace_reset_jobs WHERE mapping_username=? AND status IN ('queued','running','blocked')", (username,)).fetchone():
            raise ResetConflict('A workspace reset is already in progress.')
        generation = conn.execute('SELECT coalesce(max(generation),0) FROM workspace_reset_jobs WHERE user_id=?', (user_id,)).fetchone()[0]
        confirmation = conn.execute('SELECT * FROM workspace_reset_confirmations WHERE token_hash=? AND user_id=?',
                                    (hashlib.sha256(token.encode()).hexdigest(), user_id)).fetchone()
        if not confirmation or confirmation['expires_at'] <= now or confirmation['generation'] != generation:
            raise ResetConflict('Confirmation expired. Open Reset workspace and confirm again.')
        # A temporary-account cleanup already claimed the identity must finish first.
        temporary = conn.execute('SELECT cleanup_status FROM temporary_users WHERE user_id=?', (user_id,)).fetchone()
        if temporary and temporary['cleanup_status'] != 'active':
            raise ResetConflict('This workspace is already being cleaned up.')
        if (conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_sleep_claims'").fetchone()
                and conn.execute('SELECT 1 FROM runtime_sleep_claims WHERE user_id=?', (user_id,)).fetchone()):
            raise ResetConflict('This workspace is going to sleep. Reopen it before resetting.')
        conn.execute('DELETE FROM workspace_reset_confirmations WHERE user_id=?', (user_id,))
        job_id = uuid.uuid4().hex
        conn.execute("INSERT INTO workspace_reset_jobs VALUES (?,?,?,?,?,'queued','stopping','',0,?,?)",
                     (job_id, user_id, username, generation + 1, request_id, now, now))
        conn.commit()
        return dict(conn.execute('SELECT * FROM workspace_reset_jobs WHERE job_id=?', (job_id,)).fetchone())


def get_job(job_id: str, db_path: Path | None = None) -> dict | None:
    with connect_auth_db(_db_path(db_path)) as conn:
        row = conn.execute('SELECT * FROM workspace_reset_jobs WHERE job_id=?', (job_id,)).fetchone()
        return dict(row) if row else None


def pending_jobs(db_path: Path | None = None) -> list[dict]:
    with connect_auth_db(_db_path(db_path)) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM workspace_reset_jobs WHERE status IN ('queued','running') ORDER BY created_at"
        )]


def update_job(job_id: str, *, status: str, phase: str, error_code: str = '',
               db_path: Path | None = None) -> None:
    with connect_auth_db(_db_path(db_path)) as conn:
        conn.execute('UPDATE workspace_reset_jobs SET status=?,phase=?,error_code=?,updated_at=? WHERE job_id=?',
                     (status, phase, error_code, int(time.time()), job_id))
        conn.commit()

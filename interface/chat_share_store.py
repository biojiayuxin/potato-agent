from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from interface.secure_paths import (
    DEFAULT_PRIVATE_WRITABLE_DIR_MODE,
    DEFAULT_STATE_DIR,
    ensure_private_directory,
    ensure_sqlite_sidecar_modes,
)


DEFAULT_CHAT_SHARE_DB_PATH = Path(
    os.getenv("INTERFACE_CHAT_SHARE_DB")
    or (DEFAULT_STATE_DIR / "data" / "chat_shares.db")
)
CHAT_SHARE_TTL_SECONDS = 7 * 24 * 60 * 60
CHAT_SHARE_MAX_RECIPIENTS = 100
CHAT_SHARE_MAX_MESSAGES = 200
CHAT_SHARE_MAX_MESSAGE_BYTES = 64 * 1024
CHAT_SHARE_MAX_SNAPSHOT_BYTES = 512 * 1024
CHAT_SHARE_IMPORT_LEASE_SECONDS = 30
CHAT_SHARE_RATE_WINDOW_SECONDS = 60 * 60
CHAT_SHARE_CREATE_RATE_LIMIT = 10
CHAT_SHARE_ACTIVE_LIMIT = 20
CHAT_SHARE_IMPORT_RATE_LIMIT = 20
CHAT_SHARE_MAX_TITLE_BASE_LENGTH = 100
CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS = 60 * 60
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0

CLAIM_STATUS_CLAIMED = "claimed"
CLAIM_STATUS_COMPLETED = "completed"
CLAIM_STATUS_IN_PROGRESS = "in_progress"
CLAIM_STATUS_UNAVAILABLE = "unavailable"
CLAIM_STATUS_RECIPIENT_LIMIT = "recipient_limit"
CLAIM_STATUS_TARGET_DELETED = "target_deleted"
CLAIM_STATUS_RATE_LIMITED = "rate_limited"

LIMIT_CODE_CREATE_RATE = "create_rate_limited"
LIMIT_CODE_ACTIVE_SHARES = "active_share_limit"

_STORE_INIT_LOCK = threading.RLock()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_shares (
    share_id TEXT PRIMARY KEY,
    token_sha256 TEXT NOT NULL UNIQUE,
    owner_user_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    snapshot_version INTEGER NOT NULL DEFAULT 1,
    snapshot_json TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    snapshot_bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    recipient_count INTEGER NOT NULL DEFAULT 0,
    invalidated_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chat_shares_owner_source
ON chat_shares(owner_user_id, source_session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_shares_expiry
ON chat_shares(expires_at);

CREATE TABLE IF NOT EXISTS chat_share_owner_tombstones (
    owner_user_id TEXT PRIMARY KEY,
    invalidated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_share_source_tombstones (
    owner_user_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    invalidated_at INTEGER NOT NULL,
    PRIMARY KEY (owner_user_id, source_session_id)
);

CREATE TABLE IF NOT EXISTS chat_share_source_lifecycle_claims (
    owner_user_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    lifecycle_claim_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (owner_user_id, source_session_id, lifecycle_claim_id)
);

CREATE TABLE IF NOT EXISTS chat_share_recipient_tombstones (
    recipient_user_id TEXT PRIMARY KEY,
    invalidated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_share_imports (
    share_id TEXT NOT NULL,
    recipient_user_id TEXT NOT NULL,
    imported_session_id TEXT NOT NULL UNIQUE,
    imported_title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (
        status IN ('importing', 'completed', 'failed', 'target_deleted')
    ),
    claim_id TEXT NOT NULL DEFAULT '',
    claim_expires_at INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER,
    PRIMARY KEY (share_id, recipient_user_id),
    FOREIGN KEY (share_id) REFERENCES chat_shares(share_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_share_imports_recipient
ON chat_share_imports(recipient_user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_share_imports_recipient_session
ON chat_share_imports(recipient_user_id, imported_session_id);

CREATE TABLE IF NOT EXISTS chat_share_rate_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('create', 'import')),
    user_id TEXT NOT NULL,
    rate_key TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (event_kind, user_id, rate_key)
);

CREATE INDEX IF NOT EXISTS idx_chat_share_rate_events_window
ON chat_share_rate_events(event_kind, user_id, created_at);
"""


class ChatShareValidationError(ValueError):
    pass


class ChatShareLimitError(RuntimeError):
    def __init__(self, code: str, retry_after: int) -> None:
        super().__init__(str(code or "chat_share_limit"))
        self.code = str(code or "chat_share_limit")
        self.retry_after = max(1, int(retry_after))


@dataclass(frozen=True)
class CreatedChatShare:
    share_id: str
    token: str
    expires_at: int
    message_count: int


@dataclass(frozen=True)
class ChatShareImportClaim:
    status: str
    share_id: str = ""
    claim_id: str = ""
    imported_session_id: str = ""
    imported_title: str = ""
    title: str = ""
    messages: tuple[dict[str, Any], ...] = ()
    expires_at: int = 0
    retry_after: int = 0


def _resolve_db_path(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_CHAT_SHARE_DB_PATH


def _connect_chat_share_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
    conn.execute("PRAGMA secure_delete = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_chat_share_store(db_path: Path | str | None = None) -> Path:
    resolved = _resolve_db_path(db_path)
    with _STORE_INIT_LOCK:
        ensure_private_directory(
            resolved.parent, mode=DEFAULT_PRIVATE_WRITABLE_DIR_MODE
        )
        with _connect_chat_share_db(resolved) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA_SQL)
            share_columns = {
                str(row["name"])
                for row in conn.execute("pragma table_info(chat_shares)").fetchall()
            }
            if "recipient_count" not in share_columns:
                conn.execute(
                    "alter table chat_shares "
                    "add column recipient_count integer not null default 0"
                )
                conn.execute(
                    """
                    update chat_shares
                    set recipient_count = (
                        select count(*)
                        from chat_share_imports i
                        where i.share_id = chat_shares.share_id
                    )
                    """
                )
            lifecycle_claim_columns = {
                str(row["name"])
                for row in conn.execute(
                    "pragma table_info(chat_share_source_lifecycle_claims)"
                ).fetchall()
            }
            if "updated_at" not in lifecycle_claim_columns:
                conn.execute(
                    "alter table chat_share_source_lifecycle_claims "
                    "add column updated_at integer not null default 0"
                )
                conn.execute(
                    "update chat_share_source_lifecycle_claims "
                    "set updated_at = created_at where updated_at = 0"
                )
            conn.commit()
        ensure_sqlite_sidecar_modes(resolved)
    return resolved


def hash_chat_share_token(token: str) -> str:
    normalized = str(token or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sanitize_shared_title_base(title: Any) -> str:
    sanitized = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(title or "")
    )
    sanitized = re.sub(
        r"[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]",
        "",
        sanitized,
    )
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized[:CHAT_SHARE_MAX_TITLE_BASE_LENGTH].rstrip()


def _canonical_snapshot(
    messages: list[dict[str, Any]],
    *,
    title: str | None = None,
) -> tuple[str, int, int, str]:
    if not messages:
        raise ChatShareValidationError("A chat must contain at least one message")
    if len(messages) > CHAT_SHARE_MAX_MESSAGES:
        raise ChatShareValidationError("Chat has too many messages to share")

    normalized_messages: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ChatShareValidationError("Chat contains an invalid message")
        role = str(message.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            raise ChatShareValidationError("Chat contains an unsupported message role")
        content = message.get("content")
        if not isinstance(content, str):
            raise ChatShareValidationError("Chat contains non-text message content")
        if len(content.encode("utf-8")) > CHAT_SHARE_MAX_MESSAGE_BYTES:
            raise ChatShareValidationError("A chat message is too large to share")
        normalized_messages.append(
            {
                "role": role,
                "content": content,
            }
        )

    if not any(message["content"].strip() for message in normalized_messages):
        raise ChatShareValidationError("A chat must contain visible message text")

    snapshot = {
        "version": 1,
        "messages": normalized_messages,
    }
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    payload_bytes = len(payload.encode("utf-8"))
    if payload_bytes > CHAT_SHARE_MAX_SNAPSHOT_BYTES:
        raise ChatShareValidationError("Chat is too large to share")
    fallback_title = next(
        (
            message["content"]
            for message in normalized_messages
            if message["role"] == "user" and message["content"].strip()
        ),
        "",
    )
    title_base = (
        _sanitize_shared_title_base(title)
        or _sanitize_shared_title_base(fallback_title)
        or "Shared chat"
    )
    return (
        payload,
        len(normalized_messages),
        payload_bytes,
        title_base,
    )


def _window_retry_after(
    conn: sqlite3.Connection,
    *,
    event_kind: str,
    user_id: str,
    timestamp: int,
    window_seconds: int,
) -> int:
    row = conn.execute(
        """
        select min(created_at) as oldest
        from chat_share_rate_events
        where event_kind = ? and user_id = ? and created_at > ?
        """,
        (event_kind, user_id, timestamp - window_seconds),
    ).fetchone()
    oldest = int(row["oldest"] or timestamp) if row is not None else timestamp
    return max(1, oldest + int(window_seconds) - timestamp)


def create_chat_share(
    *,
    owner_user_id: str,
    source_session_id: str,
    title: str | None = None,
    messages: list[dict[str, Any]],
    now: int | None = None,
    ttl_seconds: int = CHAT_SHARE_TTL_SECONDS,
    create_rate_limit: int = CHAT_SHARE_CREATE_RATE_LIMIT,
    active_share_limit: int = CHAT_SHARE_ACTIVE_LIMIT,
    rate_window_seconds: int = CHAT_SHARE_RATE_WINDOW_SECONDS,
    db_path: Path | str | None = None,
) -> CreatedChatShare:
    normalized_owner = str(owner_user_id or "").strip()
    normalized_session = str(source_session_id or "").strip()
    if not normalized_owner or not normalized_session:
        raise ChatShareValidationError("Share owner and source session are required")
    if int(ttl_seconds) < 1:
        raise ChatShareValidationError("Share lifetime must be positive")
    if (
        int(create_rate_limit) < 1
        or int(active_share_limit) < 1
        or int(rate_window_seconds) < 1
    ):
        raise ValueError("Share limits and rate window must be positive")

    snapshot_json, message_count, snapshot_bytes, title_base = _canonical_snapshot(
        messages,
        title=title,
    )
    timestamp = int(time.time()) if now is None else int(now)
    expires_at = timestamp + int(ttl_seconds)
    share_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    token_sha256 = hash_chat_share_token(token)
    resolved = ensure_chat_share_store(db_path)

    with _connect_chat_share_db(resolved) as conn:
        conn.execute("begin immediate")
        conn.execute(
            "delete from chat_share_source_lifecycle_claims where updated_at <= ?",
            (timestamp - CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS,),
        )
        lifecycle_tombstone = conn.execute(
            """
            select 1
            from chat_share_owner_tombstones
            where owner_user_id = ? and invalidated_at > ?
            union all
            select 1
            from chat_share_source_tombstones
            where owner_user_id = ? and source_session_id = ?
              and invalidated_at > ?
            union all
            select 1
            from chat_share_source_lifecycle_claims
            where owner_user_id = ? and source_session_id = ?
            limit 1
            """,
            (
                normalized_owner,
                timestamp - CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS,
                normalized_owner,
                normalized_session,
                timestamp - CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS,
                normalized_owner,
                normalized_session,
            ),
        ).fetchone()
        if lifecycle_tombstone is not None:
            raise ChatShareValidationError("Source session is no longer shareable")
        conn.execute(
            "delete from chat_shares where expires_at <= ?",
            (timestamp,),
        )
        conn.execute(
            "delete from chat_share_rate_events where created_at <= ?",
            (timestamp - int(rate_window_seconds),),
        )
        recent_creations = int(
            conn.execute(
                """
                select count(*)
                from chat_share_rate_events
                where event_kind = 'create' and user_id = ? and created_at > ?
                """,
                (normalized_owner, timestamp - int(rate_window_seconds)),
            ).fetchone()[0]
            or 0
        )
        if recent_creations >= int(create_rate_limit):
            raise ChatShareLimitError(
                LIMIT_CODE_CREATE_RATE,
                _window_retry_after(
                    conn,
                    event_kind="create",
                    user_id=normalized_owner,
                    timestamp=timestamp,
                    window_seconds=int(rate_window_seconds),
                ),
            )

        active_row = conn.execute(
            """
            select count(*) as active_count, min(expires_at) as earliest_expiry
            from chat_shares
            where owner_user_id = ? and invalidated_at is null and expires_at > ?
            """,
            (normalized_owner, timestamp),
        ).fetchone()
        active_count = int(active_row["active_count"] or 0)
        if active_count >= int(active_share_limit):
            earliest_expiry = int(active_row["earliest_expiry"] or timestamp + 1)
            raise ChatShareLimitError(
                LIMIT_CODE_ACTIVE_SHARES,
                max(1, earliest_expiry - timestamp),
            )

        conn.execute(
            """
            insert into chat_shares (
                share_id, token_sha256, owner_user_id, source_session_id,
                title, snapshot_version, snapshot_json, message_count,
                snapshot_bytes, created_at, expires_at, recipient_count,
                invalidated_at
            ) values (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 0, null)
            """,
            (
                share_id,
                token_sha256,
                normalized_owner,
                normalized_session,
                title_base,
                snapshot_json,
                message_count,
                snapshot_bytes,
                timestamp,
                expires_at,
            ),
        )
        conn.execute(
            """
            insert into chat_share_rate_events (
                event_kind, user_id, rate_key, created_at
            ) values ('create', ?, ?, ?)
            """,
            (normalized_owner, share_id, timestamp),
        )
        conn.commit()
    ensure_sqlite_sidecar_modes(resolved)
    return CreatedChatShare(
        share_id=share_id,
        token=token,
        expires_at=expires_at,
        message_count=message_count,
    )


def _claim_from_rows(
    status: str,
    share: sqlite3.Row | None,
    receipt: sqlite3.Row | None = None,
    *,
    claim_id: str = "",
) -> ChatShareImportClaim:
    if share is None:
        return ChatShareImportClaim(status=status)
    try:
        snapshot = json.loads(str(share["snapshot_json"] or "{}"))
    except json.JSONDecodeError:
        snapshot = {}
    raw_messages = snapshot.get("messages") if isinstance(snapshot, dict) else []
    messages = tuple(
        dict(message) for message in raw_messages if isinstance(message, dict)
    )
    return ChatShareImportClaim(
        status=status,
        share_id=str(share["share_id"] or ""),
        claim_id=claim_id,
        imported_session_id=str(
            receipt["imported_session_id"] if receipt is not None else ""
        ),
        imported_title=str(receipt["imported_title"] if receipt is not None else ""),
        title=str(share["title"] or ""),
        messages=messages,
        expires_at=int(share["expires_at"] or 0),
    )


def _select_claimable_share(
    conn: sqlite3.Connection,
    *,
    token_sha256: str,
    timestamp: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        select share_id, title, snapshot_json, expires_at, recipient_count
        from chat_shares
        where token_sha256 = ?
          and invalidated_at is null
          and expires_at > ?
        limit 1
        """,
        (token_sha256, timestamp),
    ).fetchone()


def claim_chat_share_import(
    *,
    token: str,
    recipient_user_id: str,
    now: int | None = None,
    lease_seconds: int = CHAT_SHARE_IMPORT_LEASE_SECONDS,
    max_recipients: int = CHAT_SHARE_MAX_RECIPIENTS,
    import_rate_limit: int = CHAT_SHARE_IMPORT_RATE_LIMIT,
    rate_window_seconds: int = CHAT_SHARE_RATE_WINDOW_SECONDS,
    db_path: Path | str | None = None,
) -> ChatShareImportClaim:
    token_sha256 = hash_chat_share_token(token)
    normalized_recipient = str(recipient_user_id or "").strip()
    if not token_sha256 or not normalized_recipient:
        return ChatShareImportClaim(status=CLAIM_STATUS_UNAVAILABLE)
    if (
        int(lease_seconds) < 1
        or int(max_recipients) < 1
        or int(import_rate_limit) < 1
        or int(rate_window_seconds) < 1
    ):
        raise ValueError("Import lease, limits, and rate window must be positive")

    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        recipient_is_deleted = conn.execute(
            """
            select 1 from chat_share_recipient_tombstones
            where recipient_user_id = ? and invalidated_at > ?
            limit 1
            """,
            (
                normalized_recipient,
                timestamp - CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS,
            ),
        ).fetchone()
        if recipient_is_deleted is not None:
            return ChatShareImportClaim(status=CLAIM_STATUS_UNAVAILABLE)
        share = _select_claimable_share(
            conn,
            token_sha256=token_sha256,
            timestamp=timestamp,
        )
        if share is None:
            return ChatShareImportClaim(status=CLAIM_STATUS_UNAVAILABLE)

        conn.execute("begin immediate")
        recipient_is_deleted = conn.execute(
            """
            select 1 from chat_share_recipient_tombstones
            where recipient_user_id = ? and invalidated_at > ?
            limit 1
            """,
            (
                normalized_recipient,
                timestamp - CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS,
            ),
        ).fetchone()
        if recipient_is_deleted is not None:
            conn.commit()
            return ChatShareImportClaim(status=CLAIM_STATUS_UNAVAILABLE)
        share = _select_claimable_share(
            conn,
            token_sha256=token_sha256,
            timestamp=timestamp,
        )
        if share is None:
            conn.commit()
            return ChatShareImportClaim(status=CLAIM_STATUS_UNAVAILABLE)

        share_id = str(share["share_id"])
        receipt = conn.execute(
            """
            select imported_session_id, imported_title, status,
                   claim_id, claim_expires_at
            from chat_share_imports
            where share_id = ? and recipient_user_id = ?
            limit 1
            """,
            (share_id, normalized_recipient),
        ).fetchone()
        if receipt is not None:
            receipt_status = str(receipt["status"] or "")
            if receipt_status == "completed":
                conn.commit()
                return _claim_from_rows(CLAIM_STATUS_COMPLETED, share, receipt)
            if receipt_status == "target_deleted":
                conn.commit()
                return _claim_from_rows(CLAIM_STATUS_TARGET_DELETED, share, receipt)
            if (
                receipt_status == "importing"
                and int(receipt["claim_expires_at"] or 0) > timestamp
            ):
                conn.commit()
                return _claim_from_rows(CLAIM_STATUS_IN_PROGRESS, share, receipt)

            claim_id = uuid.uuid4().hex
            conn.execute(
                """
                update chat_share_imports
                set status = 'importing', claim_id = ?, claim_expires_at = ?,
                    error_code = '', updated_at = ?
                where share_id = ? and recipient_user_id = ?
                """,
                (
                    claim_id,
                    timestamp + int(lease_seconds),
                    timestamp,
                    share_id,
                    normalized_recipient,
                ),
            )
            conn.commit()
            return _claim_from_rows(
                CLAIM_STATUS_CLAIMED, share, receipt, claim_id=claim_id
            )

        recipient_count = int(share["recipient_count"] or 0)
        if recipient_count >= int(max_recipients):
            conn.commit()
            return _claim_from_rows(CLAIM_STATUS_RECIPIENT_LIMIT, share)

        conn.execute(
            "delete from chat_share_rate_events where created_at <= ?",
            (timestamp - int(rate_window_seconds),),
        )
        recent_imports = int(
            conn.execute(
                """
                select count(*)
                from chat_share_rate_events
                where event_kind = 'import' and user_id = ? and created_at > ?
                """,
                (normalized_recipient, timestamp - int(rate_window_seconds)),
            ).fetchone()[0]
            or 0
        )
        if recent_imports >= int(import_rate_limit):
            retry_after = _window_retry_after(
                conn,
                event_kind="import",
                user_id=normalized_recipient,
                timestamp=timestamp,
                window_seconds=int(rate_window_seconds),
            )
            conn.commit()
            limited = _claim_from_rows(CLAIM_STATUS_RATE_LIMITED, share)
            return ChatShareImportClaim(
                status=limited.status,
                share_id=limited.share_id,
                title=limited.title,
                messages=limited.messages,
                expires_at=limited.expires_at,
                retry_after=retry_after,
            )

        reserved = conn.execute(
            """
            update chat_shares
            set recipient_count = recipient_count + 1
            where share_id = ? and recipient_count < ?
            """,
            (share_id, int(max_recipients)),
        )
        if reserved.rowcount != 1:
            conn.commit()
            return _claim_from_rows(CLAIM_STATUS_RECIPIENT_LIMIT, share)

        claim_id = uuid.uuid4().hex
        imported_session_id = uuid.uuid4().hex
        conn.execute(
            """
            insert into chat_share_imports (
                share_id, recipient_user_id, imported_session_id,
                imported_title, status, claim_id, claim_expires_at,
                error_code, created_at, updated_at, completed_at
            ) values (?, ?, ?, '', 'importing', ?, ?, '', ?, ?, null)
            """,
            (
                share_id,
                normalized_recipient,
                imported_session_id,
                claim_id,
                timestamp + int(lease_seconds),
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            """
            insert into chat_share_rate_events (
                event_kind, user_id, rate_key, created_at
            ) values ('import', ?, ?, ?)
            """,
            (normalized_recipient, share_id, timestamp),
        )
        receipt = conn.execute(
            """
            select imported_session_id, imported_title, status,
                   claim_id, claim_expires_at
            from chat_share_imports
            where share_id = ? and recipient_user_id = ?
            """,
            (share_id, normalized_recipient),
        ).fetchone()
        conn.commit()
        return _claim_from_rows(
            CLAIM_STATUS_CLAIMED, share, receipt, claim_id=claim_id
        )


def chat_share_import_claim_is_valid(
    *,
    share_id: str,
    recipient_user_id: str,
    claim_id: str,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        row = conn.execute(
            """
            select 1
            from chat_share_imports i
            join chat_shares s on s.share_id = i.share_id
            where i.share_id = ? and i.recipient_user_id = ?
              and i.status = 'importing' and i.claim_id = ?
              and i.claim_expires_at > ?
              and s.invalidated_at is null and s.expires_at > ?
            limit 1
            """,
            (
                str(share_id or "").strip(),
                str(recipient_user_id or "").strip(),
                str(claim_id or "").strip(),
                timestamp,
                timestamp,
            ),
        ).fetchone()
    return row is not None


def complete_chat_share_import(
    *,
    share_id: str,
    recipient_user_id: str,
    claim_id: str,
    imported_title: str,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        cursor = conn.execute(
            """
            update chat_share_imports
            set imported_title = ?, status = 'completed', claim_id = '',
                claim_expires_at = 0, error_code = '', updated_at = ?, completed_at = ?
            where share_id = ? and recipient_user_id = ?
              and status = 'importing' and claim_id = ?
            """,
            (
                str(imported_title or "").strip(),
                timestamp,
                timestamp,
                str(share_id or "").strip(),
                str(recipient_user_id or "").strip(),
                str(claim_id or "").strip(),
            ),
        )
        conn.commit()
    return cursor.rowcount == 1


def fail_chat_share_import(
    *,
    share_id: str,
    recipient_user_id: str,
    claim_id: str,
    error_code: str,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        cursor = conn.execute(
            """
            update chat_share_imports
            set status = 'failed', claim_id = '', claim_expires_at = 0,
                error_code = ?, updated_at = ?
            where share_id = ? and recipient_user_id = ?
              and status = 'importing' and claim_id = ?
            """,
            (
                str(error_code or "import_failed")[:128],
                timestamp,
                str(share_id or "").strip(),
                str(recipient_user_id or "").strip(),
                str(claim_id or "").strip(),
            ),
        )
        conn.commit()
    return cursor.rowcount == 1


def mark_chat_share_import_target_deleted(
    *,
    share_id: str,
    recipient_user_id: str,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> bool:
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        cursor = conn.execute(
            """
            update chat_share_imports
            set status = 'target_deleted', claim_id = '', claim_expires_at = 0,
                error_code = 'target_deleted', updated_at = ?
            where share_id = ? and recipient_user_id = ?
            """,
            (
                timestamp,
                str(share_id or "").strip(),
                str(recipient_user_id or "").strip(),
            ),
        )
        conn.commit()
    return cursor.rowcount == 1


def mark_chat_share_import_target_deleted_by_session(
    *,
    recipient_user_id: str,
    imported_session_id: str,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> bool:
    normalized_recipient = str(recipient_user_id or "").strip()
    normalized_session = str(imported_session_id or "").strip()
    if not normalized_recipient or not normalized_session:
        return False
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        cursor = conn.execute(
            """
            update chat_share_imports
            set status = 'target_deleted', claim_id = '', claim_expires_at = 0,
                error_code = 'target_deleted', updated_at = ?
            where recipient_user_id = ? and imported_session_id = ?
            """,
            (timestamp, normalized_recipient, normalized_session),
        )
        conn.commit()
    return cursor.rowcount == 1


def invalidate_source_session_shares(
    owner_user_id: str,
    source_session_id: str,
    *,
    lifecycle_claim_id: str = "",
    now: int | None = None,
    db_path: Path | str | None = None,
) -> int:
    normalized_owner = str(owner_user_id or "").strip()
    normalized_session = str(source_session_id or "").strip()
    if not normalized_owner or not normalized_session:
        raise ChatShareValidationError("Share owner and source session are required")
    normalized_claim_id = str(lifecycle_claim_id or "").strip()
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        conn.execute("begin immediate")
        if normalized_claim_id:
            conn.execute(
                """
                insert into chat_share_source_lifecycle_claims (
                    owner_user_id, source_session_id, lifecycle_claim_id,
                    created_at, updated_at
                ) values (?, ?, ?, ?, ?)
                on conflict(owner_user_id, source_session_id, lifecycle_claim_id)
                do nothing
                """,
                (
                    normalized_owner,
                    normalized_session,
                    normalized_claim_id,
                    timestamp,
                    timestamp,
                ),
            )
        else:
            conn.execute(
                """
                insert into chat_share_source_tombstones (
                    owner_user_id, source_session_id, invalidated_at
                ) values (?, ?, ?)
                on conflict(owner_user_id, source_session_id) do update set
                    invalidated_at = excluded.invalidated_at
                """,
                (normalized_owner, normalized_session, timestamp),
            )
        share_rows = conn.execute(
            """
            select share_id from chat_shares
            where owner_user_id = ? and source_session_id = ?
              and invalidated_at is null
            """,
            (
                normalized_owner,
                normalized_session,
            ),
        ).fetchall()
        share_ids = [str(row["share_id"]) for row in share_rows]
        if share_ids:
            placeholders = ",".join("?" for _ in share_ids)
            conn.execute(
                f"""
                update chat_shares
                set invalidated_at = ?, title = '', snapshot_json = '{{}}',
                    message_count = 0, snapshot_bytes = 2
                where share_id in ({placeholders})
                """,
                (timestamp, *share_ids),
            )
        conn.commit()
    return len(share_ids)


def heartbeat_source_session_invalidation(
    owner_user_id: str,
    source_session_id: str,
    *,
    lifecycle_claim_id: str,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> bool:
    normalized_owner = str(owner_user_id or "").strip()
    normalized_session = str(source_session_id or "").strip()
    normalized_claim_id = str(lifecycle_claim_id or "").strip()
    if not normalized_owner or not normalized_session or not normalized_claim_id:
        raise ChatShareValidationError(
            "Share owner, source session, and lifecycle claim are required"
        )
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        cursor = conn.execute(
            """
            update chat_share_source_lifecycle_claims
            set updated_at = ?
            where owner_user_id = ? and source_session_id = ?
              and lifecycle_claim_id = ?
            """,
            (
                timestamp,
                normalized_owner,
                normalized_session,
                normalized_claim_id,
            ),
        )
        conn.commit()
    return cursor.rowcount == 1


def clear_source_session_invalidation(
    owner_user_id: str,
    source_session_id: str,
    *,
    lifecycle_claim_id: str | None = None,
    db_path: Path | str | None = None,
) -> bool:
    normalized_owner = str(owner_user_id or "").strip()
    normalized_session = str(source_session_id or "").strip()
    if not normalized_owner or not normalized_session:
        raise ChatShareValidationError("Share owner and source session are required")
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        if lifecycle_claim_id is None:
            tombstone_cursor = conn.execute(
                """
                delete from chat_share_source_tombstones
                where owner_user_id = ? and source_session_id = ?
                """,
                (normalized_owner, normalized_session),
            )
            claim_cursor = conn.execute(
                """
                delete from chat_share_source_lifecycle_claims
                where owner_user_id = ? and source_session_id = ?
                """,
                (normalized_owner, normalized_session),
            )
            changed = tombstone_cursor.rowcount > 0 or claim_cursor.rowcount > 0
        else:
            normalized_claim_id = str(lifecycle_claim_id or "").strip()
            if not normalized_claim_id:
                raise ChatShareValidationError("Lifecycle claim id is required")
            cursor = conn.execute(
                """
                delete from chat_share_source_lifecycle_claims
                where owner_user_id = ? and source_session_id = ?
                  and lifecycle_claim_id = ?
                """,
                (normalized_owner, normalized_session, normalized_claim_id),
            )
            changed = cursor.rowcount > 0
        conn.commit()
    return changed


def finalize_source_session_invalidation(
    owner_user_id: str,
    source_session_id: str,
    *,
    lifecycle_claim_id: str,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> bool:
    normalized_owner = str(owner_user_id or "").strip()
    normalized_session = str(source_session_id or "").strip()
    normalized_claim_id = str(lifecycle_claim_id or "").strip()
    if not normalized_owner or not normalized_session or not normalized_claim_id:
        raise ChatShareValidationError(
            "Share owner, source session, and lifecycle claim are required"
        )
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        conn.execute("begin immediate")
        claim = conn.execute(
            """
            select 1 from chat_share_source_lifecycle_claims
            where owner_user_id = ? and source_session_id = ?
              and lifecycle_claim_id = ?
            limit 1
            """,
            (normalized_owner, normalized_session, normalized_claim_id),
        ).fetchone()
        if claim is None:
            conn.commit()
            return False
        conn.execute(
            """
            insert into chat_share_source_tombstones (
                owner_user_id, source_session_id, invalidated_at
            ) values (?, ?, ?)
            on conflict(owner_user_id, source_session_id) do update set
                invalidated_at = excluded.invalidated_at
            """,
            (normalized_owner, normalized_session, timestamp),
        )
        conn.execute(
            """
            delete from chat_share_source_lifecycle_claims
            where owner_user_id = ? and source_session_id = ?
              and lifecycle_claim_id = ?
            """,
            (normalized_owner, normalized_session, normalized_claim_id),
        )
        conn.commit()
    return True


def invalidate_user_chat_share_data(
    user_id: str,
    *,
    recipient_user_id: str | None = None,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> dict[str, int]:
    normalized_user_id = str(user_id or "").strip()
    normalized_recipient = str(recipient_user_id or normalized_user_id).strip()
    if not normalized_user_id or not normalized_recipient:
        raise ChatShareValidationError("User and recipient identities are required")
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        conn.execute("begin immediate")
        conn.execute(
            """
            insert into chat_share_owner_tombstones (owner_user_id, invalidated_at)
            values (?, ?)
            on conflict(owner_user_id) do nothing
            """,
            (normalized_user_id, timestamp),
        )
        conn.execute(
            """
            insert into chat_share_recipient_tombstones (
                recipient_user_id, invalidated_at
            ) values (?, ?)
            on conflict(recipient_user_id) do nothing
            """,
            (normalized_recipient, timestamp),
        )
        conn.execute(
            "delete from chat_share_source_tombstones where owner_user_id = ?",
            (normalized_user_id,),
        )
        conn.execute(
            "delete from chat_share_source_lifecycle_claims where owner_user_id = ?",
            (normalized_user_id,),
        )
        owned_share_ids = [
            str(row["share_id"])
            for row in conn.execute(
            """
            select share_id from chat_shares
            where owner_user_id = ?
            """,
            (normalized_user_id,),
            ).fetchall()
        ]
        conn.execute(
            "delete from chat_shares where owner_user_id = ?",
            (normalized_user_id,),
        )
        recipient_cursor = conn.execute(
            """
            delete from chat_share_imports
            where recipient_user_id = ?
            """,
            (normalized_recipient,),
        )
        conn.execute(
            """
            delete from chat_share_rate_events
            where (event_kind = 'create' and user_id = ?)
               or (event_kind = 'import' and user_id = ?)
            """,
            (normalized_user_id, normalized_recipient),
        )
        conn.commit()
    return {
        "owned_shares_invalidated": len(owned_share_ids),
        "recipient_receipts_invalidated": max(int(recipient_cursor.rowcount), 0),
    }


def cleanup_expired_chat_shares(
    *,
    now: int | None = None,
    db_path: Path | str | None = None,
) -> int:
    timestamp = int(time.time()) if now is None else int(now)
    resolved = ensure_chat_share_store(db_path)
    with _connect_chat_share_db(resolved) as conn:
        cursor = conn.execute(
            "delete from chat_shares where expires_at <= ?",
            (timestamp,),
        )
        conn.execute(
            "delete from chat_share_rate_events where created_at <= ?",
            (timestamp - CHAT_SHARE_RATE_WINDOW_SECONDS,),
        )
        tombstone_cutoff = timestamp - CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS
        conn.execute(
            "delete from chat_share_owner_tombstones where invalidated_at <= ?",
            (tombstone_cutoff,),
        )
        conn.execute(
            "delete from chat_share_source_tombstones where invalidated_at <= ?",
            (tombstone_cutoff,),
        )
        conn.execute(
            "delete from chat_share_recipient_tombstones where invalidated_at <= ?",
            (tombstone_cutoff,),
        )
        conn.execute(
            "delete from chat_share_source_lifecycle_claims where updated_at <= ?",
            (tombstone_cutoff,),
        )
        conn.commit()
        deleted = max(int(cursor.rowcount), 0)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ensure_sqlite_sidecar_modes(resolved)
    return deleted

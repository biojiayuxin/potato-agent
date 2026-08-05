#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml


DEFAULT_ARCHIVE_DB = Path("/var/lib/potato-agent/data/archive.db")
DEFAULT_INTERFACE_DB = Path("/var/lib/potato-agent/data/interface.db")
DEFAULT_MAPPING = Path("/var/lib/potato-agent/config/users_mapping.yaml")
CONTENT_JSON_PREFIX = "\x00json:"

SESSION_COLUMNS = (
    "id",
    "source",
    "user_id",
    "model",
    "model_config",
    "system_prompt",
    "parent_session_id",
    "started_at",
    "ended_at",
    "end_reason",
    "message_count",
    "tool_call_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "cwd",
    "billing_provider",
    "billing_base_url",
    "billing_mode",
    "estimated_cost_usd",
    "actual_cost_usd",
    "cost_status",
    "cost_source",
    "pricing_version",
    "title",
    "api_call_count",
    "handoff_state",
    "handoff_platform",
    "handoff_error",
    "rewind_count",
    "archived",
)

MESSAGE_COLUMNS = (
    "id",
    "session_id",
    "role",
    "content",
    "tool_call_id",
    "tool_calls",
    "tool_name",
    "timestamp",
    "token_count",
    "finish_reason",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
    "platform_message_id",
    "observed",
    "active",
)

JSON_TEXT_MESSAGE_COLUMNS = {
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
}

FTS_TABLES = ("messages_fts", "messages_fts_trigram")


class RestoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestorePaths:
    archive_db: Path = DEFAULT_ARCHIVE_DB
    interface_db: Path = DEFAULT_INTERFACE_DB
    mapping: Path = DEFAULT_MAPPING


@dataclass(frozen=True)
class OwnerBinding:
    mapping_username: str
    auth_user_id: str
    linux_user: str
    systemd_service: str
    state_db: Path


@dataclass(frozen=True)
class ArchivedChat:
    mapping_username: str
    original_session_id: str
    logical_session_id: str
    source: str
    archived_at: int
    started_at: float
    last_active: float
    hermes_title: str
    draft_title: str
    tool_call_count: int
    session: dict[str, Any]
    messages: list[dict[str, Any]]
    display_messages: list[dict[str, Any]]
    display_messages_json: str

    @property
    def compressed(self) -> bool:
        return self.logical_session_id != self.original_session_id


@dataclass
class RestoreSummary:
    archive_records: int = 0
    raw_messages: int = 0
    display_messages: int = 0
    compressed_records: int = 0
    needs_state: int = 0
    needs_display: int = 0
    already_restored: int = 0
    restored_state: int = 0
    restored_display: int = 0
    mappings: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def public_dict(self, *, mode: str, backup_dir: Path | None = None) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        payload["mode"] = mode
        if backup_dir is not None:
            payload["backup_dir"] = str(backup_dir)
        return payload


def _absolute_regular_file(path: Path, label: str) -> Path:
    expanded = path.expanduser().absolute()
    if expanded.is_symlink():
        raise RestoreError(f"{label} must not be a symbolic link: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise RestoreError(f"{label} not found: {resolved}")
    return resolved


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _connect_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _quick_check(conn: sqlite3.Connection, label: str) -> None:
    rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
    if rows != ["ok"]:
        raise RestoreError(f"{label} failed SQLite quick_check")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    exists = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ? limit 1",
        (table,),
    ).fetchone()
    if exists is None:
        raise RestoreError(f"Required table is missing: {table}")
    return {str(row["name"]) for row in conn.execute(f'pragma table_info("{table}")')}


def _load_mapping_targets(mapping_path: Path) -> dict[str, tuple[str, Path, str]]:
    try:
        payload = yaml.safe_load(mapping_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RestoreError(f"Failed to load mapping file: {exc}") from exc
    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, list):
        raise RestoreError("Mapping file has no users list")

    targets: dict[str, tuple[str, Path, str]] = {}
    for raw in users:
        if not isinstance(raw, dict):
            continue
        username = str(raw.get("username") or "").strip()
        linux_user = str(raw.get("linux_user") or "").strip()
        systemd_service = str(
            raw.get("systemd_service")
            or f"hermes-{username.replace('_', '-')}.service"
        ).strip()
        home_dir = str(raw.get("home_dir") or "").strip()
        hermes_home = str(raw.get("hermes_home") or "").strip()
        if not username:
            continue
        if username in targets:
            raise RestoreError(f"Duplicate mapping username: {username}")
        if not linux_user:
            raise RestoreError(f"Mapping {username!r} has no linux_user")
        if re.fullmatch(r"hermes-[A-Za-z0-9_.@-]+\.service", systemd_service) is None:
            raise RestoreError(
                f"Mapping {username!r} has an invalid systemd_service"
            )
        if not hermes_home:
            if not home_dir:
                raise RestoreError(f"Mapping {username!r} has no Hermes home")
            hermes_home = str(Path(home_dir) / ".hermes")
        if "${" in hermes_home:
            raise RestoreError(f"Mapping {username!r} has an unresolved Hermes home")
        state_db = Path(hermes_home).expanduser().absolute() / "state.db"
        targets[username] = (linux_user, state_db, systemd_service)
    return targets


def _load_auth_users(interface_conn: sqlite3.Connection) -> dict[str, str]:
    _table_columns(interface_conn, "users")
    rows = interface_conn.execute(
        "select id, mapping_username from users order by mapping_username"
    ).fetchall()
    users: dict[str, str] = {}
    for row in rows:
        mapping_username = str(row["mapping_username"] or "").strip()
        user_id = str(row["id"] or "").strip()
        if not mapping_username or not user_id:
            continue
        if mapping_username in users:
            raise RestoreError(f"Duplicate auth mapping username: {mapping_username}")
        users[mapping_username] = user_id
    return users


def _load_bindings(paths: RestorePaths) -> dict[str, OwnerBinding]:
    targets = _load_mapping_targets(paths.mapping)
    with _connect_read_only(paths.interface_db) as interface_conn:
        auth_users = _load_auth_users(interface_conn)

    bindings: dict[str, OwnerBinding] = {}
    for mapping_username, (linux_user, state_db, systemd_service) in targets.items():
        auth_user_id = auth_users.get(mapping_username)
        if not auth_user_id:
            continue
        bindings[mapping_username] = OwnerBinding(
            mapping_username=mapping_username,
            auth_user_id=auth_user_id,
            linux_user=linux_user,
            systemd_service=systemd_service,
            state_db=state_db,
        )
    return bindings


def _json_value(raw: Any, *, expected: type, label: str) -> Any:
    try:
        value = json.loads(str(raw or ""))
    except (json.JSONDecodeError, TypeError) as exc:
        raise RestoreError(f"Malformed {label}") from exc
    if not isinstance(value, expected):
        raise RestoreError(f"{label} has the wrong JSON type")
    return value


def _parse_archived_chat(row: sqlite3.Row) -> ArchivedChat:
    mapping_username = str(row["mapping_username"] or "").strip()
    original_session_id = str(row["original_session_id"] or "").strip()
    source = str(row["source"] or "").strip().lower()
    if not mapping_username or not original_session_id:
        raise RestoreError("Archived record is missing its owner or session id")
    if source != "tui":
        raise RestoreError(f"Archived record for {mapping_username!r} is not a TUI chat")

    session = _json_value(row["session_json"], expected=dict, label="session_json")
    messages = _json_value(row["messages_json"], expected=list, label="messages_json")
    display_messages = _json_value(
        row["display_messages_json"],
        expected=list,
        label="display_messages_json",
    )
    if any(not isinstance(message, dict) for message in messages):
        raise RestoreError("messages_json contains a non-object message")
    if any(not isinstance(message, dict) for message in display_messages):
        raise RestoreError("display_messages_json contains a non-object message")

    stored_session_id = str(session.get("id") or "").strip()
    if stored_session_id and stored_session_id != original_session_id:
        raise RestoreError("Archived session id does not match its archive index")
    logical_session_id = str(
        session.get("_lineage_root_id") or original_session_id
    ).strip()
    if not logical_session_id:
        raise RestoreError("Archived record has no logical session id")
    declared_count = int(row["message_count"] or 0)
    if declared_count != len(messages):
        raise RestoreError("Archived message count does not match messages_json")
    message_ids = [message.get("id") for message in messages]
    if any(message_id is None for message_id in message_ids):
        raise RestoreError("Archived message is missing its original id")
    try:
        normalized_message_ids = [int(message_id) for message_id in message_ids]
    except (TypeError, ValueError) as exc:
        raise RestoreError("Archived message has an invalid original id") from exc
    if len(set(normalized_message_ids)) != len(normalized_message_ids):
        raise RestoreError("Archived chat contains duplicate message ids")

    return ArchivedChat(
        mapping_username=mapping_username,
        original_session_id=original_session_id,
        logical_session_id=logical_session_id,
        source=source,
        archived_at=int(row["archived_at"] or 0),
        started_at=float(row["started_at"] or session.get("started_at") or 0),
        last_active=float(row["last_active"] or row["started_at"] or 0),
        hermes_title=str(row["hermes_title"] or ""),
        draft_title=str(row["draft_title"] or ""),
        tool_call_count=int(row["tool_call_count"] or 0),
        session=session,
        messages=messages,
        display_messages=display_messages,
        display_messages_json=str(row["display_messages_json"] or "[]"),
    )


def _archive_mappings(
    archive_conn: sqlite3.Connection,
    selected_mappings: set[str] | None,
) -> list[str]:
    rows = archive_conn.execute(
        "select distinct mapping_username from archived_sessions order by mapping_username"
    ).fetchall()
    available = [str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()]
    if selected_mappings is None:
        return available
    unknown = selected_mappings - set(available)
    if unknown:
        raise RestoreError(
            "Requested mapping has no archived chats: " + ", ".join(sorted(unknown))
        )
    return [item for item in available if item in selected_mappings]


def _iter_archived_chats(
    archive_conn: sqlite3.Connection,
    mapping_username: str,
) -> Iterator[ArchivedChat]:
    rows = archive_conn.execute(
        """
        select archived_at, mapping_username, original_session_id, source,
               hermes_title, draft_title, started_at, last_active,
               message_count, tool_call_count, session_json, messages_json,
               display_messages_json
        from archived_sessions
        where mapping_username = ?
        order by archived_at, original_session_id
        """,
        (mapping_username,),
    )
    for row in rows:
        yield _parse_archived_chat(row)


def _decode_content(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(CONTENT_JSON_PREFIX):
        try:
            return json.loads(value[len(CONTENT_JSON_PREFIX) :])
        except json.JSONDecodeError:
            return value
    return value


def _decode_json_text(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _message_signature(message: dict[str, Any]) -> tuple[Any, ...]:
    normalized: list[Any] = []
    for column in MESSAGE_COLUMNS:
        if column == "session_id":
            continue
        value = message.get(column)
        if column == "content":
            value = _decode_content(value)
        elif column == "tool_calls":
            value = _decode_json_text(value)
        elif column in JSON_TEXT_MESSAGE_COLUMNS:
            value = _decode_json_text(value)
        elif column == "observed":
            value = int(value or 0)
        elif column == "active":
            value = 1 if value is None else int(value)
        normalized.append(_freeze_json(value))
    return tuple(normalized)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_json(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _messages_are_prefix(
    archived_messages: list[dict[str, Any]],
    existing_messages: list[dict[str, Any]],
) -> bool:
    if len(existing_messages) < len(archived_messages):
        return False
    return all(
        _message_signature(archived) == _message_signature(existing)
        for archived, existing in zip(archived_messages, existing_messages)
    )


def _display_is_prefix(
    archived_messages: list[dict[str, Any]],
    existing_messages: list[dict[str, Any]],
) -> bool:
    if len(existing_messages) < len(archived_messages):
        return False
    return existing_messages[: len(archived_messages)] == archived_messages


def _existing_state_status(
    conn: sqlite3.Connection,
    chat: ArchivedChat,
) -> tuple[bool, str | None]:
    session = conn.execute(
        "select source from sessions where id = ? limit 1",
        (chat.logical_session_id,),
    ).fetchone()
    message_ids = [
        int(message["id"])
        for message in chat.messages
        if message.get("id") is not None
    ]

    if session is None:
        for start in range(0, len(message_ids), 500):
            chunk = message_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            collision = conn.execute(
                f"select 1 from messages where id in ({placeholders}) limit 1",
                chunk,
            ).fetchone()
            if collision is not None:
                return False, "archived message id collides with a live message"
            for fts_table in FTS_TABLES:
                collision = conn.execute(
                    f'select 1 from "{fts_table}" '
                    f"where rowid in ({placeholders}) limit 1",
                    chunk,
                ).fetchone()
                if collision is not None:
                    return (
                        False,
                        f"archived message id collides with a stale {fts_table} rowid",
                    )
        return False, None

    if str(session["source"] or "").strip().lower() != "tui":
        return True, "logical session id is already used by a non-TUI session"
    existing = [
        dict(row)
        for row in conn.execute(
            "select * from messages where session_id = ? order by id",
            (chat.logical_session_id,),
        ).fetchall()
    ]
    if not _messages_are_prefix(chat.messages, existing):
        return True, "logical session exists with different messages"
    for start in range(0, len(message_ids), 500):
        chunk = message_ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for fts_table in FTS_TABLES:
            indexed = int(
                conn.execute(
                    f'select count(*) from "{fts_table}" '
                    f"where rowid in ({placeholders})",
                    chunk,
                ).fetchone()[0]
            )
            if indexed != len(chunk):
                return True, f"logical session is missing {fts_table} rows"
    return True, None


def _existing_display_status(
    conn: sqlite3.Connection,
    binding: OwnerBinding,
    chat: ArchivedChat,
) -> tuple[bool, str | None]:
    row = conn.execute(
        "select messages_json from session_display_transcripts "
        "where user_id = ? and session_id = ? limit 1",
        (binding.auth_user_id, chat.logical_session_id),
    ).fetchone()
    if row is None:
        return False, None
    try:
        existing = json.loads(str(row["messages_json"] or "[]"))
    except json.JSONDecodeError:
        return True, "existing display transcript is malformed"
    if not isinstance(existing, list) or not _display_is_prefix(chat.display_messages, existing):
        return True, "logical session has a different display transcript"
    return True, None


def preflight_restore(
    paths: RestorePaths,
    *,
    selected_mappings: set[str] | None = None,
) -> tuple[RestoreSummary, dict[str, OwnerBinding]]:
    paths = RestorePaths(
        archive_db=_absolute_regular_file(paths.archive_db, "Archive database"),
        interface_db=_absolute_regular_file(paths.interface_db, "Interface database"),
        mapping=_absolute_regular_file(paths.mapping, "Mapping file"),
    )
    bindings = _load_bindings(paths)
    summary = RestoreSummary()

    with _connect_read_only(paths.archive_db) as archive_conn, _connect_read_only(
        paths.interface_db
    ) as interface_conn:
        _quick_check(archive_conn, "Archive database")
        _quick_check(interface_conn, "Interface database")
        _table_columns(archive_conn, "archived_sessions")
        _table_columns(interface_conn, "session_display_transcripts")
        mappings = _archive_mappings(archive_conn, selected_mappings)

        for mapping_username in mappings:
            binding = bindings.get(mapping_username)
            if binding is None:
                summary.errors.append(
                    f"Archive owner {mapping_username!r} has no matching target and auth user"
                )
                continue
            try:
                state_db = _absolute_regular_file(
                    binding.state_db,
                    f"State database for {mapping_username}",
                )
            except RestoreError as exc:
                summary.errors.append(str(exc))
                continue
            binding = OwnerBinding(
                mapping_username=binding.mapping_username,
                auth_user_id=binding.auth_user_id,
                linux_user=binding.linux_user,
                systemd_service=binding.systemd_service,
                state_db=state_db,
            )
            bindings[mapping_username] = binding
            seen_logical_ids: set[str] = set()
            seen_message_ids: set[int] = set()
            planned_hermes_titles: dict[str, str] = {}
            try:
                with _connect_read_only(state_db) as state_conn:
                    _quick_check(state_conn, f"State database for {mapping_username}")
                    session_columns = _table_columns(state_conn, "sessions")
                    message_columns = _table_columns(state_conn, "messages")
                    for fts_table in FTS_TABLES:
                        _table_columns(state_conn, fts_table)
                    for chat in _iter_archived_chats(archive_conn, mapping_username):
                        summary.archive_records += 1
                        summary.raw_messages += len(chat.messages)
                        summary.display_messages += len(chat.display_messages)
                        summary.mappings[mapping_username] = (
                            summary.mappings.get(mapping_username, 0) + 1
                        )
                        if chat.compressed:
                            summary.compressed_records += 1
                        if chat.logical_session_id in seen_logical_ids:
                            summary.errors.append(
                                f"Archive owner {mapping_username!r} has a duplicate logical session id"
                            )
                            continue
                        seen_logical_ids.add(chat.logical_session_id)
                        archived_message_ids = {
                            int(message["id"]) for message in chat.messages
                        }
                        if seen_message_ids.intersection(archived_message_ids):
                            summary.errors.append(
                                f"Archive owner {mapping_username!r} has duplicate message ids across chats"
                            )
                            continue
                        seen_message_ids.update(archived_message_ids)
                        state_exists, state_error = _existing_state_status(state_conn, chat)
                        if not state_exists and state_error is None:
                            _session_values(chat, session_columns)
                            for index, message in enumerate(chat.messages):
                                _message_values(
                                    chat,
                                    message,
                                    message_columns,
                                    index,
                                )
                        title_error: str | None = None
                        hermes_title = chat.hermes_title.strip()
                        if not state_exists and hermes_title:
                            live_title_owner = state_conn.execute(
                                "select id from sessions "
                                "where title = ? and id != ? limit 1",
                                (hermes_title, chat.logical_session_id),
                            ).fetchone()
                            planned_title_owner = planned_hermes_titles.get(hermes_title)
                            if live_title_owner is not None:
                                title_error = (
                                    "Hermes title is already used by a live session"
                                )
                            elif planned_title_owner is not None:
                                title_error = (
                                    "archive contains duplicate non-empty Hermes titles"
                                )
                            else:
                                planned_hermes_titles[hermes_title] = (
                                    chat.logical_session_id
                                )
                        display_exists, display_error = _existing_display_status(
                            interface_conn,
                            binding,
                            chat,
                        )
                        if state_error:
                            summary.errors.append(
                                f"State conflict for archive owner {mapping_username!r}: {state_error}"
                            )
                        if title_error:
                            summary.errors.append(
                                f"State conflict for archive owner {mapping_username!r}: {title_error}"
                            )
                        if display_error:
                            summary.errors.append(
                                f"Display conflict for archive owner {mapping_username!r}: {display_error}"
                            )
                        if state_error or title_error or display_error:
                            continue
                        if state_exists and display_exists:
                            summary.already_restored += 1
                        else:
                            if not state_exists:
                                summary.needs_state += 1
                            if not display_exists:
                                summary.needs_display += 1
            except (RestoreError, sqlite3.Error) as exc:
                summary.errors.append(
                    f"Failed to inspect archive owner {mapping_username!r}: {exc}"
                )
    return summary, bindings


def _backup_database(source: Path, destination: Path) -> None:
    with _connect_read_only(source) as source_conn, sqlite3.connect(
        str(destination)
    ) as destination_conn:
        source_conn.backup(destination_conn)
        _quick_check(destination_conn, f"Backup {destination.name}")
    os.chmod(destination, 0o600)


def _safe_backup_name(mapping_username: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", mapping_username).strip("._")
    return slug or "user"


def create_backups(
    paths: RestorePaths,
    bindings: dict[str, OwnerBinding],
    affected_mappings: set[str],
    backup_dir: Path,
) -> Path:
    target = backup_dir.expanduser().absolute()
    if target.exists():
        raise RestoreError(f"Backup directory already exists: {target}")
    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    required_bytes = paths.archive_db.stat().st_size + paths.interface_db.stat().st_size
    for mapping_username in affected_mappings:
        required_bytes += bindings[mapping_username].state_db.stat().st_size
    available_bytes = shutil.disk_usage(parent).free
    if available_bytes < int(required_bytes * 1.1):
        raise RestoreError("Insufficient free space for the required SQLite backups")

    target.mkdir(parents=True, mode=0o700)
    os.chmod(target, 0o700)
    try:
        _backup_database(paths.archive_db, target / "archive.db")
        _backup_database(paths.interface_db, target / "interface.db")
        for mapping_username in sorted(affected_mappings):
            destination = target / f"state--{_safe_backup_name(mapping_username)}.db"
            _backup_database(bindings[mapping_username].state_db, destination)
        manifest = {
            "created_at": int(time.time()),
            "archive_db": "archive.db",
            "interface_db": "interface.db",
            "state_databases": {
                mapping_username: f"state--{_safe_backup_name(mapping_username)}.db"
                for mapping_username in sorted(affected_mappings)
            },
        }
        manifest_path = target / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(manifest_path, 0o600)
    except Exception:
        raise
    return target


def _encode_content(value: Any) -> Any:
    if value is None or isinstance(value, (str, bytes, int, float)):
        return value
    try:
        return CONTENT_JSON_PREFIX + json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _require_preserved_columns(
    table: str,
    values: dict[str, Any],
    available_columns: set[str],
) -> None:
    missing = sorted(
        column
        for column, value in values.items()
        if value is not None and column not in available_columns
    )
    if missing:
        raise RestoreError(
            f"State {table} table cannot preserve archived non-NULL columns: "
            + ", ".join(missing)
        )


def _session_values(
    chat: ArchivedChat,
    available_columns: set[str],
) -> tuple[list[str], list[Any]]:
    restored = {
        column: chat.session.get(column)
        for column in SESSION_COLUMNS
        if column in chat.session
    }
    restored.update(
        {
            "id": chat.logical_session_id,
            "source": "tui",
            "parent_session_id": None,
            "started_at": chat.started_at,
            "message_count": len(chat.messages),
            "tool_call_count": chat.tool_call_count,
            "archived": 0,
        }
    )
    restored.pop("title", None)
    hermes_title = chat.hermes_title.strip()
    if hermes_title:
        restored["title"] = hermes_title
    _require_preserved_columns("sessions", restored, available_columns)
    columns = [
        column
        for column in SESSION_COLUMNS
        if column in available_columns and column in restored
    ]
    missing_required = {"id", "source", "started_at"} - set(columns)
    if missing_required:
        raise RestoreError(
            "State sessions table is missing required columns: "
            + ", ".join(sorted(missing_required))
        )
    return columns, [_sqlite_value(restored.get(column)) for column in columns]


def _message_values(
    chat: ArchivedChat,
    message: dict[str, Any],
    available_columns: set[str],
    index: int,
) -> tuple[list[str], list[Any]]:
    values: dict[str, Any] = {
        column: message.get(column) for column in MESSAGE_COLUMNS
    }
    if message.get("id") is None:
        raise RestoreError("Archived message is missing its original id")
    values["id"] = int(message["id"])
    values["session_id"] = chat.logical_session_id
    values["role"] = str(message.get("role") or "unknown")
    values["timestamp"] = float(
        message.get("timestamp") or chat.started_at + (index * 0.000001)
    )
    values["content"] = _encode_content(message.get("content"))
    tool_calls = message.get("tool_calls")
    if tool_calls is not None and not isinstance(tool_calls, str):
        values["tool_calls"] = json.dumps(
            tool_calls,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    for column in JSON_TEXT_MESSAGE_COLUMNS:
        if column in values and isinstance(values[column], (dict, list)):
            values[column] = json.dumps(
                values[column],
                ensure_ascii=False,
                separators=(",", ":"),
            )
    if "observed" in values:
        values["observed"] = int(values["observed"] or 0)
    if "active" in values:
        values["active"] = 1 if values["active"] is None else int(values["active"])
    _require_preserved_columns("messages", values, available_columns)
    columns = [column for column in MESSAGE_COLUMNS if column in available_columns]
    missing_required = {"id", "session_id", "role", "timestamp"} - set(columns)
    if missing_required:
        raise RestoreError(
            "State messages table is missing required columns: "
            + ", ".join(sorted(missing_required))
        )
    return columns, [_sqlite_value(values.get(column)) for column in columns]


def _insert_chat_state(conn: sqlite3.Connection, chat: ArchivedChat) -> None:
    session_columns = _table_columns(conn, "sessions")
    message_columns = _table_columns(conn, "messages")
    columns, values = _session_values(chat, session_columns)
    placeholders = ",".join("?" for _ in columns)
    selected = ",".join(f'"{column}"' for column in columns)
    conn.execute(
        f"insert into sessions ({selected}) values ({placeholders})",
        values,
    )
    for index, message in enumerate(chat.messages):
        columns, values = _message_values(chat, message, message_columns, index)
        placeholders = ",".join("?" for _ in columns)
        selected = ",".join(f'"{column}"' for column in columns)
        conn.execute(
            f"insert into messages ({selected}) values ({placeholders})",
            values,
        )


def _insert_chat_display(
    conn: sqlite3.Connection,
    binding: OwnerBinding,
    chat: ArchivedChat,
) -> None:
    conn.execute(
        """
        insert into session_display_transcripts (
            user_id, session_id, messages_json, draft_title, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            binding.auth_user_id,
            chat.logical_session_id,
            chat.display_messages_json,
            chat.draft_title or chat.hermes_title,
            int(chat.started_at),
            int(chat.last_active),
        ),
    )


def _capture_database_metadata(path: Path) -> tuple[int, int, int]:
    info = path.stat()
    return info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)


def _restore_database_metadata(path: Path, metadata: tuple[int, int, int]) -> None:
    uid, gid, mode = metadata
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if not candidate.exists():
            continue
        if os.geteuid() == 0:
            os.chown(candidate, uid, gid)
        os.chmod(candidate, mode)
        info = candidate.stat()
        actual = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        if actual != metadata:
            raise RestoreError(f"Failed to restore database ownership/mode: {candidate}")


def _verify_services_stopped(
    bindings: dict[str, OwnerBinding],
    mappings: set[str],
) -> None:
    units = {"potato-interface.service"}
    units.update(bindings[mapping].systemd_service for mapping in mappings)
    unsafe: list[str] = []
    for unit in sorted(units):
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "--property=LoadState",
                    "--property=ActiveState",
                    "--no-pager",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RestoreError(f"Failed to inspect service {unit}: {exc}") from exc
        if result.returncode != 0:
            raise RestoreError(
                f"Failed to inspect service {unit}: systemctl exited "
                f"with status {result.returncode}"
            )
        properties = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties[key] = value
        load_state = properties.get("LoadState", "")
        active_state = properties.get("ActiveState", "")
        if load_state != "loaded" or active_state not in {"inactive", "failed"}:
            unsafe.append(f"{unit} ({load_state or 'unknown'}/{active_state or 'unknown'})")
    if unsafe:
        raise RestoreError(
            "Restore requires stopped, loaded services; unsafe state: "
            + ", ".join(unsafe)
        )


def apply_restore(
    paths: RestorePaths,
    bindings: dict[str, OwnerBinding],
    mappings: set[str],
) -> RestoreSummary:
    summary = RestoreSummary()
    committed_state_mappings: list[str] = []
    with _connect_read_only(paths.archive_db) as archive_conn:
        for mapping_username in sorted(mappings):
            binding = bindings[mapping_username]
            metadata = _capture_database_metadata(binding.state_db)
            conn = _connect_writable(binding.state_db)
            try:
                conn.execute("BEGIN IMMEDIATE")
                for chat in _iter_archived_chats(archive_conn, mapping_username):
                    state_exists, state_error = _existing_state_status(conn, chat)
                    if state_error:
                        raise RestoreError(
                            f"State changed after preflight for {mapping_username!r}: {state_error}"
                        )
                    if not state_exists:
                        _insert_chat_state(conn, chat)
                        summary.restored_state += 1
                conn.commit()
                committed_state_mappings.append(mapping_username)
            except Exception as exc:
                conn.rollback()
                committed = ", ".join(committed_state_mappings) or "none"
                raise RestoreError(
                    f"State restore failed for {mapping_username!r}; "
                    f"already committed state mappings: {committed}; error: {exc}"
                ) from exc
            finally:
                conn.close()
                _restore_database_metadata(binding.state_db, metadata)

        metadata = _capture_database_metadata(paths.interface_db)
        interface_conn = _connect_writable(paths.interface_db)
        try:
            interface_conn.execute("BEGIN IMMEDIATE")
            for mapping_username in sorted(mappings):
                binding = bindings[mapping_username]
                for chat in _iter_archived_chats(archive_conn, mapping_username):
                    display_exists, display_error = _existing_display_status(
                        interface_conn,
                        binding,
                        chat,
                    )
                    if display_error:
                        raise RestoreError(
                            f"Display changed after preflight for {mapping_username!r}: {display_error}"
                        )
                    if not display_exists:
                        _insert_chat_display(interface_conn, binding, chat)
                        summary.restored_display += 1
            interface_conn.commit()
        except Exception as exc:
            interface_conn.rollback()
            committed = ", ".join(committed_state_mappings) or "none"
            raise RestoreError(
                "Interface display restore failed after committed state mappings "
                f"{committed}; error: {exc}"
            ) from exc
        finally:
            interface_conn.close()
            _restore_database_metadata(paths.interface_db, metadata)
    return summary


def run_restore(
    paths: RestorePaths,
    *,
    apply: bool = False,
    backup_dir: Path | None = None,
    selected_mappings: set[str] | None = None,
    enforce_services_stopped: bool = False,
) -> tuple[RestoreSummary, Path | None]:
    normalized_paths = RestorePaths(
        archive_db=_absolute_regular_file(paths.archive_db, "Archive database"),
        interface_db=_absolute_regular_file(paths.interface_db, "Interface database"),
        mapping=_absolute_regular_file(paths.mapping, "Mapping file"),
    )
    summary, bindings = preflight_restore(
        normalized_paths,
        selected_mappings=selected_mappings,
    )
    if not summary.ok:
        return summary, None
    mappings = set(summary.mappings)
    if not apply:
        return summary, None
    if backup_dir is None:
        raise RestoreError("--backup-dir is required with --apply")
    if enforce_services_stopped:
        _verify_services_stopped(bindings, mappings)
    created_backup = create_backups(
        normalized_paths,
        bindings,
        mappings,
        backup_dir,
    )
    try:
        if enforce_services_stopped:
            _verify_services_stopped(bindings, mappings)
        applied = apply_restore(normalized_paths, bindings, mappings)
        if enforce_services_stopped:
            _verify_services_stopped(bindings, mappings)
        postflight, _ = preflight_restore(
            normalized_paths,
            selected_mappings=selected_mappings,
        )
        if not postflight.ok:
            raise RestoreError(
                "Postflight preflight failed: " + "; ".join(postflight.errors)
            )
        if (
            postflight.archive_records != summary.archive_records
            or postflight.needs_state != 0
            or postflight.needs_display != 0
            or postflight.already_restored != postflight.archive_records
        ):
            raise RestoreError(
                "Postflight counts do not prove a complete restore: "
                f"archive_records={postflight.archive_records}, "
                f"needs_state={postflight.needs_state}, "
                f"needs_display={postflight.needs_display}, "
                f"already_restored={postflight.already_restored}"
            )
    except Exception as exc:
        if isinstance(exc, RestoreError):
            detail = str(exc)
        else:
            detail = f"{type(exc).__name__}: {exc}"
        raise RestoreError(
            f"Restore did not complete; backups are at {created_backup}; {detail}"
        ) from exc
    postflight.restored_state = applied.restored_state
    postflight.restored_display = applied.restored_display
    return postflight, created_backup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore archived TUI chats to each owner's Hermes state database "
            "and Interface display transcript store. Defaults to a read-only dry-run."
        )
    )
    parser.add_argument("--archive-db", type=Path, default=DEFAULT_ARCHIVE_DB)
    parser.add_argument("--interface-db", type=Path, default=DEFAULT_INTERFACE_DB)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument(
        "--mapping-username",
        action="append",
        default=None,
        help="Restore only this mapping username; may be repeated.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the restore after a successful preflight. Services must be stopped.",
    )
    parser.add_argument(
        "--confirm-services-stopped",
        action="store_true",
        help="Required with --apply; asserts Interface and affected Hermes services are stopped.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Required new private directory for SQLite backups when --apply is used.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply and not args.confirm_services_stopped:
        parser.error("--apply requires --confirm-services-stopped")
    if args.apply and args.backup_dir is None:
        parser.error("--apply requires --backup-dir")
    paths = RestorePaths(
        archive_db=args.archive_db,
        interface_db=args.interface_db,
        mapping=args.mapping,
    )
    try:
        summary, backup_dir = run_restore(
            paths,
            apply=bool(args.apply),
            backup_dir=args.backup_dir,
            selected_mappings=(
                set(args.mapping_username) if args.mapping_username else None
            ),
            enforce_services_stopped=bool(args.apply),
        )
    except (RestoreError, sqlite3.Error, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            summary.public_dict(
                mode="apply" if args.apply else "dry-run",
                backup_dir=backup_dir,
            ),
            sort_keys=True,
        )
    )
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())

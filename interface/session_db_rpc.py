from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from hermes_state import SessionDB


READ_ONLY_METHODS = {
    "get_logical_session_context",
    "get_compression_tip",
    "get_messages",
    "get_session",
    "list_sessions_rich",
    "resolve_session_id",
}

MAX_SHARED_IMPORT_MESSAGES = 200
MAX_SHARED_IMPORT_MESSAGE_BYTES = 64 * 1024
MAX_SHARED_IMPORT_SNAPSHOT_BYTES = 512 * 1024
MAX_SHARED_IMPORT_TITLE_LENGTH = 100


def _is_compression_continuation(
    parent_session: dict[str, Any] | None,
    child_session: dict[str, Any] | None,
) -> bool:
    if not isinstance(parent_session, dict) or not isinstance(child_session, dict):
        return False
    parent_id = str(parent_session.get("id") or "").strip()
    if not parent_id:
        return False
    if str(child_session.get("parent_session_id") or "").strip() != parent_id:
        return False
    if str(parent_session.get("end_reason") or "").strip() != "compression":
        return False
    ended_at = float(parent_session.get("ended_at") or 0)
    started_at = float(child_session.get("started_at") or 0)
    return ended_at > 0 and started_at >= ended_at


def _find_logical_root(db: SessionDB, session_id: str) -> str:
    current_id = str(session_id or "").strip()
    current_session = db.get_session(current_id)
    if not current_session:
        return current_id
    for _ in range(100):
        parent_id = str(current_session.get("parent_session_id") or "").strip()
        if not parent_id:
            break
        parent_session = db.get_session(parent_id)
        if not _is_compression_continuation(parent_session, current_session):
            break
        current_id = parent_id
        current_session = parent_session
    return current_id


def _get_logical_session_context(
    db: SessionDB,
    *,
    session_id: str,
    include_messages: bool = False,
) -> dict[str, Any]:
    connection = getattr(db, "_conn", None)
    started_transaction = bool(
        connection is not None and not bool(getattr(connection, "in_transaction", False))
    )
    if started_transaction:
        connection.execute("BEGIN")
    try:
        resolved = db.resolve_session_id(str(session_id or "").strip())
        if not resolved:
            return {
                "logical_session_id": "",
                "logical_session": None,
                "tip_session_id": "",
                "projected_session": None,
                "messages": [],
            }

        logical_session_id = _find_logical_root(db, resolved)
        logical_session = db.get_session(logical_session_id)
        tip_session_id = str(
            db.get_compression_tip(logical_session_id) or logical_session_id
        ).strip()
        projected_session = logical_session
        projected_candidates = db.list_sessions_rich(
            source="tui",
            limit=20,
            offset=0,
            order_by_last_active=True,
            include_archived=True,
            id_query=logical_session_id,
        )
        for session in projected_candidates:
            lineage_root_id = str(
                session.get("_lineage_root_id") or session.get("id") or ""
            ).strip()
            if lineage_root_id == logical_session_id:
                projected_session = session
                break

        messages = db.get_messages(tip_session_id) if include_messages else []
        return {
            "logical_session_id": logical_session_id,
            "logical_session": logical_session,
            "tip_session_id": tip_session_id,
            "projected_session": projected_session,
            "messages": messages,
        }
    finally:
        if started_transaction:
            connection.rollback()


def _sanitize_shared_title_base(title: str) -> str:
    base = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(title or ""))
    base = re.sub(
        r"[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]",
        "",
        base,
    )
    return re.sub(r"\s+", " ", base).strip() or "Shared chat"


def _shared_title_candidate(title: str, attempt: int) -> str:
    base = _sanitize_shared_title_base(title)
    suffix = " (shared)" if attempt <= 1 else f" (shared {attempt})"
    max_base_length = MAX_SHARED_IMPORT_TITLE_LENGTH - len(suffix)
    return f"{base[:max_base_length].rstrip()}{suffix}"


def _message_identity(message: dict[str, Any]) -> tuple[str, str] | None:
    role = message.get("role")
    content = message.get("content")
    if not isinstance(role, str) or not isinstance(content, str):
        return None
    return role, content


def _existing_messages_have_import_prefix(
    existing_messages: list[dict[str, Any]],
    imported_messages: list[dict[str, str]],
) -> bool:
    if len(existing_messages) < len(imported_messages):
        return False
    return all(
        _message_identity(existing) == _message_identity(imported)
        for existing, imported in zip(existing_messages, imported_messages)
    )


def _import_shared_session(db: SessionDB, kwargs: dict[str, Any]) -> dict[str, Any]:
    session_id = str(kwargs.get("session_id") or "").strip()
    title = str(kwargs.get("title") or "").strip()
    raw_messages = kwargs.get("messages")
    if not session_id or len(session_id) > 128:
        raise ValueError("Invalid imported session id")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("Shared messages are required")
    if len(raw_messages) > MAX_SHARED_IMPORT_MESSAGES:
        raise ValueError("Shared chat has too many messages")

    messages: list[dict[str, str]] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            raise ValueError("Shared chat contains an invalid message")
        role = str(raw_message.get("role") or "").strip()
        content = raw_message.get("content")
        if role not in {"user", "assistant"}:
            raise ValueError("Shared chat contains an invalid role")
        if not isinstance(content, str):
            raise ValueError("Shared chat contains non-text content")
        if len(content.encode("utf-8")) > MAX_SHARED_IMPORT_MESSAGE_BYTES:
            raise ValueError("Shared chat contains an oversized message")
        messages.append({"role": role, "content": content})

    if not any(message["content"].strip() for message in messages):
        raise ValueError("Shared chat contains no visible message text")
    snapshot_bytes = len(
        json.dumps(
            {"version": 1, "messages": messages},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if snapshot_bytes > MAX_SHARED_IMPORT_SNAPSHOT_BYTES:
        raise ValueError("Shared chat is too large")

    existing = db.get_session(session_id)
    if existing is not None and str(existing.get("source") or "") != "tui":
        raise ValueError("Imported session id is already in use")
    if existing is None:
        db.create_session(session_id, "tui")
        existing = db.get_session(session_id)
        if existing is None or str(existing.get("source") or "") != "tui":
            raise ValueError("Unable to create imported session")

    existing_messages = db.get_messages(session_id)
    if not existing_messages:
        db.replace_messages(session_id, messages)
    elif not _existing_messages_have_import_prefix(existing_messages, messages):
        raise ValueError("Imported session id contains different messages")

    persisted_messages = db.get_messages(session_id)
    if not _existing_messages_have_import_prefix(persisted_messages, messages):
        raise ValueError("Unable to persist the shared chat")

    current = db.get_session(session_id) or {}
    imported_title = str(current.get("title") or "").strip()
    if not imported_title:
        for attempt in range(1, 10_001):
            candidate = _shared_title_candidate(title, attempt)
            try:
                if db.set_session_title(session_id, candidate):
                    imported_title = candidate
                    break
            except ValueError as exc:
                if "already in use" not in str(exc):
                    raise
        if not imported_title:
            raise ValueError("Unable to allocate a title for the shared chat")

    return {
        "session_id": session_id,
        "title": imported_title,
        "message_count": len(persisted_messages),
    }


def execute(db: SessionDB, method: str, kwargs: dict[str, Any]) -> Any:
    if method == "import_shared_session":
        return _import_shared_session(db, kwargs)
    if method == "get_logical_session_context":
        return _get_logical_session_context(db, **kwargs)
    if method == "list_sessions_rich":
        return db.list_sessions_rich(**kwargs)
    if method == "get_session":
        return db.get_session(str(kwargs.get("session_id") or "").strip())
    if method == "resolve_session_id":
        return db.resolve_session_id(
            str(kwargs.get("session_id_or_prefix") or "").strip()
        )
    if method == "get_compression_tip":
        return db.get_compression_tip(str(kwargs.get("session_id") or "").strip())
    if method == "get_messages":
        return db.get_messages(str(kwargs.get("session_id") or "").strip())
    if method == "set_session_title":
        return db.set_session_title(
            str(kwargs.get("session_id") or "").strip(),
            str(kwargs.get("title") or ""),
        )
    if method == "delete_session":
        return db.delete_session(str(kwargs.get("session_id") or "").strip())
    raise RuntimeError(f"Unsupported session DB method: {method}")


def main() -> int:
    db: SessionDB | None = None
    try:
        db_path = Path(sys.argv[1])
        method = str(sys.argv[2] or "").strip()
        kwargs = json.loads(sys.stdin.read() or "{}")
        if not isinstance(kwargs, dict):
            raise RuntimeError("Session DB kwargs must be a JSON object")
        db = SessionDB(
            db_path=db_path,
            read_only=method in READ_ONLY_METHODS and db_path.exists(),
        )
        result = execute(db, method, kwargs)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "type": type(exc).__name__},
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())

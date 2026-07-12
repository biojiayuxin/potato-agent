from __future__ import annotations

import json
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


def execute(db: SessionDB, method: str, kwargs: dict[str, Any]) -> Any:
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
        kwargs = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
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

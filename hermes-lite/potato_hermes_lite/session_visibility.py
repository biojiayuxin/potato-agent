"""Durable UI visibility, independent of Hermes' mutable session ancestry.

Internal transcripts remain available to the agent and to administrative reads.
The marker deliberately survives deletion: Interface may still have a cached
transcript for that ID. Existing orphans require an explicitly reviewed ID;
their prompts, titles, and lack of a final answer are not reliable evidence.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from hermes_state import SessionDB as HermesSessionDB

_TABLE = "potato_session_visibility"


def _public_child(child: str, parent: str) -> str:
    return f"""(
        json_extract(CASE WHEN json_valid({child}.model_config)
            THEN {child}.model_config ELSE '{{}}' END, '$._branched_from') IS NOT NULL
        OR ({parent}.end_reason IN ('compression', 'branched')
            AND {parent}.ended_at > 0
            AND {child}.started_at >= {parent}.ended_at)
    )"""


def _has_markers(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (_TABLE,)
        ).fetchone()
        is not None
    )


def _internal_cte(conn: sqlite3.Connection) -> str:
    has_markers = _has_markers(conn)
    persisted = (
        f"SELECT session_id FROM {_TABLE} WHERE kind = 'internal'"
        if has_markers
        else "SELECT NULL WHERE 0"
    )
    known_public = _known_public("s", has_markers)
    return f"""WITH RECURSIVE internal(session_id) AS (
        {persisted}
        UNION
        SELECT s.id FROM sessions s
        LEFT JOIN sessions p ON p.id = s.parent_session_id
        WHERE COALESCE(s.parent_session_id, '') != ''
          AND NOT {known_public}
          AND NOT COALESCE({_public_child('s', 'p')}, 0)
        UNION
        SELECT s.id FROM sessions s
        JOIN internal i ON s.parent_session_id = i.session_id
    )"""


def _known_public(child: str, has_markers: bool) -> str:
    if not has_markers:
        return "0"
    return f"""EXISTS (SELECT 1 FROM {_TABLE} v
        WHERE v.session_id = {child}.id AND v.kind = 'user')"""


def internal_session_ids(db: Any) -> set[str]:
    """Also recognize linked legacy subagents before a writable upgrade."""
    conn = getattr(db, "_conn", None)
    if conn is None:
        return set()
    with db._lock:
        return {
            row[0]
            for row in conn.execute(
                _internal_cte(conn) + " SELECT session_id FROM internal"
            )
        }


def is_internal_session(db: Any, session_id: str) -> bool:
    """Walk only this session's ancestry; keep live polling inexpensive."""
    conn = getattr(db, "_conn", None)
    if conn is None:
        return False
    with db._lock:
        has_markers = _has_markers(conn)
        marker_check = (
            f"""EXISTS (SELECT 1 FROM {_TABLE} i JOIN lineage l ON i.session_id = l.id
                WHERE i.kind = 'internal')"""
            if has_markers
            else "0"
        )
        return bool(
            conn.execute(
                f"""WITH RECURSIVE lineage(id) AS (
                SELECT ?
                UNION
                SELECT s.parent_session_id FROM sessions s
                JOIN lineage l ON s.id = l.id
                WHERE COALESCE(s.parent_session_id, '') != ''
            )
            SELECT {marker_check} OR EXISTS (
                SELECT 1 FROM lineage l JOIN sessions s ON s.id = l.id
                LEFT JOIN sessions p ON p.id = s.parent_session_id
                WHERE COALESCE(s.parent_session_id, '') != ''
                  AND NOT {_known_public('s', has_markers)}
                  AND NOT COALESCE({_public_child('s', 'p')}, 0)
            )""",
                (session_id,),
            ).fetchone()[0]
        )


def _backfill(conn: sqlite3.Connection) -> None:
    conn.execute(
        _internal_cte(conn)
        + f""" INSERT INTO {_TABLE} (session_id, parent_session_id, kind, reason)
        SELECT i.session_id, s.parent_session_id, 'internal', 'ancestry'
        FROM internal i JOIN sessions s ON s.id = i.session_id WHERE 1
        ON CONFLICT(session_id) DO UPDATE SET kind = 'internal'
        WHERE kind != 'internal'"""
    )
    # Remember legitimate continuations too: reopening their parent later must
    # not reclassify an existing user conversation as delegation.
    conn.execute(
        f"""INSERT OR IGNORE INTO {_TABLE} (session_id, parent_session_id, kind, reason)
        SELECT id, parent_session_id, 'user', 'ancestry' FROM sessions"""
    )


def _install(conn: sqlite3.Connection) -> None:
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
        session_id TEXT PRIMARY KEY,
        parent_session_id TEXT,
        kind TEXT NOT NULL CHECK (kind IN ('user', 'internal')),
        reason TEXT NOT NULL
    )""")
    # DB triggers cover writes from inherited Hermes connections too, without
    # changing delegation, compression, or deletion orchestration.
    for event, suffix in (
        ("INSERT", "insert"),
        ("UPDATE OF parent_session_id", "reparent"),
    ):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS potato_session_visibility_{suffix}
            AFTER {event} ON sessions
            BEGIN
                INSERT INTO {_TABLE} (session_id, parent_session_id, kind, reason)
                VALUES (NEW.id, NEW.parent_session_id,
                    CASE WHEN COALESCE(NEW.parent_session_id, '') != '' AND (
                        EXISTS (SELECT 1 FROM {_TABLE}
                            WHERE session_id = NEW.parent_session_id AND kind = 'internal')
                        OR NOT COALESCE((SELECT {_public_child('NEW', 'p')}
                            FROM (SELECT 1) LEFT JOIN sessions p ON p.id = NEW.parent_session_id), 0)
                    ) THEN 'internal' ELSE 'user' END, 'ancestry')
                ON CONFLICT(session_id) DO UPDATE SET kind = 'internal'
                    WHERE excluded.kind = 'internal' AND kind != 'internal';
            END""")
    _backfill(conn)


class SessionDB(HermesSessionDB):
    """Lite storage adapter. Raw session/message access retains Hermes semantics."""

    def __init__(self, db_path: Path | None = None, read_only: bool = False):
        super().__init__(db_path=db_path, read_only=read_only)
        if not read_only:
            try:
                self._execute_write(_install)
            except Exception:
                self.close()
                raise

    def get_internal_session_ids(self) -> set[str]:
        return internal_session_ids(self)

    def is_internal_session(self, session_id: str) -> bool:
        return is_internal_session(self, session_id)

    def list_sessions_rich(self, *args, **kwargs) -> list[dict[str, Any]]:
        return list_visible_sessions(self, super().list_sessions_rich, *args, **kwargs)

    def mark_internal_sessions(self, session_ids: list[str]) -> None:
        def mark(conn):
            for session_id in session_ids:
                row = conn.execute(
                    "SELECT parent_session_id FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"Session not found: {session_id}")
                conn.execute(
                    f"""INSERT INTO {_TABLE} VALUES (?, ?, 'internal', 'reviewed')
                    ON CONFLICT(session_id) DO UPDATE SET kind = 'internal', reason = 'reviewed'""",
                    (session_id, row[0]),
                )
            _backfill(conn)

        self._execute_write(mark)


def list_visible_sessions(db, list_sessions, *args, **kwargs) -> list[dict[str, Any]]:
    hidden = internal_session_ids(db)
    if not hidden:
        rows = list_sessions(*args, **kwargs)
        # A writer may have created/orphaned a child while fetching the page.
        hidden = internal_session_ids(db)
        if not hidden:
            return rows
    # Bind the inherited signature so both positional and keyword callers keep
    # their contract. Filter before applying the caller's visible-page offset.
    import inspect

    bound = inspect.signature(HermesSessionDB.list_sessions_rich).bind(
        db, *args, **kwargs
    )
    bound.apply_defaults()
    options = dict(bound.arguments)
    options.pop("self")
    limit = options.pop("limit")
    offset = max(0, options.pop("offset"))
    if limit == 0:
        return []
    batch_size = max(64, limit)
    visible: list[dict[str, Any]] = []
    raw_offset = 0
    while True:
        rows = list_sessions(limit=batch_size, offset=raw_offset, **options)
        hidden.update(internal_session_ids(db))
        visible.extend(
            row
            for row in rows
            if row["id"] not in hidden and row.get("_lineage_root_id") not in hidden
        )
        visible = [
            row
            for row in visible
            if row["id"] not in hidden and row.get("_lineage_root_id") not in hidden
        ]
        if len(rows) < batch_size or (limit > 0 and len(visible) >= offset + limit):
            return visible[offset:] if limit < 0 else visible[offset : offset + limit]
        raw_offset += len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hide reviewed internal session IDs")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--mark-internal", nargs="+", required=True, metavar="SESSION_ID"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Persist markers (default: preview only)"
    )
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("Session database does not exist")
    db = SessionDB(args.db, read_only=True)
    try:
        if any(db.get_session(sid) is None for sid in args.mark_internal):
            parser.error("One or more session IDs do not exist")
    finally:
        db.close()
    if args.apply:
        db = SessionDB(args.db)
        try:
            db.mark_internal_sessions(args.mark_internal)
        finally:
            db.close()
    print(json.dumps({"applied": args.apply, "session_ids": args.mark_internal}))


if __name__ == "__main__":
    main()

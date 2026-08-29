from __future__ import annotations

import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_LITE_ROOT = REPO_ROOT / "hermes-lite"
for path in (REPO_ROOT, HERMES_LITE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hermes_state import SessionDB

from interface.session_db_rpc import execute


IMPORTED_MESSAGES = [
    {
        "role": "user",
        "content": "Shared question",
        "attachments": [{"path": "/private/input.csv"}],
    },
    {
        "role": "assistant",
        "content": "Shared answer",
        "reasoning": "private reasoning",
        "tool_calls": [{"function": {"name": "read_file"}}],
    },
]


def _import_payload(session_id: str = "shared-session-1") -> dict[str, object]:
    return {
        "session_id": session_id,
        "title": "Source conversation",
        "messages": IMPORTED_MESSAGES,
    }


def _raw_message_rows(db_path: Path, session_id: str) -> list[sqlite3.Row]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return list(
            conn.execute(
                "select role, content, reasoning, tool_calls, tool_name "
                "from messages where session_id = ? order by id",
                (session_id,),
            )
        )


def test_import_shared_session_is_idempotent_and_persists_visible_text_only(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        first = execute(db, "import_shared_session", _import_payload())
        db.append_message("shared-session-1", "user", "A follow-up after import")
        second = execute(db, "import_shared_session", _import_payload())
        session = db.get_session("shared-session-1")
        messages = db.get_messages("shared-session-1")
    finally:
        db.close()

    assert first == {
        "session_id": "shared-session-1",
        "title": "Source conversation (shared)",
        "message_count": 2,
    }
    assert second == {
        "session_id": "shared-session-1",
        "title": "Source conversation (shared)",
        "message_count": 3,
    }
    assert session is not None
    assert session["source"] == "tui"
    assert session["message_count"] == 3
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "Shared question"),
        ("assistant", "Shared answer"),
        ("user", "A follow-up after import"),
    ]

    rows = _raw_message_rows(db_path, "shared-session-1")
    assert len(rows) == 3
    assert all(row["reasoning"] is None for row in rows)
    assert all(row["tool_calls"] is None for row in rows)
    assert all(row["tool_name"] is None for row in rows)
    database_bytes = db_path.read_bytes()
    assert b"private reasoning" not in database_bytes
    assert b"/private/input.csv" not in database_bytes


def test_concurrent_import_retries_do_not_duplicate_the_session_or_messages(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    initializer = SessionDB(db_path=db_path)
    initializer.close()
    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def import_once(_index: int) -> dict[str, object]:
        db = SessionDB(db_path=db_path)
        try:
            barrier.wait(timeout=10)
            return execute(db, "import_shared_session", _import_payload())
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(import_once, range(worker_count)))

    assert all(result == results[0] for result in results)
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "select count(*) from sessions where id = 'shared-session-1'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from messages where session_id = 'shared-session-1'"
        ).fetchone()[0] == 2
        assert conn.execute(
            "select message_count from sessions where id = 'shared-session-1'"
        ).fetchone()[0] == 2


def test_import_rejects_unsupported_or_non_text_messages(tmp_path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        with pytest.raises(ValueError):
            execute(
                db,
                "import_shared_session",
                {
                    "session_id": "tool-message",
                    "title": "Invalid",
                    "messages": [{"role": "tool", "content": "private output"}],
                },
            )
        with pytest.raises(ValueError):
            execute(
                db,
                "import_shared_session",
                {
                    "session_id": "structured-message",
                    "title": "Invalid",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "url": "private-file-url"}
                            ],
                        }
                    ],
                },
            )
        with pytest.raises(ValueError):
            execute(
                db,
                "import_shared_session",
                {
                    "session_id": "snapshot-too-large",
                    "title": "Invalid",
                    "messages": [
                        {"role": "assistant", "content": "x" * 60_000}
                    ]
                    * 9,
                },
            )
        with pytest.raises(ValueError):
            execute(
                db,
                "import_shared_session",
                {
                    "session_id": "no-visible-text",
                    "title": "Invalid",
                    "messages": [{"role": "user", "content": "  \n\t "}],
                },
            )
        with pytest.raises(ValueError):
            execute(
                db,
                "import_shared_session",
                {
                    "session_id": "too-many",
                    "title": "Invalid",
                    "messages": [{"role": "user", "content": "x"}] * 201,
                },
            )
        with pytest.raises(ValueError):
            execute(
                db,
                "import_shared_session",
                {
                    "session_id": "too-large",
                    "title": "Invalid",
                    "messages": [
                        {"role": "assistant", "content": "x" * (64 * 1024 + 1)}
                    ],
                },
            )
    finally:
        db.close()


def test_import_does_not_overwrite_an_existing_non_tui_session(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        db.create_session("shared-session-1", "api")
        db.append_message("shared-session-1", "user", "original content")
        with pytest.raises(ValueError, match="already in use"):
            execute(db, "import_shared_session", _import_payload())
        messages = db.get_messages("shared-session-1")
    finally:
        db.close()

    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "original content")
    ]


def test_import_does_not_overwrite_a_different_existing_tui_transcript(
    tmp_path,
) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("shared-session-1", "tui")
        db.append_message("shared-session-1", "user", "different content")
        with pytest.raises(ValueError, match="different messages"):
            execute(db, "import_shared_session", _import_payload())
        messages = db.get_messages("shared-session-1")
    finally:
        db.close()

    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "different content")
    ]


def test_import_allocates_a_stable_unique_title(tmp_path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("existing-session", "tui")
        assert db.set_session_title("existing-session", "Source conversation (shared)")

        first = execute(db, "import_shared_session", _import_payload("import-1"))
        retried = execute(db, "import_shared_session", _import_payload("import-1"))
        second = execute(db, "import_shared_session", _import_payload("import-2"))
    finally:
        db.close()

    assert first["title"] == "Source conversation (shared 2)"
    assert retried["title"] == first["title"]
    assert second["title"] == "Source conversation (shared 3)"

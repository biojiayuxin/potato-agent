from __future__ import annotations

import tempfile
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.display_store import (
    append_session_event,
    ensure_display_store,
    get_display_session_meta,
    get_live_session_state,
    list_live_session_states,
    save_display_messages,
    save_live_session_state,
)


def test_live_state_round_trip() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-display-store-test-")) / "interface.db"
    ensure_display_store(db_path)

    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="running",
        pending_approval=None,
        last_error="",
        last_event_seq=3,
        db_path=db_path,
    )

    row = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert row is not None
    assert row["run_id"] == "run-1"
    assert row["live_session_id"] == "live-1"
    assert row["status"] == "running"
    assert row["last_event_seq"] == 3


def test_live_state_list_and_event_append() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-display-store-test-")) / "interface.db"
    ensure_display_store(db_path)

    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="awaiting_approval",
        pending_approval={"command": "rm -rf /tmp/x", "description": "need approval"},
        last_error="",
        last_event_seq=5,
        db_path=db_path,
    )
    append_session_event(
        "user-1",
        "session-1",
        run_id="run-1",
        seq=5,
        event_type="approval.request",
        payload={"command": "rm -rf /tmp/x"},
        db_path=db_path,
    )

    rows = list_live_session_states("user-1", db_path=db_path)
    assert "session-1" in rows
    assert rows["session-1"]["status"] == "awaiting_approval"
    assert rows["session-1"]["pending_approval"]["command"] == "rm -rf /tmp/x"


def test_display_draft_title_is_sticky_after_first_write() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-display-store-test-")) / "interface.db"
    ensure_display_store(db_path)

    save_display_messages(
        "user-1",
        "session-1",
        [{"id": "m1", "role": "user", "content": "first", "done": True}],
        draft_title="first title",
        db_path=db_path,
    )
    save_display_messages(
        "user-1",
        "session-1",
        [{"id": "m1", "role": "user", "content": "first", "done": True}],
        draft_title="second title",
        db_path=db_path,
    )

    meta = get_display_session_meta("user-1", "session-1", db_path=db_path)
    assert meta is not None
    assert meta["draft_title"] == "first title"


def run() -> None:
    test_live_state_round_trip()
    test_live_state_list_and_event_append()
    test_display_draft_title_is_sticky_after_first_write()


if __name__ == "__main__":
    run()

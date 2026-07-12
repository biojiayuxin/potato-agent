from __future__ import annotations

import tempfile
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.display_store import (
    append_session_event,
    create_turn_submission_receipt,
    cleanup_turn_submission_receipts,
    ensure_display_store,
    fail_turn_submission_receipt,
    find_live_session_id_by_run_id,
    finish_turn_submission_receipt,
    get_display_session_meta,
    get_live_poll_snapshot,
    get_live_session_state,
    list_live_session_states,
    mark_active_live_session_states_failed,
    save_display_messages,
    save_live_session_state,
    get_turn_submission_receipt,
    heartbeat_turn_submission_receipt,
)


def test_live_state_round_trip() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-display-store-test-")) / "interface.db"
    ensure_display_store(db_path)

    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        tip_session_id="tip-1",
        assistant_message_id="assistant-1",
        status="running",
        pending_approval=None,
        last_error="",
        last_event_seq=3,
        last_workspace_event_seq=2,
        db_path=db_path,
    )

    row = get_live_session_state("user-1", "session-1", db_path=db_path)
    assert row is not None
    assert row["run_id"] == "run-1"
    assert row["live_session_id"] == "live-1"
    assert row["tip_session_id"] == "tip-1"
    assert row["status"] == "running"
    assert row["last_event_seq"] == 3
    assert row["last_workspace_event_seq"] == 2


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


def test_active_live_state_cleanup_marks_stale_rows_failed() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-display-store-test-")) / "interface.db"
    ensure_display_store(db_path)

    save_live_session_state(
        "user-1",
        "running-session",
        run_id="run-running",
        live_session_id="live-running",
        assistant_message_id="assistant-running",
        status="running",
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "done-session",
        run_id="run-done",
        live_session_id="live-done",
        assistant_message_id="assistant-done",
        status="completed",
        db_path=db_path,
    )

    assert mark_active_live_session_states_failed("stale", db_path=db_path) == 1

    stale = get_live_session_state("user-1", "running-session", db_path=db_path)
    assert stale is not None
    assert stale["status"] == "failed"
    assert stale["last_error"] == "stale"
    assert stale["finished_at"] > 0

    done = get_live_session_state("user-1", "done-session", db_path=db_path)
    assert done is not None
    assert done["status"] == "completed"


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


def test_live_poll_snapshot_omits_unchanged_transcript() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-display-store-test-")) / "interface.db"
    ensure_display_store(db_path)
    save_display_messages(
        "user-1",
        "session-1",
        [{"id": "m1", "role": "assistant", "content": "working"}],
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="m1",
        status="running",
        last_event_seq=3,
        db_path=db_path,
    )
    append_session_event(
        "user-1",
        "session-1",
        run_id="run-1",
        seq=1,
        event_type="message.delta",
        db_path=db_path,
    )
    text_only = get_live_poll_snapshot(
        "user-1",
        "session-1",
        after_run_id="run-1",
        after_event_seq=0,
        db_path=db_path,
    )
    append_session_event(
        "user-1",
        "session-1",
        run_id="run-1",
        seq=2,
        event_type="tool.complete",
        db_path=db_path,
    )
    save_live_session_state(
        "user-1",
        "session-1",
        last_workspace_event_seq=2,
        db_path=db_path,
    )

    initial = get_live_poll_snapshot(
        "user-1", "session-1", db_path=db_path
    )
    changed = get_live_poll_snapshot(
        "user-1",
        "session-1",
        after_run_id="run-1",
        after_event_seq=1,
        db_path=db_path,
    )
    unchanged = get_live_poll_snapshot(
        "user-1",
        "session-1",
        after_run_id="run-1",
        after_event_seq=3,
        db_path=db_path,
    )

    assert initial is not None
    assert initial["messages"][0]["content"] == "working"
    assert initial["file_tree_refresh"] is False
    assert text_only is not None
    assert text_only["file_tree_refresh"] is False
    assert changed is not None
    assert changed["file_tree_refresh"] is True
    assert unchanged is not None
    assert unchanged["messages"] is None
    assert unchanged["file_tree_refresh"] is False
    assert unchanged["live"]["last_event_seq"] == 3
    assert find_live_session_id_by_run_id(
        "user-1", "run-1", db_path=db_path
    ) == "session-1"


def test_turn_submission_receipt_tracks_pending_success_and_failure() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="potato-display-store-test-")) / "interface.db"
    ensure_display_store(db_path)

    pending = create_turn_submission_receipt(
        "user-1",
        "request-1",
        requested_session_id="draft",
        db_path=db_path,
    )
    duplicate = create_turn_submission_receipt(
        "user-1",
        "request-1",
        requested_session_id="draft",
        db_path=db_path,
    )

    assert pending["created"] is True
    assert pending["status"] == "pending"
    assert duplicate["created"] is False
    assert heartbeat_turn_submission_receipt(
        "user-1", "request-1", db_path=db_path
    ) is True
    assert finish_turn_submission_receipt(
        "user-1", "request-1", session_id="session-1", db_path=db_path
    ) is True
    submitted = get_turn_submission_receipt(
        "user-1", "request-1", db_path=db_path
    )
    assert submitted is not None
    assert submitted["status"] == "submitted"
    assert submitted["session_id"] == "session-1"

    create_turn_submission_receipt(
        "user-1",
        "request-2",
        requested_session_id="session-2",
        db_path=db_path,
    )
    assert fail_turn_submission_receipt(
        "user-1", "request-2", error_message="bridge failed", db_path=db_path
    ) is True
    failed = get_turn_submission_receipt(
        "user-1", "request-2", db_path=db_path
    )
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["last_error"] == "bridge failed"

    expiring = create_turn_submission_receipt(
        "user-1",
        "request-3",
        requested_session_id="draft",
        pending_timeout_seconds=1,
        db_path=db_path,
    )
    cleanup = cleanup_turn_submission_receipts(
        now=int(expiring["expires_at"]),
        db_path=db_path,
    )
    expired = get_turn_submission_receipt(
        "user-1", "request-3", db_path=db_path
    )
    assert cleanup["expired"] == 1
    assert expired is not None
    assert expired["status"] == "failed"

    cleanup = cleanup_turn_submission_receipts(
        retention_seconds=1,
        now=int(expired["updated_at"]) + 1,
        db_path=db_path,
    )
    assert cleanup["deleted"] >= 1
    assert get_turn_submission_receipt(
        "user-1", "request-3", db_path=db_path
    ) is None


def run() -> None:
    test_live_state_round_trip()
    test_live_state_list_and_event_append()
    test_active_live_state_cleanup_marks_stale_rows_failed()
    test_display_draft_title_is_sticky_after_first_write()


if __name__ == "__main__":
    run()

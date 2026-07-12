from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.display_store import get_live_session_state, save_live_session_state
from interface.session_run_manager import SessionRunManager
from interface.tui_gateway_bridge import TuiGatewayBridgeError


def _save_pending_approval(db_path: Path) -> None:
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="awaiting_approval",
        pending_approval={
            "approval_id": "run-1:7",
            "command": "echo ok",
            "description": "Approve",
        },
        last_event_seq=7,
        db_path=db_path,
    )


def test_consecutive_approval_events_are_persisted_fifo(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="running",
        pending_approval=None,
        last_event_seq=6,
        db_path=db_path,
    )

    class Bridge:
        user_id = "user-1"

    async def scenario() -> None:
        manager = SessionRunManager(db_path=db_path)
        for seq, command in ((7, "first"), (8, "second")):
            await manager.handle_bridge_event(
                Bridge(),  # type: ignore[arg-type]
                {
                    "type": "approval.request",
                    "session_id": "live-1",
                    "persistent_session_id": "session-1",
                    "run_id": "run-1",
                    "seq": seq,
                    "payload": {"command": command, "description": command},
                },
            )

    asyncio.run(scenario())

    live_state = get_live_session_state(
        "user-1", "session-1", db_path=db_path
    )
    assert live_state is not None
    pending = live_state["pending_approval"]
    assert pending["approval_id"] == "run-1:7"
    assert [item["approval_id"] for item in pending["queue"]] == [
        "run-1:7",
        "run-1:8",
    ]


def test_approval_response_moves_unchanged_request_back_to_running(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _save_pending_approval(db_path)

    class Bridge:
        async def rpc(self, method: str, params: dict) -> dict:
            return {"resolved": 1}

    manager = SessionRunManager(db_path=db_path)
    asyncio.run(
        manager.respond_to_approval(
            bridge=Bridge(),  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
            choice="once",
            approval_id="run-1:7",
        )
    )

    live_state = get_live_session_state(
        "user-1", "session-1", db_path=db_path
    )
    assert live_state is not None
    assert live_state["status"] == "running"
    assert live_state["pending_approval"] is None


def test_approval_response_does_not_reopen_a_completed_run(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _save_pending_approval(db_path)

    class CompletingBridge:
        async def rpc(self, method: str, params: dict) -> dict:
            assert method == "approval.respond"
            assert params == {"session_id": "live-1", "choice": "once"}
            save_live_session_state(
                "user-1",
                "session-1",
                run_id="run-1",
                live_session_id="live-1",
                assistant_message_id="assistant-1",
                status="completed",
                pending_approval=None,
                last_event_seq=8,
                finished_at=1,
                db_path=db_path,
            )
            return {"resolved": 1}

    manager = SessionRunManager(db_path=db_path)
    result = asyncio.run(
        manager.respond_to_approval(
            bridge=CompletingBridge(),  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
            choice="once",
            approval_id="run-1:7",
        )
    )

    assert result == {"resolved": 1}
    live_state = get_live_session_state(
        "user-1", "session-1", db_path=db_path
    )
    assert live_state is not None
    assert live_state["status"] == "completed"
    assert live_state["pending_approval"] is None
    assert live_state["last_event_seq"] == 8


def test_approval_response_journal_stays_with_original_run(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _save_pending_approval(db_path)

    class ReplacedRunBridge:
        async def rpc(self, method: str, params: dict) -> dict:
            save_live_session_state(
                "user-1",
                "session-1",
                run_id="run-2",
                live_session_id="live-2",
                assistant_message_id="assistant-2",
                status="running",
                pending_approval=None,
                last_event_seq=1,
                db_path=db_path,
            )
            return {"resolved": 1}

    manager = SessionRunManager(db_path=db_path)
    asyncio.run(
        manager.respond_to_approval(
            bridge=ReplacedRunBridge(),  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
            choice="once",
            approval_id="run-1:7",
        )
    )

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """
            SELECT run_id, seq, payload_json
            FROM session_event_journal
            WHERE event_type = 'approval.respond'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert row[0] == "run-1"
    assert row[1] == 7
    assert json.loads(row[2])["approval_id"] == "run-1:7"


def test_stale_approval_token_is_rejected_before_gateway_rpc(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _save_pending_approval(db_path)

    class Bridge:
        async def rpc(self, method: str, params: dict) -> dict:
            raise AssertionError("stale approval must not reach the gateway")

    manager = SessionRunManager(db_path=db_path)
    try:
        asyncio.run(
            manager.respond_to_approval(
                bridge=Bridge(),  # type: ignore[arg-type]
                user_id="user-1",
                session_id="session-1",
                choice="once",
                approval_id="run-1:6",
            )
        )
    except TuiGatewayBridgeError as exc:
        assert "no longer pending" in str(exc)
    else:
        raise AssertionError("stale approval token was accepted")


def test_zero_resolved_approvals_keeps_pending_state(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _save_pending_approval(db_path)

    class Bridge:
        async def rpc(self, method: str, params: dict) -> dict:
            return {"resolved": 0}

    manager = SessionRunManager(db_path=db_path)
    try:
        asyncio.run(
            manager.respond_to_approval(
                bridge=Bridge(),  # type: ignore[arg-type]
                user_id="user-1",
                session_id="session-1",
                choice="once",
                approval_id="run-1:7",
            )
        )
    except TuiGatewayBridgeError as exc:
        assert "no longer pending" in str(exc)
    else:
        raise AssertionError("zero resolved approvals was treated as success")

    live_state = get_live_session_state(
        "user-1", "session-1", db_path=db_path
    )
    assert live_state is not None
    assert live_state["status"] == "awaiting_approval"
    assert live_state["pending_approval"]["approval_id"] == "run-1:7"


def test_approval_response_advances_fifo_queue(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    queue = [
        {
            "approval_id": "run-1:7",
            "command": "first",
            "description": "First",
        },
        {
            "approval_id": "run-1:8",
            "command": "second",
            "description": "Second",
        },
    ]
    save_live_session_state(
        "user-1",
        "session-1",
        run_id="run-1",
        live_session_id="live-1",
        assistant_message_id="assistant-1",
        status="awaiting_approval",
        pending_approval={**queue[0], "queue": queue},
        last_event_seq=8,
        db_path=db_path,
    )

    class Bridge:
        async def rpc(self, method: str, params: dict) -> dict:
            return {"resolved": 1}

    manager = SessionRunManager(db_path=db_path)
    asyncio.run(
        manager.respond_to_approval(
            bridge=Bridge(),  # type: ignore[arg-type]
            user_id="user-1",
            session_id="session-1",
            choice="once",
            approval_id="run-1:7",
        )
    )

    live_state = get_live_session_state(
        "user-1", "session-1", db_path=db_path
    )
    assert live_state is not None
    assert live_state["status"] == "awaiting_approval"
    assert live_state["pending_approval"]["approval_id"] == "run-1:8"
    assert len(live_state["pending_approval"]["queue"]) == 1

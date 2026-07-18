from __future__ import annotations

from tools import approval


def test_exact_approval_id_resolves_only_the_matching_queue_entry() -> None:
    session_key = "approval-token-test"
    first = approval._ApprovalEntry({"command": "first"})
    second = approval._ApprovalEntry({"command": "second"})
    approval._gateway_queues[session_key] = [first, second]
    try:
        assert first.approval_id != second.approval_id
        assert first.data["approval_id"] == first.approval_id
        assert second.data["approval_id"] == second.approval_id

        assert approval.resolve_gateway_approval(
            session_key,
            "deny",
            approval_id=second.approval_id,
        ) == 1
        assert second.event.is_set()
        assert second.result == "deny"
        assert not first.event.is_set()
        assert approval._gateway_queues[session_key] == [first]
    finally:
        approval._gateway_queues.pop(session_key, None)
        approval._gateway_resolved.pop(session_key, None)


def test_unknown_or_repeated_approval_id_never_consumes_the_queue_head() -> None:
    session_key = "approval-token-retry-test"
    first = approval._ApprovalEntry({"command": "first"})
    second = approval._ApprovalEntry({"command": "second"})
    approval._gateway_queues[session_key] = [first, second]
    try:
        assert approval.resolve_gateway_approval(
            session_key,
            "once",
            approval_id="unknown-token",
        ) == 0
        assert approval._gateway_queues[session_key] == [first, second]

        assert approval.resolve_gateway_approval(
            session_key,
            "once",
            approval_id=first.approval_id,
        ) == 1
        assert approval.resolve_gateway_approval(
            session_key,
            "once",
            approval_id=first.approval_id,
        ) == 1
        assert approval.resolve_gateway_approval(
            session_key,
            "always",
            approval_id=first.approval_id,
        ) == 0
        assert approval._gateway_queues[session_key] == [second]
        assert not second.event.is_set()
    finally:
        approval._gateway_queues.pop(session_key, None)
        approval._gateway_resolved.pop(session_key, None)

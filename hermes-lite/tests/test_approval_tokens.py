from __future__ import annotations

import threading

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


def test_gateway_timeout_emits_expiration_for_exact_request(monkeypatch) -> None:
    session_key = "approval-expiration-test"
    requests: list[dict] = []
    lifecycle: list[tuple[str, dict]] = []
    approval.register_gateway_notify(
        session_key,
        requests.append,
        lifecycle_cb=lambda event_type, data: lifecycle.append((event_type, data)),
    )
    monkeypatch.setattr(
        approval,
        "_get_approval_config",
        lambda: {"gateway_timeout": 0},
    )
    try:
        result = approval._await_gateway_decision(
            session_key,
            requests.append,
            {
                "command": "rm -rf /tmp/example",
                "description": "recursive delete",
                "pattern_key": "rm_rf",
                "pattern_keys": ["rm_rf"],
            },
        )

        assert result == {"resolved": False, "choice": None}
        assert len(requests) == 1
        approval_id = requests[0]["approval_id"]
        assert lifecycle == [
            (
                "approval.expired",
                {**requests[0], "outcome": "timeout"},
            )
        ]
        assert approval.resolve_gateway_approval(
            session_key,
            "once",
            approval_id=approval_id,
        ) == 0
        assert not approval.has_blocking_approval(session_key)
    finally:
        approval.unregister_gateway_notify(session_key)


def test_gateway_resolution_wins_atomically_at_timeout_boundary(monkeypatch) -> None:
    session_key = "approval-timeout-boundary-test"
    lifecycle: list[tuple[str, dict]] = []
    captured: dict = {}

    def resolve_during_notify(data: dict) -> None:
        captured.update(data)
        assert approval.resolve_gateway_approval(
            session_key,
            "once",
            approval_id=data["approval_id"],
        ) == 1

    approval.register_gateway_notify(
        session_key,
        resolve_during_notify,
        lifecycle_cb=lambda event_type, data: lifecycle.append((event_type, data)),
    )
    monkeypatch.setattr(
        approval,
        "_get_approval_config",
        lambda: {"gateway_timeout": 0},
    )
    try:
        result = approval._await_gateway_decision(
            session_key,
            resolve_during_notify,
            {
                "command": "python -c 'print(1)'",
                "description": "script execution",
                "pattern_key": "python_c",
                "pattern_keys": ["python_c"],
            },
        )

        assert captured["approval_id"]
        assert result == {"resolved": True, "choice": "once"}
        assert lifecycle == []
    finally:
        approval.unregister_gateway_notify(session_key)


def test_normal_gateway_resolution_does_not_emit_expiration(monkeypatch) -> None:
    session_key = "approval-normal-resolution-test"
    request_ready = threading.Event()
    request: dict = {}
    lifecycle: list[tuple[str, dict]] = []
    result: list[dict] = []

    def notify(data: dict) -> None:
        request.update(data)
        request_ready.set()

    approval.register_gateway_notify(
        session_key,
        notify,
        lifecycle_cb=lambda event_type, data: lifecycle.append((event_type, data)),
    )
    monkeypatch.setattr(
        approval,
        "_get_approval_config",
        lambda: {"gateway_timeout": 5},
    )
    waiter = threading.Thread(
        target=lambda: result.append(
            approval._await_gateway_decision(
                session_key,
                notify,
                {
                    "command": "python -c 'print(1)'",
                    "description": "script execution",
                    "pattern_key": "python_c",
                    "pattern_keys": ["python_c"],
                },
            )
        )
    )
    try:
        waiter.start()
        assert request_ready.wait(timeout=2)
        assert approval.resolve_gateway_approval(
            session_key,
            "deny",
            approval_id=request["approval_id"],
        ) == 1
        waiter.join(timeout=2)

        assert not waiter.is_alive()
        assert result == [{"resolved": True, "choice": "deny"}]
        assert lifecycle == []
    finally:
        approval.unregister_gateway_notify(session_key)
        waiter.join(timeout=2)

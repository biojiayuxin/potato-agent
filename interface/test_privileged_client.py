from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from interface.mapping import HermesTarget
from interface.privileged_client import PrivilegedClient


def test_privileged_client_uses_helper_when_not_root(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 1000)

    def fake_run_process_group(command, **kwargs):
        assert kwargs.get("timeout_seconds") == 70.0
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "result": {"status": "ready"}}),
            stderr="",
        )

    monkeypatch.setattr(
        "interface.privileged_client.run_process_group", fake_run_process_group
    )

    client = PrivilegedClient(helper_python="/opt/interface-env/bin/python")
    result = client.session_db_call("alice", "get_session", {"session_id": "s1"})

    assert result == {"status": "ready"}
    assert calls
    assert calls[0][:4] == [
        "sudo",
        "-n",
        "/opt/interface-env/bin/python",
        "-m",
    ]
    assert "interface.privileged_helper" in calls[0]
    assert "--username" in calls[0]
    assert "alice" in calls[0]


def test_tui_gateway_command_uses_exec_helper_when_not_root(monkeypatch) -> None:
    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 1000)
    monkeypatch.setenv("INTERFACE_PRIVILEGED_HELPER", "/usr/local/libexec/potato-agent-privileged-helper")
    target = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=Path("/home/hmx_alice"),
        hermes_home=Path("/home/hmx_alice/.hermes"),
        workdir=Path("/home/hmx_alice"),
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )

    command = PrivilegedClient().tui_gateway_command(target)

    assert command == [
        "sudo",
        "-n",
        "/usr/local/libexec/potato-agent-privileged-helper",
        "tui-gateway",
        "--username",
        "alice",
    ]


def test_has_active_background_processes_uses_helper_when_not_root(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 1000)

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "active": False}),
            stderr="",
        )

    monkeypatch.setattr("interface.privileged_client.subprocess.run", fake_run)

    active = PrivilegedClient(helper_python="/opt/interface-env/bin/python").has_active_background_processes("alice")

    assert active is False
    assert calls
    assert "has-background-jobs" in calls[0]
    assert "--username" in calls[0]
    assert "alice" in calls[0]


def test_stop_idle_runtime_uses_helper_when_not_root(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 1000)

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "stopped": True}),
            stderr="",
        )

    monkeypatch.setattr("interface.privileged_client.subprocess.run", fake_run)

    result = PrivilegedClient(helper_python="/opt/interface-env/bin/python").stop_idle_runtime(
        "alice",
        "user-1",
        idle_timeout_seconds=321,
    )

    assert result == {"stopped": True, "reason": ""}
    assert calls
    assert "stop-idle-runtime" in calls[0]
    assert "--username" in calls[0]
    assert "alice" in calls[0]
    assert "--user-id" in calls[0]
    assert "user-1" in calls[0]
    timeout_index = calls[0].index("--idle-timeout-seconds")
    assert calls[0][timeout_index + 1] == "321"


def test_stop_idle_runtime_rechecks_eligibility_inside_service_lock(monkeypatch) -> None:
    events: list[str] = []
    target = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=Path("/home/hmx_alice"),
        hermes_home=Path("/home/hmx_alice/.hermes"),
        workdir=Path("/home/hmx_alice"),
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )

    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 0)

    class FakeMappingStore:
        def __init__(self, path) -> None:
            self.path = path

        def get_target_by_username(self, username: str):
            assert username == "alice"
            return target

    @contextmanager
    def fake_service_lock(service_name: str):
        assert service_name == target.systemd_service
        events.append("lock_enter")
        try:
            yield
        finally:
            events.append("lock_exit")

    def fake_eligibility(user_id: str, *, idle_timeout_seconds: int):
        assert events == ["lock_enter"]
        assert user_id == "user-1"
        assert idle_timeout_seconds == 321
        events.append("eligibility")
        return {"eligible": False, "reason": "recent_activity"}

    monkeypatch.setattr("interface.privileged_client.MappingStore", FakeMappingStore)
    monkeypatch.setattr(
        "interface.privileged_client.service_operation_lock",
        fake_service_lock,
    )
    monkeypatch.setattr(
        "interface.privileged_client.get_runtime_idle_eligibility",
        fake_eligibility,
    )
    monkeypatch.setattr(
        "interface.privileged_client.stop_service",
        lambda service_name: events.append("stop"),
    )

    result = PrivilegedClient().stop_idle_runtime(
        "alice",
        "user-1",
        idle_timeout_seconds=321,
    )

    assert result == {"stopped": False, "reason": "recent_activity"}
    assert events == ["lock_enter", "eligibility", "lock_exit"]


def test_stop_idle_runtime_rechecks_after_service_probe(monkeypatch) -> None:
    target = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=Path("/home/hmx_alice"),
        hermes_home=Path("/home/hmx_alice/.hermes"),
        workdir=Path("/home/hmx_alice"),
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )
    eligibility_calls = 0

    class FakeMappingStore:
        def __init__(self, path) -> None:
            pass

        def get_target_by_username(self, username: str):
            return target

    @contextmanager
    def fake_service_lock(service_name: str):
        yield

    def fake_eligibility(user_id: str, *, idle_timeout_seconds: int):
        nonlocal eligibility_calls
        eligibility_calls += 1
        if eligibility_calls == 1:
            return {"eligible": True, "reason": ""}
        return {"eligible": False, "reason": "recent_activity"}

    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 0)
    monkeypatch.setattr("interface.privileged_client.MappingStore", FakeMappingStore)
    monkeypatch.setattr(
        "interface.privileged_client.service_operation_lock", fake_service_lock
    )
    monkeypatch.setattr(
        "interface.privileged_client.get_runtime_idle_eligibility", fake_eligibility
    )
    monkeypatch.setattr(
        "interface.privileged_client.claim_runtime_sleep",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "interface.privileged_client.has_active_background_processes",
        lambda checked_target: False,
    )
    monkeypatch.setattr(
        "interface.privileged_client.is_service_active", lambda service: True
    )
    monkeypatch.setattr(
        "interface.privileged_client.stop_service",
        lambda service: (_ for _ in ()).throw(
            AssertionError("recent activity must prevent service stop")
        ),
    )

    result = PrivilegedClient().stop_idle_runtime(
        "alice", "user-1", idle_timeout_seconds=321
    )

    assert result == {"stopped": False, "reason": "recent_activity"}
    assert eligibility_calls == 2


def test_stop_idle_runtime_releases_claim_when_service_stop_fails(monkeypatch) -> None:
    target = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="hmx_alice",
        home_dir=Path("/home/hmx_alice"),
        hermes_home=Path("/home/hmx_alice/.hermes"),
        workdir=Path("/home/hmx_alice"),
        api_server_host="127.0.0.1",
        api_port=8655,
        api_key="sk-user",
        api_server_model_name="Hermes",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )
    released: list[tuple[str, str]] = []

    class FakeMappingStore:
        def __init__(self, path) -> None:
            pass

        def get_target_by_username(self, username: str):
            return target

    @contextmanager
    def fake_service_lock(service_name: str):
        yield

    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 0)
    monkeypatch.setattr("interface.privileged_client.MappingStore", FakeMappingStore)
    monkeypatch.setattr(
        "interface.privileged_client.service_operation_lock", fake_service_lock
    )
    monkeypatch.setattr(
        "interface.privileged_client.get_runtime_idle_eligibility",
        lambda *args, **kwargs: {"eligible": True, "reason": ""},
    )
    monkeypatch.setattr(
        "interface.privileged_client.has_active_background_processes",
        lambda target: False,
    )
    monkeypatch.setattr(
        "interface.privileged_client.claim_runtime_sleep",
        lambda *args, **kwargs: "claim-1",
    )
    monkeypatch.setattr(
        "interface.privileged_client.runtime_sleep_claim_is_valid",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "interface.privileged_client.release_runtime_sleep_claim",
        lambda user_id, *, claim_id: released.append((user_id, claim_id)),
    )
    monkeypatch.setattr(
        "interface.privileged_client.is_service_active", lambda service: True
    )
    monkeypatch.setattr(
        "interface.privileged_client.revoke_runtime_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("failed stop must not revoke the session")
        ),
    )
    monkeypatch.setattr(
        "interface.privileged_client.stop_service",
        lambda service: (_ for _ in ()).throw(RuntimeError("stop failed")),
    )

    with pytest.raises(RuntimeError, match="stop failed"):
        PrivilegedClient().stop_idle_runtime(
            "alice", "user-1", idle_timeout_seconds=321
        )

    assert released == [("user-1", "claim-1")]

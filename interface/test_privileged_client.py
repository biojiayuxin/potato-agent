from __future__ import annotations

import json
import subprocess
from pathlib import Path

from interface.mapping import HermesTarget
from interface.privileged_client import PrivilegedClient


def test_privileged_client_uses_helper_when_not_root(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("interface.privileged_client.os.geteuid", lambda: 1000)

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "result": {"status": "ready"}}),
            stderr="",
        )

    monkeypatch.setattr("interface.privileged_client.subprocess.run", fake_run)

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
    )

    assert result == {"stopped": True, "reason": ""}
    assert calls
    assert "stop-idle-runtime" in calls[0]
    assert "--username" in calls[0]
    assert "alice" in calls[0]
    assert "--user-id" in calls[0]
    assert "user-1" in calls[0]

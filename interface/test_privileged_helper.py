from __future__ import annotations

from pathlib import Path

from interface.mapping import HermesTarget
from interface import privileged_helper


def _target() -> HermesTarget:
    return HermesTarget(
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


def test_exec_tui_gateway_chdirs_to_target_workdir(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(privileged_helper.os, "chdir", lambda path: calls.append(("chdir", path)))
    monkeypatch.setattr(
        privileged_helper.os,
        "execvp",
        lambda binary, command: calls.append(("execvp", (binary, command))),
    )

    privileged_helper._exec_tui_gateway(_target())

    assert calls[0] == ("chdir", Path("/home/hmx_alice"))
    assert calls[1][0] == "execvp"
    binary, command = calls[1][1]
    assert binary == "runuser"
    assert command[:4] == ["runuser", "-u", "hmx_alice", "--"]
    assert "TERMINAL_CWD=/home/hmx_alice" in command

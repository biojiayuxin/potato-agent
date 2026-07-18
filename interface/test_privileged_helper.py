from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from interface import file_browser_policy
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
    assert "HERMES_DISABLE_LAZY_INSTALLS=1" in command
    assert "HERMES_SKIP_NODE_BOOTSTRAP=1" in command
    assert "HERMES_DISABLE_GATEWAY_PLATFORMS=1" in command
    assert "HERMES_DISABLE_MCP=1" in command
    assert "HERMES_DISABLE_CRON=1" in command
    assert "HERMES_DISABLE_KANBAN=1" in command
    assert "TERMINAL_ENV=local" in command
    assert "AGENT_BROWSER_ENGINE=chrome" in command
    assert "BROWSER_CDP_URL=" in command
    assert "CAMOFOX_URL=" in command
    assert (
        "HERMES_BUNDLED_SKILLS=/opt/potato-hermes-lite/current/share/hermes/skills"
        in command
    )
    assert (
        "HERMES_OPTIONAL_SKILLS=/opt/potato-hermes-lite/current/share/hermes/optional-skills"
        in command
    )
    assert (
        "HERMES_AGENT_BROWSER_BIN_DIR=/opt/potato-hermes-lite/current/browser/bin"
        in command
    )
    assert (
        "AGENT_BROWSER_EXECUTABLE_PATH="
        "/opt/potato-hermes-lite/current/browser/chrome/chrome-linux64/chrome"
        in command
    )
    assert "/opt/potato-hermes-lite/current/venv/bin/python3" in command


def test_helper_tui_gateway_command_injects_explicit_profile_path(monkeypatch) -> None:
    command = privileged_helper._tui_gateway_command(
        replace(
            _target(),
            runtime_profile_path=Path("/opt/potato/profile.yaml"),
            browser_cdp_url="ws://127.0.0.1:9222/devtools/browser/local",
        )
    )

    assert "HERMES_DISABLE_LAZY_INSTALLS=1" in command
    assert "HERMES_SKIP_NODE_BOOTSTRAP=1" in command
    assert "HERMES_DISABLE_GATEWAY_PLATFORMS=1" in command
    assert "HERMES_DISABLE_MCP=1" in command
    assert "HERMES_DISABLE_CRON=1" in command
    assert "HERMES_DISABLE_KANBAN=1" in command
    assert "TERMINAL_ENV=local" in command
    assert "AGENT_BROWSER_ENGINE=chrome" in command
    assert (
        "BROWSER_CDP_URL=ws://127.0.0.1:9222/devtools/browser/local"
        in command
    )
    assert "CAMOFOX_URL=" in command
    assert (
        "HERMES_BUNDLED_SKILLS=/opt/potato-hermes-lite/current/share/hermes/skills"
        in command
    )
    assert (
        "HERMES_OPTIONAL_SKILLS=/opt/potato-hermes-lite/current/share/hermes/optional-skills"
        in command
    )
    assert (
        "HERMES_AGENT_BROWSER_BIN_DIR=/opt/potato-hermes-lite/current/browser/bin"
        in command
    )
    assert (
        "AGENT_BROWSER_EXECUTABLE_PATH="
        "/opt/potato-hermes-lite/current/browser/chrome/chrome-linux64/chrome"
        in command
    )
    assert "HERMES_RUNTIME_PROFILE_PATH=/opt/potato/profile.yaml" in command


def test_session_db_rpc_source_is_passed_without_repo_path(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run_as_user(target, command, *, cwd=None, timeout_seconds=None):
        assert timeout_seconds == 60.0
        captured.extend(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "result": {"id": "session-1"}}),
            stderr="",
        )

    monkeypatch.setattr(privileged_helper, "_run_as_user", fake_run_as_user)

    result = privileged_helper._session_db_call(
        _target(), "get_session", {"session_id": "session-1"}
    )

    assert result == {"id": "session-1"}
    assert captured[1] == "-c"
    assert "from hermes_state import SessionDB" in captured[2]
    assert str(privileged_helper.USER_SESSION_DB_RPC_PATH) not in captured


def test_stop_idle_runtime_cli_rechecks_with_requested_timeout(
    monkeypatch,
    capsys,
) -> None:
    events: list[str] = []

    class FakeLock:
        def __enter__(self):
            events.append("lock_enter")

        def __exit__(self, exc_type, exc, traceback):
            events.append("lock_exit")

    def fake_eligibility(user_id: str, *, idle_timeout_seconds: int):
        assert events == ["lock_enter"]
        assert user_id == "user-1"
        assert idle_timeout_seconds == 321
        events.append("eligibility")
        return {"eligible": False, "reason": "active_lease"}

    monkeypatch.setattr(privileged_helper, "require_root", lambda: None)
    monkeypatch.setattr(privileged_helper, "require_binary", lambda binary: None)
    monkeypatch.setattr(privileged_helper, "_load_target", lambda username: _target())
    monkeypatch.setattr(
        privileged_helper,
        "service_operation_lock",
        lambda service_name: FakeLock(),
    )
    monkeypatch.setattr(
        privileged_helper,
        "get_runtime_idle_eligibility",
        fake_eligibility,
    )
    monkeypatch.setattr(
        privileged_helper,
        "stop_service",
        lambda service_name: events.append("stop"),
    )
    monkeypatch.setattr(
        privileged_helper.sys,
        "argv",
        [
            "privileged-helper",
            "stop-idle-runtime",
            "--username",
            "alice",
            "--user-id",
            "user-1",
            "--idle-timeout-seconds",
            "321",
        ],
    )

    assert privileged_helper.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "stopped": False, "reason": "active_lease"}
    assert events == ["lock_enter", "eligibility", "lock_exit"]


def test_stop_idle_runtime_cli_rechecks_after_service_probe(
    monkeypatch,
    capsys,
) -> None:
    eligibility_calls = 0

    class FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, traceback):
            return None

    def fake_eligibility(user_id: str, *, idle_timeout_seconds: int):
        nonlocal eligibility_calls
        eligibility_calls += 1
        if eligibility_calls == 1:
            return {"eligible": True, "reason": ""}
        return {"eligible": False, "reason": "recent_activity"}

    monkeypatch.setattr(privileged_helper, "require_root", lambda: None)
    monkeypatch.setattr(privileged_helper, "require_binary", lambda binary: None)
    monkeypatch.setattr(privileged_helper, "_load_target", lambda username: _target())
    monkeypatch.setattr(
        privileged_helper, "service_operation_lock", lambda service_name: FakeLock()
    )
    monkeypatch.setattr(
        privileged_helper, "get_runtime_idle_eligibility", fake_eligibility
    )
    monkeypatch.setattr(
        privileged_helper, "claim_runtime_sleep", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        privileged_helper, "has_active_background_processes", lambda target: False
    )
    monkeypatch.setattr(privileged_helper, "is_service_active", lambda service: True)
    monkeypatch.setattr(
        privileged_helper,
        "stop_service",
        lambda service: (_ for _ in ()).throw(
            AssertionError("recent activity must prevent service stop")
        ),
    )
    monkeypatch.setattr(
        privileged_helper.sys,
        "argv",
        [
            "privileged-helper",
            "stop-idle-runtime",
            "--username",
            "alice",
            "--user-id",
            "user-1",
            "--idle-timeout-seconds",
            "321",
        ],
    )

    assert privileged_helper.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "stopped": False, "reason": "recent_activity"}
    assert eligibility_calls == 2


def test_file_info_allows_only_configured_public_data_in_restricted_modes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    public_data = tmp_path / "public_data"
    public_data.mkdir()
    public_file = public_data / "dataset.tsv"
    public_file.write_text("gene\tvalue\n", encoding="utf-8")
    public_link = home / "public_data"
    public_link.symlink_to(public_data, target_is_directory=True)
    target = replace(
        _target(),
        home_dir=home,
        hermes_home=home / ".hermes",
        workdir=home,
    )
    monkeypatch.setattr(
        file_browser_policy,
        "DEFAULT_PUBLIC_DATA_PATH",
        public_data,
    )
    monkeypatch.setattr(
        privileged_helper,
        "_probe_path",
        lambda checked_target, path: {
            "exists": True,
            "is_file": True,
            "readable": True,
        },
    )

    info = privileged_helper._file_info(
        target,
        public_link / public_file.name,
        mode="home_and_public_data",
    )

    assert info["filename"] == public_file.name
    assert info["size"] == public_file.stat().st_size
    with pytest.raises(RuntimeError, match="outside the allowed browser roots"):
        privileged_helper._file_info(
            target,
            public_link / public_file.name,
            mode="home_only",
        )

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import types
from pathlib import Path

import pytest


def test_delegate_schema_and_registry_strip_legacy_acp_fields():
    from tools.delegate_tool import (
        DELEGATE_TASK_SCHEMA,
        _strip_model_hidden_task_fields,
    )

    properties = DELEGATE_TASK_SCHEMA["parameters"]["properties"]
    assert "acp_command" not in properties
    assert "acp_args" not in properties
    task_properties = properties["tasks"]["items"]["properties"]
    assert "acp_command" not in task_properties
    assert "acp_args" not in task_properties

    tasks = [{
        "goal": "sentinel",
        "acp_command": "/tmp/attacker",
        "acp_args": ["--run"],
        "context": "kept",
    }]
    assert _strip_model_hidden_task_fields(tasks) == [{
        "goal": "sentinel",
        "context": "kept",
    }]


def _rpc_round_trip(monkeypatch, token: str | None):
    import model_tools
    from tools.code_execution_tool import _rpc_server_loop

    dispatched = []

    def fake_dispatch(name, args, task_id=None):
        dispatched.append((name, args, task_id))
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools, "handle_function_call", fake_dispatch)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    thread = threading.Thread(
        target=_rpc_server_loop,
        args=(server, "task", [], [0], 5, frozenset({"terminal"}), "expected"),
        daemon=True,
    )
    thread.start()
    client = socket.create_connection(server.getsockname(), timeout=2)
    request = {"tool": "terminal", "args": {"command": "true"}}
    if token is not None:
        request["token"] = token
    client.sendall((json.dumps(request) + "\n").encode())
    response = b""
    while not response.endswith(b"\n"):
        response += client.recv(4096)
    client.close()
    thread.join(timeout=2)
    server.close()
    return json.loads(response), dispatched


@pytest.mark.parametrize("token", [None, "wrong"])
def test_execute_code_rpc_rejects_missing_or_wrong_token(monkeypatch, token):
    response, dispatched = _rpc_round_trip(monkeypatch, token)
    assert response == {"error": "Unauthorized RPC request"}
    assert dispatched == []


def test_execute_code_rpc_accepts_matching_token(monkeypatch):
    response, dispatched = _rpc_round_trip(monkeypatch, "expected")
    assert response == {"ok": True}
    assert dispatched == [("terminal", {"command": "true"}, "task")]


def test_generated_execute_code_clients_send_rpc_token():
    from tools.code_execution_tool import generate_hermes_tools_module

    for transport in ("socket", "file"):
        source = generate_hermes_tools_module(["terminal"], transport=transport)
        assert "HERMES_RPC_TOKEN" in source
        assert '"token"' in source


def test_subprocess_env_never_inherits_control_plane_or_dynamic_secrets(monkeypatch):
    from tools.environments.local import hermes_subprocess_env

    sentinels = {
        "INTERFACE_SESSION_SECRET": "interface-secret",
        "GITHUB_TOKEN": "github-secret",
        "TELEGRAM_BOT_TOKEN": "bot-secret",
        "MODAL_TOKEN_SECRET": "remote-secret",
        "VERTEX_CREDENTIALS_PATH": "/tmp/gcp.json",
        "AUXILIARY_VISION_API_KEY": "aux-secret",
        "AUXILIARY_VISION_BASE_URL": "http://private/v1",
        "GATEWAY_RELAY_ARBITRARY_TOKEN": "relay-secret",
    }
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)

    default = hermes_subprocess_env()
    inherited = hermes_subprocess_env(inherit_credentials=True)
    assert "OPENAI_API_KEY" not in default
    assert inherited["OPENAI_API_KEY"] == "provider-secret"
    for key in sentinels:
        assert key not in default
        assert key not in inherited


def test_env_passthrough_cannot_restore_dynamic_internal_secret(monkeypatch):
    from tools.environments.local import _sanitize_subprocess_env

    monkeypatch.setattr(
        "tools.env_passthrough.is_env_passthrough",
        lambda _key: True,
    )
    result = _sanitize_subprocess_env({
        "AUXILIARY_APPROVAL_API_KEY": "sentinel",
        "GATEWAY_RELAY_ID": "relay-id",
        "SAFE_VALUE": "kept",
    })
    assert result == {"SAFE_VALUE": "kept"}


def test_read_guard_is_case_insensitive_and_resolves_symlinks(tmp_path):
    from agent.file_safety import raise_if_read_blocked

    secret = tmp_path / ".ENV"
    secret.write_text("TOKEN=sentinel", encoding="utf-8")
    link = tmp_path / "image.png"
    link.symlink_to(secret)

    with pytest.raises(ValueError, match="secret-bearing"):
        raise_if_read_blocked(str(secret))
    with pytest.raises(ValueError, match="secret-bearing"):
        raise_if_read_blocked(str(link))


def test_vision_mime_probe_blocks_before_open(tmp_path):
    from tools.vision_tools import _detect_image_mime_type

    secret = tmp_path / ".env"
    secret.write_bytes(b"\x89PNG\r\n\x1a\nTOKEN=sentinel")
    with pytest.raises(ValueError, match="secret-bearing"):
        _detect_image_mime_type(secret)


def test_state_and_sessions_are_write_protected(monkeypatch, tmp_path):
    from agent import file_safety

    root = tmp_path / ".hermes"
    profile = root / "profiles" / "work"
    profile.mkdir(parents=True)
    monkeypatch.setattr(file_safety, "_hermes_home_path", lambda: profile)
    monkeypatch.setattr(file_safety, "_hermes_root_path", lambda: root)

    assert file_safety.is_write_denied(str(profile / "state.db"))
    assert file_safety.is_write_denied(str(profile / "sessions" / "x.json"))
    assert file_safety.is_write_denied(str(root / "state.db"))
    assert not file_safety.is_write_denied(str(tmp_path / "project" / "state.db"))


@pytest.mark.parametrize("recorded_start", [None, -1])
def test_process_recovery_marks_untrusted_pid_identity_lost(
    monkeypatch, tmp_path, recorded_start
):
    from tools import process_registry as module

    checkpoint = tmp_path / "processes.json"
    entry = {
        "session_id": "proc_untrusted",
        "command": "sleep 300",
        "pid": os.getpid(),
        "pid_scope": "host",
    }
    if recorded_start is not None:
        entry["pid_start_time"] = recorded_start
    checkpoint.write_text(json.dumps([entry]), encoding="utf-8")
    monkeypatch.setattr(module, "CHECKPOINT_PATH", checkpoint)

    registry = module.ProcessRegistry()
    assert registry.recover_from_checkpoint() == 0
    recovered = registry.get("proc_untrusted")
    assert recovered is not None
    assert recovered.lost is True
    assert registry.kill_process("proc_untrusted")["status"] == "lost"


def test_host_pid_termination_rechecks_identity_before_signaling(monkeypatch):
    from tools.process_registry import ProcessRegistry

    calls = iter([777, 778])
    monkeypatch.setattr(
        ProcessRegistry,
        "_get_host_pid_start_time",
        staticmethod(lambda _pid: next(calls)),
    )
    signaled = []

    class FakeProcess:
        def __init__(self, _pid):
            pass

        def children(self, recursive=False):
            assert recursive is True
            return []

        def terminate(self):
            signaled.append("parent")

    fake_psutil = types.SimpleNamespace(
        Process=FakeProcess,
        NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert ProcessRegistry._terminate_host_pid(1234, expected_start_time=777) is False
    assert signaled == []


def test_background_process_output_is_force_redacted(monkeypatch):
    from tools.process_registry import ProcessRegistry, ProcessSession

    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_secret",
        command="printenv",
        started_at=1.0,
        exited=True,
        output_buffer="OPENAI_API_KEY=sk-testabcdefghijklmnop",
    )
    registry._finished[session.id] = session

    result = registry.poll(session.id)
    assert "sk-testabcdefghijklmnop" not in result["output_preview"]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://[fe80::1%25eth0]/",
    ],
)
def test_metadata_floor_blocks_ip_hostname_and_scoped_ipv6(url):
    from tools.url_safety import is_always_blocked_url

    assert is_always_blocked_url(url) is True


def test_metadata_floor_still_allows_local_browser_private_addresses(
    monkeypatch, runtime_paths
):
    from tools import browser_tool

    monkeypatch.setattr(browser_tool, "_eval_ssrf_guard_active", lambda _task: False)
    assert browser_tool._browser_url_block_reason("http://127.0.0.1:8080", "task") is None
    assert (
        browser_tool._browser_url_block_reason(
            "http://169.254.169.254/latest/meta-data/", "task"
        )
        == "cloud metadata endpoint"
    )


@pytest.mark.parametrize(
    "expression",
    [
        "fetch('http://169.254.' + '169.254/latest/meta-data/')",
        r"fetch('http:\u002f\u002f169.254.169.254/latest/meta-data/')",
        "window.location = 'http://metadata.google.internal/'",
    ],
)
def test_eval_and_raw_cdp_block_metadata_literal_bypasses(
    monkeypatch, expression, runtime_paths
):
    from tools import browser_tool
    from tools.browser_cdp_tool import _cdp_metadata_floor_error

    monkeypatch.setattr(browser_tool, "_eval_ssrf_guard_active", lambda _task: False)
    blocked = browser_tool._expression_block_reason(expression, "task")
    assert blocked is not None
    assert blocked[1] == "cloud metadata endpoint"
    assert _cdp_metadata_floor_error("Runtime.evaluate", {"expression": expression})


def test_dynamic_network_eval_fails_closed(runtime_paths):
    from tools.browser_cdp_tool import _cdp_metadata_floor_error

    assert _cdp_metadata_floor_error(
        "Runtime.evaluate",
        {"expression": "fetch(window.name)"},
    ) == "Blocked: CDP evaluation uses an unvalidated dynamic URL"


def test_current_page_probe_failure_fails_closed(monkeypatch, runtime_paths):
    from tools import browser_tool

    monkeypatch.setattr(
        browser_tool,
        "_run_browser_command",
        lambda *args, **kwargs: {"success": False, "error": "probe failed"},
    )
    assert browser_tool._current_page_block_reason("task") == (
        "unknown",
        "URL could not be validated",
    )


def test_browser_output_redaction_is_forced_and_recursive(runtime_paths):
    from tools.browser_tool import _redact_browser_output

    secret = "sk-testabcdefghijklmnop"
    result = _redact_browser_output(
        {"console": [secret], "nested": {"exception": f"token={secret}"}}
    )
    assert secret not in json.dumps(result)

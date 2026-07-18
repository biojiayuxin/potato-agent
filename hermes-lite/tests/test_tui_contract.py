from __future__ import annotations

import sys


def _import_server():
    stdout = sys.stdout
    try:
        from tui_gateway import server
    finally:
        sys.stdout = stdout
    return server


def _request(server, method: str, params: dict, request_id: int = 1) -> dict:
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )
    assert response is not None
    return response


def test_removed_cli_and_slash_workers_fail_closed(runtime_paths, monkeypatch) -> None:
    server = _import_server()

    cli = _request(server, "cli.exec", {"argv": ["sessions", "list"]})
    assert cli["error"]["code"] == 5032
    assert "not part of Potato Hermes Lite" in cli["error"]["message"]

    monkeypatch.setattr(server, "_sess", lambda _params, _rid: ({}, None))
    slash = _request(server, "slash.exec", {"session_id": "fixture"})
    assert slash["error"]["code"] == 4018
    assert "command.dispatch" in slash["error"]["message"]


def test_removed_interaction_and_management_rpcs_fail_closed(runtime_paths) -> None:
    server = _import_server()
    calls = (
        ("clipboard.paste", {}),
        ("voice.toggle", {"action": "on"}),
        ("voice.record", {"action": "start"}),
        ("voice.tts", {"text": "not spoken"}),
        ("tools.configure", {"toolset": "web", "enabled": True}),
        ("cron.manage", {"action": "list"}),
        ("clarify.respond", {"value": "answer"}),
        ("sudo.respond", {"password": "not-used"}),
        ("secret.respond", {"value": "not-used"}),
    )

    for request_id, (method, params) in enumerate(calls, start=10):
        response = _request(server, method, params, request_id=request_id)
        assert response["error"]["code"] == 5032, method
        assert "Potato Hermes Lite" in response["error"]["message"], method

    removed_modules = {
        "hermes_cli.clipboard",
        "hermes_cli.tools_config",
        "hermes_cli.voice",
        "tools.clarify_tool",
        "tools.cronjob_tools",
        "tools.voice_mode",
    }
    assert removed_modules.isdisjoint(sys.modules)


def test_approval_response_requires_and_forwards_exact_id(
    runtime_paths, monkeypatch
) -> None:
    server = _import_server()
    from tools import approval

    session_id = "approval-session"
    server._sessions[session_id] = {"session_key": "approval-key"}
    captured = {}

    def resolve(session_key, choice, *, resolve_all=False, approval_id=""):
        captured.update(
            {
                "session_key": session_key,
                "choice": choice,
                "resolve_all": resolve_all,
                "approval_id": approval_id,
            }
        )
        return 1

    monkeypatch.setattr(approval, "resolve_gateway_approval", resolve)
    try:
        missing = _request(
            server,
            "approval.respond",
            {"session_id": session_id, "choice": "once"},
        )
        assert missing["error"]["code"] == 4002

        invalid = _request(
            server,
            "approval.respond",
            {
                "session_id": session_id,
                "choice": "anything",
                "approval_id": "approval-123",
            },
            request_id=2,
        )
        assert invalid["error"]["code"] == 4002

        response = _request(
            server,
            "approval.respond",
            {
                "session_id": session_id,
                "choice": "once",
                "approval_id": "approval-123",
            },
            request_id=3,
        )
        assert response["result"] == {"resolved": 1}
        assert captured == {
            "session_key": "approval-key",
            "choice": "once",
            "resolve_all": False,
            "approval_id": "approval-123",
        }
    finally:
        server._sessions.pop(session_id, None)


def test_plan_dispatch_preserves_interface_skill_contract(
    runtime_paths, monkeypatch
) -> None:
    server = _import_server()
    import agent.skill_commands as skill_commands
    import hermes_cli.plugins as cli_plugins

    session_id = "session-id"
    task_id = "session-key"
    captured = {}

    def build_message(name: str, arg: str, *, task_id: str) -> str:
        captured.update({"name": name, "arg": arg, "task_id": task_id})
        return f"plan invocation: {arg}"

    monkeypatch.setattr(server, "_load_cfg", lambda: {"quick_commands": {}})
    monkeypatch.setattr(
        skill_commands,
        "scan_skill_commands",
        lambda: {"/plan": {"name": "plan", "description": "Plan a task"}},
    )
    monkeypatch.setattr(skill_commands, "build_skill_invocation_message", build_message)
    monkeypatch.setattr(cli_plugins, "get_plugin_command_handler", lambda _name: None)
    server._sessions[session_id] = {"session_key": task_id}
    try:
        response = _request(
            server,
            "command.dispatch",
            {"name": "plan", "arg": "review the migration", "session_id": session_id},
        )
    finally:
        server._sessions.pop(session_id, None)

    assert response["result"] == {
        "type": "skill",
        "message": "plan invocation: review the migration",
        "name": "plan",
    }
    assert captured == {
        "name": "/plan",
        "arg": "review the migration",
        "task_id": task_id,
    }

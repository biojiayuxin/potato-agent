from __future__ import annotations

import sys
import threading

from agent.title_generator import AutoTitleOutcome


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


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self) -> None:
        self.target(*self.args, **self.kwargs)


def test_fork_boundary_requires_matching_physical_session_flush_and_head() -> None:
    server = _import_server()

    class _DB:
        def __init__(self) -> None:
            self.messages = [
                {"id": 1, "role": "user", "content": "first"},
                {"id": 2, "role": "assistant", "content": "old"},
            ]

        def get_session(self, session_id):
            return {"id": session_id}

        def get_messages(self, _session_id):
            return list(self.messages)

        def get_compression_tip(self, session_id):
            return session_id

    db = _DB()

    class _Agent:
        session_id = "physical"
        _session_db = db
        _last_flushed_db_idx = 2

    agent = _Agent()
    session = {
        "session_key": "physical",
        "history": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "old"},
        ],
        "history_lock": threading.Lock(),
    }
    before = server._fork_boundary_checkpoint(session, agent)
    db.messages.extend(
        [
            {"id": 3, "role": "user", "content": "next"},
            {"id": 4, "role": "assistant", "content": "answer"},
        ]
    )
    session["history"].extend(
        [
            {"role": "user", "content": "next"},
            {"role": "assistant", "content": "answer"},
        ]
    )
    agent._last_flushed_db_idx = 4

    assert server._validated_fork_raw_boundary(
        session, agent, before, "answer"
    ) == {
        "physical_session_id": "physical",
        "active_message_head": 4,
    }
    agent._last_flushed_db_idx = 3
    assert server._validated_fork_raw_boundary(session, agent, before, "answer") is None


def _patch_prompt_runner(server, monkeypatch, events):
    from hermes_cli import goals
    from tools import approval
    from tools.process_registry import process_registry

    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(server, "_emit", lambda kind, sid, payload=None: events.append((kind, sid, payload)))
    monkeypatch.setattr(server, "_set_session_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_wire_callbacks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "make_stream_renderer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "render_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_get_usage", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_session_info", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(approval, "set_current_session_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(approval, "reset_current_session_key", lambda *_args, **_kwargs: None)
    class _InactiveGoalManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def is_active(self):
            return False

    monkeypatch.setattr(goals, "GoalManager", _InactiveGoalManager)
    monkeypatch.setattr(process_registry, "drain_notifications", lambda: [])


def _prompt_session(agent):
    return {
        "agent": agent,
        "session_key": "persistent-session",
        "history": [],
        "history_version": 0,
        "history_lock": threading.RLock(),
        "attached_images": [],
        "pending_title": None,
        "running": True,
        "cwd": "/tmp",
        "cols": 100,
    }


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


def test_prompt_auto_title_uses_agent_db_runtime_and_ordered_events(
    runtime_paths,
    monkeypatch,
) -> None:
    server = _import_server()
    from agent import title_generator

    class _DB:
        def get_session_title_in_lineage(self, _session_id):
            return None

    class _Agent:
        _session_db = _DB()

        def run_conversation(self, prompt, **_kwargs):
            return {
                "final_response": "answer",
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "answer"},
                ],
            }

        def _current_main_runtime(self):
            return {
                "model": "runtime-model",
                "provider": "custom",
                "base_url": "https://example.invalid/v1",
                "api_key": "runtime-key",
                "api_mode": "codex_responses",
            }

    events = []
    captured = {}
    _patch_prompt_runner(server, monkeypatch, events)

    def fake_auto_title(db, session_id, user_message, assistant_response, history, **kwargs):
        captured.update(
            {
                "db": db,
                "session_id": session_id,
                "user_message": user_message,
                "assistant_response": assistant_response,
                "history": history,
                "runtime": kwargs["main_runtime"],
            }
        )
        kwargs["title_callback"]("Generated Title")
        kwargs["outcome_callback"](
            AutoTitleOutcome("updated", title="Generated Title")
        )
        return True

    monkeypatch.setattr(title_generator, "maybe_auto_title", fake_auto_title)
    agent = _Agent()
    session = _prompt_session(agent)
    server._run_prompt_submit("request", "live-session", session, "question")

    title_events = [kind for kind, _sid, _payload in events if kind.startswith("session.title.")]
    assert [kind for kind, _sid, _payload in events].index("message.complete") < (
        [kind for kind, _sid, _payload in events].index("session.title.started")
    )
    assert title_events == [
        "session.title.started",
        "session.title.updated",
        "session.title.finished",
    ]
    assert captured["db"] is agent._session_db
    assert captured["session_id"] == "persistent-session"
    assert captured["runtime"]["api_mode"] == "codex_responses"
    for kind, _sid, payload in events:
        if kind.startswith("session.title."):
            assert payload["task_id"]
            assert payload["session_key"] == "persistent-session"


def test_prompt_auto_title_allows_only_one_worker_per_session(
    runtime_paths,
    monkeypatch,
) -> None:
    server = _import_server()
    from agent import title_generator

    class _DB:
        def get_session_title_in_lineage(self, _session_id):
            return None

    class _Agent:
        _session_db = _DB()

        def run_conversation(self, prompt, **_kwargs):
            return {
                "final_response": "answer",
                "messages": [{"role": "user", "content": prompt}],
            }

        def _current_main_runtime(self):
            return {
                "model": "runtime-model",
                "provider": "custom",
                "base_url": "",
                "api_key": "runtime-key",
                "api_mode": "chat_completions",
            }

    events = []
    calls = []
    _patch_prompt_runner(server, monkeypatch, events)
    monkeypatch.setattr(
        title_generator,
        "maybe_auto_title",
        lambda *_args, **_kwargs: calls.append(True) or True,
    )
    session = _prompt_session(_Agent())

    server._run_prompt_submit("request-1", "live-session", session, "first")
    session["running"] = True
    server._run_prompt_submit("request-2", "live-session", session, "second")

    assert calls == [True]
    assert session["auto_title_task_id"]

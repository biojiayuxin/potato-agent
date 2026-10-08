from __future__ import annotations

import copy
import os
import threading
from types import SimpleNamespace

import pytest

from potato_hermes_lite.session_models import apply_model_config, normalize_model_config
from tests.test_tui_contract import (
    _ImmediateThread, _import_server, _patch_prompt_runner, _prompt_session, _request,
)


def _config(model="Fast", **kwargs):
    return {
        "id": model.lower(), "model": model, "provider": "custom",
        "api_mode": "codex_responses", "context_length": 128_000,
        "reasoning_effort": "high", **kwargs,
    }


class _Compressor:
    context_length = 256_000
    threshold_tokens = 128_000

    def update_model(self, **kwargs):
        self.__dict__.update(kwargs)
        self.threshold_tokens = int(self.context_length * 0.5)


class _Agent:
    def __init__(self):
        self.model = "Old"
        self.provider = "custom"
        self.api_mode = "chat_completions"
        self.base_url = "http://127.0.0.1:9999/v1"
        self.api_key = "local-proxy-fixture-token"
        self.reasoning_config = {"effort": "low"}
        self.context_compressor = _Compressor()
        self._primary_runtime = {}
        self._session_init_model_config = {}
        self.switches = []
        self._session_db = None

    def switch_model(self, **kwargs):
        self.switches.append(kwargs)
        self.model = kwargs["new_model"]
        self.provider = kwargs["new_provider"]
        self.api_mode = kwargs["api_mode"]


def test_identity_prompt_and_durable_rows_use_upstream_while_requests_keep_route(runtime_paths, tmp_path):
    from potato_hermes_lite.session_visibility import SessionDB
    from potato_hermes_lite.session_models import record_runtime_model
    import json
    class Agent(_Agent):
        def _build_system_prompt(self, system_message=None):
            return "Instructions\n\nConversation started: today\nModel: " + self.model + "\nProvider: custom"
        def _build_system_prompt_parts(self, system_message=None):
            return {"stable": "Instructions", "context": "", "volatile": "Conversation started: today\nModel: " + self.model}

    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("chat", "tui", model="old-alias", system_prompt="Model: old-alias")
        agent = Agent()
        agent._session_db, agent.session_id = db, "chat"
        selected = _config("gpt-current.pmc1.SIGNED_FIXTURE", upstream_model="gpt-current", display_name="Deep",
                           config_revision="v1", reasoning_effort="xhigh")
        apply_model_config(agent, selected)
        assert agent.model == selected["model"]
        assert db.get_session("chat")["system_prompt"] is None
        prompt = agent._build_system_prompt()
        assert "Model: gpt-current\n" in prompt and "SIGNED_FIXTURE" not in prompt
        assert "Reasoning effort: xhigh" in prompt
        assert "SIGNED_FIXTURE" not in agent._build_system_prompt_parts()["volatile"]
        record_runtime_model(agent, "chat", "turn-1")
        db.create_session("compressed", "tui", model=agent.model, model_config=agent._session_init_model_config)
        assert db.get_session("compressed")["model"] == "gpt-current"
        row = db._conn.execute("SELECT model_snapshot FROM potato_model_runs WHERE turn_id = 'turn-1'").fetchone()
        assert json.loads(row[0])["upstream_model"] == "gpt-current"
        assert "SIGNED_FIXTURE" not in row[0]
        replacement = _config("gpt-next.pmc1.SIGNED_NEXT", upstream_model="gpt-next", display_name="Research",
                              config_revision="v2", reasoning_effort="medium")
        apply_model_config(agent, replacement)
        assert "Model: gpt-next\n" in agent._build_system_prompt()
        assert "Research" in agent._build_system_prompt()
        assert json.loads(db._conn.execute("SELECT model_snapshot FROM potato_model_runs").fetchone()[0])["upstream_model"] == "gpt-current"
    finally:
        db.close()


def test_identity_write_failure_is_retried_before_snapshot_is_committed(runtime_paths, tmp_path, monkeypatch):
    from potato_hermes_lite.session_visibility import SessionDB

    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("chat", "tui", model="old-alias", system_prompt="Model: old-alias")
        agent = _Agent()
        agent._session_db, agent.session_id = db, "chat"
        selected = _config("gpt-current.pmc1.SIGNED_FIXTURE", upstream_model="gpt-current",
                           display_name="Deep", config_revision="v1")
        real_write = db._execute_write

        def fail_write(_callback):
            raise OSError("identity write failed")

        monkeypatch.setattr(db, "_execute_write", fail_write)
        with pytest.raises(OSError, match="identity write failed"):
            apply_model_config(agent, selected)
        assert agent._potato_model_config is None
        assert db.get_session("chat")["system_prompt"] == "Model: old-alias"

        monkeypatch.setattr(db, "_execute_write", real_write)
        assert apply_model_config(agent, selected)
        assert agent._potato_model_config == selected
        assert db.get_session("chat")["model"] == "gpt-current"
        assert db.get_session("chat")["system_prompt"] is None
        assert not apply_model_config(agent, selected)
    finally:
        db.close()


def test_model_adapter_preserves_proxy_and_other_sessions(runtime_paths):
    first, second = _Agent(), _Agent()
    first_before = copy.deepcopy(first.__dict__)
    selected = _config()
    normalize_model_config(selected)  # Runtime-profile initialization is process scoped.
    environment = dict(os.environ)

    assert apply_model_config(second, selected)
    assert second.switches == [{
        "new_model": "Fast", "new_provider": "custom",
        "base_url": second.base_url, "api_key": second.api_key,
        "api_mode": "codex_responses",
    }]
    assert second.reasoning_config == {"enabled": True, "effort": "high"}
    assert second._config_context_length == 128_000
    assert second._aux_compression_context_length_config == 128_000
    assert second.context_compressor.context_length == 128_000
    assert second._primary_runtime["compressor_context_length"] == 128_000
    assert second._primary_runtime["compressor_threshold_tokens"] == 64_000
    assert second._primary_runtime["compressor_api_mode"] == "codex_responses"
    assert first.model == first_before["model"]
    assert first.switches == []
    assert dict(os.environ) == environment
    assert not apply_model_config(second, selected)
    assert len(second.switches) == 1


def test_model_adapter_clears_old_context_override_and_reasoning(runtime_paths, monkeypatch):
    from agent import model_metadata

    calls = []
    monkeypatch.setattr(model_metadata, "get_model_context_length", lambda *args, **kwargs: calls.append(kwargs) or 96_000)
    agent = _Agent()
    apply_model_config(agent, _config(context_length=None, reasoning_effort="none"))
    assert calls[0]["config_context_length"] is None
    assert agent._config_context_length is None
    assert agent._aux_compression_context_length_config is None
    assert agent.context_compressor.context_length == 96_000
    assert agent.reasoning_config == {"enabled": False}


def test_failed_model_switch_keeps_applied_snapshot_and_metadata(runtime_paths):
    agent = _Agent()
    apply_model_config(agent, _config())

    def fail(**_kwargs):
        raise RuntimeError("client construction failed")

    agent.switch_model = fail
    with pytest.raises(RuntimeError, match="client construction"):
        apply_model_config(agent, _config("Pro", context_length=256_000))
    assert agent._potato_model_config == _config()
    assert agent.model == "Fast"
    assert agent._config_context_length == 128_000


@pytest.mark.parametrize("patch", [
    {"api_key": "forbidden"}, {"base_url": "https://untrusted.invalid"},
    {"provider": "openrouter"}, {"context_length": True},
    {"context_length": 0}, {"api_mode": "invalid"}, {"reasoning_effort": "invalid"},
])
def test_snapshot_rejects_credentials_endpoints_and_invalid_settings(runtime_paths, patch):
    with pytest.raises(ValueError):
        normalize_model_config({**_config(), **patch})


def _session(key, *, running=False):
    return {
        "session_key": key, "history_lock": threading.Lock(), "running": running,
        "model_config": _config("Pro"), "agent": _Agent(),
    }


def test_model_selection_is_local_and_does_not_initialize_an_agent(runtime_paths, monkeypatch):
    server = _import_server()
    first, second = _session("key-a", running=True), _session("key-b")
    monkeypatch.setattr(server, "_sessions", {"a": first, "b": second})
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: pytest.fail("selection built an agent"))
    selected = _request(server, "session.model.set", {"session_id": "key-b", "model_config": _config()})
    assert selected["result"] == {"found": True, "model_config": _config()}
    assert first["model_config"] == _config("Pro")
    assert first["running"]
    assert second["agent"].switches == []
    assert second["model_config"] == _config()
    rejected = _request(server, "session.model.set", {"session_id": "a", "model_config": _config()})
    assert rejected["error"]["code"] == 4009
    missing = _request(server, "session.model.set", {"session_id": "missing", "model_config": _config()})
    assert missing["result"] == {"found": False, "model_config": None}


@pytest.mark.parametrize("busy", ["approval", "child", "pending"])
def test_model_selection_rejects_approval_and_delegation(runtime_paths, monkeypatch, busy):
    server = _import_server()
    from tools import approval

    session = _session("key")
    if busy != "approval":
        session["delegation_manager"] = SimpleNamespace(
            lock=threading.RLock(), owner="key",
            _live=lambda: {"child": object()} if busy == "child" else {},
            store=SimpleNamespace(pending=lambda _key: ["pending"] if busy == "pending" else []),
        )
    monkeypatch.setattr(approval, "has_blocking_approval", lambda _key: busy == "approval")
    monkeypatch.setattr(server, "_sessions", {"live": session})
    result = _request(server, "session.model.set", {"session_id": "live", "model_config": _config()})
    assert result["error"]["code"] == 4009
    assert session["model_config"] == _config("Pro")


def test_prompt_busy_check_does_not_replace_model_snapshot(runtime_paths, monkeypatch):
    server = _import_server()
    from potato_hermes_lite import delegation_gateway

    session = _session("key", running=True)
    monkeypatch.setattr(server, "_sessions", {"live": session})
    monkeypatch.setattr(delegation_gateway, "bind", lambda *_args: SimpleNamespace())
    result = _request(server, "prompt.submit", {
        "session_id": "live", "text": "next", "model_config": _config(),
    })
    assert result["error"]["code"] == 4009
    assert session["model_config"] == _config("Pro")


def test_internal_continuation_applies_selected_model_before_conversation(runtime_paths, monkeypatch):
    server = _import_server()
    events, observed = [], []
    _patch_prompt_runner(server, monkeypatch, events)
    agent = _Agent()

    def run(prompt, **_kwargs):
        observed.append((prompt, agent.model, agent.reasoning_config))
        return {"final_response": "answer", "messages": []}

    agent.run_conversation = run
    session = _prompt_session(agent)
    session["model_config"] = _config()
    server._run_prompt_submit("notification", "live", session, "background finished")
    assert observed == [("background finished", "Fast", {"enabled": True, "effort": "high"})]
    assert not session["running"]
    assert any(kind == "message.complete" for kind, _, _ in events)


def test_turn_uses_snapshot_frozen_before_worker_starts(runtime_paths, monkeypatch):
    server = _import_server()
    events, workers, observed = [], [], []
    _patch_prompt_runner(server, monkeypatch, events)

    class DeferredThread(_ImmediateThread):
        def start(self):
            workers.append(self.target)

    monkeypatch.setattr(server.threading, "Thread", DeferredThread)
    agent = _Agent()

    def run(_prompt, **_kwargs):
        observed.append(agent.model)
        return {"final_response": "answer", "messages": []}

    agent.run_conversation = run
    session = _prompt_session(agent)
    session["model_config"] = _config()
    server._run_prompt_submit("notification", "live", session, "background finished")
    session["model_config"] = _config("Pro")
    workers[0]()
    assert observed == ["Fast"]


def test_model_failure_emits_terminal_event_without_running_conversation(runtime_paths, monkeypatch):
    server = _import_server()
    events = []
    _patch_prompt_runner(server, monkeypatch, events)
    agent = _Agent()
    agent.run_conversation = lambda *_args, **_kwargs: pytest.fail("ran after switch failure")
    agent.switch_model = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("switch failed"))
    session = _prompt_session(agent)
    session["model_config"] = _config()
    server._run_prompt_submit("notification", "live", session, "background finished")
    assert not session["running"]
    assert any(kind == "error" and payload["message"] == "switch failed" for kind, _, payload in events)


def test_async_create_build_uses_its_model_snapshot(runtime_paths, monkeypatch):
    server = _import_server()
    built = []
    scheduled = []

    class Timer:
        def __init__(self, _delay, callback):
            self.callback = callback

        def start(self):
            scheduled.append(self.callback)

    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server.threading, "Timer", Timer)
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(server, "_make_agent", lambda *args, **kwargs: built.append(kwargs["model_config"]) or _Agent())
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda *_args: None)
    monkeypatch.setattr(server, "_wire_callbacks", lambda *_args: None)
    monkeypatch.setattr(server, "_start_notification_poller", lambda *_args: threading.Event())
    monkeypatch.setattr(server, "_notify_session_boundary", lambda *_args: None)
    monkeypatch.setattr(server, "_session_info", lambda *_args: {})
    monkeypatch.setattr(server, "_probe_config_health", lambda *_args: "")
    monkeypatch.setattr(server, "_emit", lambda *_args: None)
    monkeypatch.setattr(server, "_load_cfg", lambda: {})
    monkeypatch.setattr(server, "_git_branch_for_cwd", lambda *_args: "")
    result = _request(server, "session.create", {"model_config": _config()})
    assert result["result"]["info"]["model"] == "Fast"
    scheduled[0]()
    assert built == [_config()]
    session = server._sessions[result["result"]["session_id"]]
    assert session["agent_ready"].is_set()
    assert not session["agent_error"]


def test_cold_resume_applies_snapshot_before_session_is_live(runtime_paths, monkeypatch):
    server = _import_server()
    from potato_hermes_lite.session_visibility import SessionDB

    db = SessionDB(runtime_paths.hermes_home / "resume-test.db")
    db.create_session("stored", source="tui", model="Old")
    observed = []
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_make_agent", lambda *args, **kwargs: observed.append(("build", kwargs["model_config"])) or _Agent())
    monkeypatch.setattr(server, "_init_session", lambda *args, **kwargs: observed.append(("init", kwargs["model_config"])))
    monkeypatch.setattr(server, "_session_info", lambda *_args: {})
    try:
        result = _request(server, "session.resume", {"session_id": "stored", "model_config": _config()})
    finally:
        db.close()
    assert "result" in result
    assert observed == [("build", _config()), ("init", _config())]

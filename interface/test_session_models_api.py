from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

from anyio.from_thread import start_blocking_portal
import pytest

from interface.test_internal_session_visibility import chat, restore_interface_imports  # noqa: F401


class _Bridge:
    def __init__(self, user, db) -> None:
        self.user_id = user.id
        self.db = db
        self.calls = []
        self.bindings = {}
        self.listener = None
        self.failure = None
        self.created = 0

    def add_event_listener(self, listener):
        self.listener = listener
        return "listener"

    def remove_event_listener(self, listener_id):
        self.listener = None

    def remember_live_session(self, live_session_id, *, persistent_session_id="", run_id=""):
        self.bindings[live_session_id] = (persistent_session_id, run_id)

    def forget_live_session(self, live_session_id):
        self.bindings.pop(live_session_id, None)

    async def add_subscriber(self, websocket):
        return True

    def remove_subscriber(self, websocket):
        pass

    async def rpc(self, method, params):
        self.calls.append((method, params))
        if method == "session.model.set":
            if self.failure:
                raise self.failure
            return {"status": "updated"}
        if method == "session.create":
            self.created += 1
            sid = f"draft-{self.created}"
            self.db.create_session(sid, "tui")
            self.bindings[f"live-{sid}"] = (sid, "")
            return {"session_id": f"live-{sid}", "session_key": sid}
        if method == "session.title":
            return {"session_key": self.bindings[params["session_id"]][0]}
        if method == "session.resume":
            return {"session_id": f"live-{params['session_id']}", "session_key": params["session_id"]}
        if method == "prompt.submit":
            session_id, run_id = self.bindings[params["session_id"]]
            await self.listener({
                "type": "message.complete", "session_id": params["session_id"],
                "persistent_session_id": session_id, "run_id": run_id, "seq": 1,
                "payload": {"text": "done", "status": "complete"},
            })
            return {"status": "streaming"}
        raise AssertionError(f"unexpected RPC: {method}")


class _Registry:
    def __init__(self, bridge):
        self.bridge = bridge
        self.closed = []

    async def get_existing(self, user_id):
        return self.bridge

    async def close_for_reconfigure(self, user_id):
        self.closed.append(user_id)
        raise AssertionError("session selection must not close the user's bridge")

    async def maybe_close_if_unused(self, user_id):
        pass


@pytest.fixture
def models(chat, monkeypatch):
    app_mod, client, user, db = chat
    from interface import auth_db, session_model_store
    from interface.display_store import save_live_session_state
    from interface.session_run_manager import SessionRunManager

    from interface.test_model_support import make_catalog, install_catalog
    catalog = make_catalog()
    install_catalog(monkeypatch, catalog)
    config = {"hermes": {"model_catalog": True}}
    monkeypatch.setattr(app_mod.mapping_store, "load_config", lambda **_: config)
    config_path = user.target.hermes_home / "config.yaml"
    config_path.write_text("model:\n  default: old-global-model\n", encoding="utf-8")
    bridge = _Bridge(user, db)
    registry = _Registry(bridge)
    manager = SessionRunManager(db_path=auth_db.DEFAULT_AUTH_DB_PATH, model_resolver=app_mod._resolve_session_model_config)
    monkeypatch.setattr(app_mod.app.state, "session_run_manager", manager, raising=False)
    monkeypatch.setattr(app_mod.app.state, "tui_gateway_bridges", registry, raising=False)

    async def get_bridge(_):
        return bridge

    async def websocket_user(_):
        return user

    monkeypatch.setattr(app_mod, "_get_tui_bridge_for_user", get_bridge)
    monkeypatch.setattr(app_mod, "get_current_user_ws", websocket_user)

    def save_model(sid, mid):
        return session_model_store.set_session_model(user.id, sid, mid, auth_db.DEFAULT_AUTH_DB_PATH)

    def read_model(sid):
        return session_model_store.get_session_model_state(user.id, sid, auth_db.DEFAULT_AUTH_DB_PATH)

    def save_live(sid, **state):
        return save_live_session_state(user.id, sid, db_path=auth_db.DEFAULT_AUTH_DB_PATH, **state)

    # Keep one loop alive for background managed turns without starting the
    # application lifespan's runtime/maintenance schedulers.
    with start_blocking_portal() as portal:
        client.portal = portal
        try:
            yield SimpleNamespace(catalog=catalog,
                app=app_mod, client=client, user=user, db=db, manager=manager,
                bridge=bridge, registry=registry, save_model=save_model,
                read_model=read_model, save_live=save_live, config_path=config_path,
                portal=portal,
            )
        finally:
            portal.call(manager.shutdown)
            client.portal = None


def _put(models, sid, model_id):
    return models.client.put(f"/api/sessions/{sid}/model", json={"id": model_id})


def test_canonical_catalog_controls_ui_and_each_turn_snapshot(models, monkeypatch):
    from interface.model_catalog import public_catalog
    from interface.privileged_client import PrivilegedClient
    catalog = {
        "schema_version": 2, "catalog_signing_key": "PRIVATE_SIGNING_" + "x" * 64,
        "primary_option_id": "deep", "default_option_id": "fast",
        "backends": {"main": {"model": "actual-model", "api_mode": "chat_completions",
                               "base_url": "https://PRIVATE_ENDPOINT.example/v1", "api_key": "PRIVATE_CREDENTIAL"}},
        "options": {
            "deep": {"display_name": "Deep configured", "backend": "main", "context_length": 500000,
                     "reasoning_effort": "high"},
            "fast": {"display_name": "Fast configured", "backend": "main", "context_length": 128000,
                     "reasoning_effort": "medium"},
        },
    }
    monkeypatch.setattr(models.app.mapping_store, "load_config", lambda **_: {"hermes": {"model_catalog": True}})
    monkeypatch.setattr(PrivilegedClient, "get_model_catalog", lambda self: public_catalog(catalog))
    models.save_model("public", "deep")
    response = models.client.get("/api/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["display_name"] == "Deep configured"
    assert "PRIVATE_" not in response.text and ".pmc1." not in response.text
    state = models.client.get("/api/sessions/public").json()["session"]
    assert state["model_id"] == "deep"
    assert models.read_model("public")["model_id"] == "deep"
    assert _put(models, "public", "primary").status_code == 400
    selected = _put(models, "public", "deep")
    assert selected.json()["model_id"] == "deep"
    frozen = models.bridge.calls[-1][1]["model_config"]
    assert frozen["upstream_model"] == "actual-model"
    catalog["backends"]["main"]["model"] = "upgraded-model"
    next_round = models.app._resolve_session_model_config_sync(models.user.id, "public")
    assert next_round["upstream_model"] == "upgraded-model"
    assert frozen["upstream_model"] == "actual-model"
    assert frozen["config_revision"] != next_round["config_revision"]
    assert models.read_model("public")["model_revision"] == selected.json()["model_revision"]
    assert "PRIVATE_" not in json.dumps(models.bridge.calls)


def test_other_conversation_can_switch_while_one_is_responding(models):
    models.save_model("parent", "fast")
    models.save_live("parent", status="running", run_id="busy-parent")
    before_config = models.config_path.read_bytes()
    response = _put(models, "public", "deep")
    assert response.status_code == 200, response.text
    assert response.json()["model_id"] == "deep"
    assert models.read_model("parent")["model_id"] == "fast"
    assert models.read_model("public")["model_id"] == "deep"
    assert models.registry.closed == []
    assert models.config_path.read_bytes() == before_config
    assert models.bridge.calls[0][0] == "session.model.set"
    assert models.bridge.calls[0][1]["session_id"] == "public"
    model_config = models.bridge.calls[0][1]["model_config"]
    from interface.model_catalog import resolve_route
    snapshot, _ = resolve_route(models.catalog, model_config["model"])
    assert snapshot["id"] == model_config["id"] == "deep"
    assert snapshot["model"] == model_config["upstream_model"] == "upstream-deep"
    assert model_config["context_length"] == 200000
    assert model_config["reasoning_effort"] == "high"
    assert "SECRET" not in json.dumps(models.bridge.calls)
    assert "example/v1" not in json.dumps(models.bridge.calls)


@pytest.mark.parametrize("state", [
    {"status": "running"}, {"status": "queued"}, {"status": "starting"},
    {"status": "awaiting_approval"}, {"status": "completed", "background_pending": True},
])
def test_active_conversation_cannot_switch(models, state):
    initial = models.save_model("parent", "fast")
    models.save_live("parent", **state)
    response = _put(models, "parent", "deep")
    assert response.status_code == 409, response.text
    assert models.read_model("parent") == initial
    assert models.bridge.calls == []


def test_offline_switch_persists_without_starting_gateway(models):
    models.registry.bridge = None
    response = _put(models, "public", "deep")
    assert response.status_code == 200, response.text
    assert models.bridge.calls == []
    for path in ["/api/sessions/public", "/api/sessions"]:
        payload = models.client.get(path).json()
        row = payload["session"] if "session" in payload else next(s for s in payload["sessions"] if s["id"] == "public")
        assert row["model_id"] == "deep"
        assert row["model_revision"] == response.json()["model_revision"]


def test_model_catalog_and_unconfigured_history_default_to_fast(models):
    catalog = models.client.get("/api/models")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["default_id"] == "fast"
    assert "active_id" not in catalog.json()
    assert "SECRET" not in catalog.text
    assert "example/v1" not in catalog.text
    history = models.client.get("/api/sessions/public")
    assert history.json()["session"]["model_id"] == "fast"


@pytest.mark.parametrize("path", ["/api/sessions/public", "/api/sessions", "/api/sessions/public/live"])
def test_removed_model_falls_back_to_fast_with_a_new_persisted_revision(models, path):
    previous = models.save_model("public", "deep")
    models.save_live("public", status="completed", run_id="completed-run")
    models.catalog["primary_option_id"] = "fast"
    del models.catalog["options"]["deep"]

    response = models.client.get(path)
    assert response.status_code == 200, response.text
    payload = response.json()
    if path.endswith("/live"):
        state = payload
    elif "session" in payload:
        state = payload["session"]
    else:
        state = next(row for row in payload["sessions"] if row["id"] == "public")
    assert state["model_id"] == "fast"
    assert state["model_revision"] > previous["model_revision"]
    assert models.read_model("public") == {
        "model_id": "fast", "model_revision": state["model_revision"],
    }
    repeated = models.client.get("/api/sessions/public").json()["session"]
    assert repeated["model_revision"] == state["model_revision"]
    assert models.bridge.calls == []


@pytest.mark.parametrize("session_id", ["missing", "internal-child", "internal-ch"])
def test_model_selection_enforces_conversation_visibility(models, session_id):
    assert _put(models, session_id, "deep").status_code == 404
    assert models.read_model(session_id)["model_revision"] == 0
    assert models.bridge.calls == []


def test_model_selection_rejects_unlisted_models_and_client_runtime_details(models):
    assert _put(models, "public", "arbitrary-model").status_code == 400
    response = models.client.put("/api/sessions/public/model", json={
        "id": "deep", "base_url": "https://attacker.example", "api_key": "injected",
    })
    assert response.status_code == 422
    assert models.read_model("public")["model_revision"] == 0


@pytest.mark.parametrize("failure", ["busy", "timeout"])
def test_gateway_failure_returns_restored_model_with_fresh_revision(models, failure):
    from interface.tui_gateway_bridge import TuiGatewayBridgeError

    initial = models.save_model("public", "fast")
    models.bridge.failure = (
        TuiGatewayBridgeError("session busy", code=4009)
        if failure == "busy" else asyncio.TimeoutError()
    )
    response = _put(models, "public", "deep")
    assert response.status_code == (409 if failure == "busy" else 503), response.text
    restored = models.read_model("public")
    assert restored["model_id"] == initial["model_id"]
    assert restored["model_revision"] > initial["model_revision"]
    detail = response.json()["detail"]
    assert detail["message"]
    assert detail["session_id"] == "public"
    assert detail["model_id"] == restored["model_id"]
    assert detail["model_revision"] == restored["model_revision"]


@pytest.mark.parametrize("selected", [None, "deep"])
def test_draft_submission_persists_initial_model_and_retry_cannot_change_it(models, selected):
    payload = {"prompt": "first question", "request_id": "first-turn"}
    if selected:
        payload["model_id"] = selected
    response = models.client.post("/api/sessions/draft/turns", json=payload)
    assert response.status_code == 200, response.text
    sid = response.json()["session"]["id"]
    expected = selected or "fast"
    assert response.json()["session"]["model_id"] == expected
    original = models.read_model(sid)
    assert original["model_id"] == expected
    created = next(params for method, params in models.bridge.calls if method == "session.create")
    assert created["model_config"]["id"] == expected
    retry = models.client.post("/api/sessions/draft/turns", json={**payload, "model_id": "fast" if expected == "deep" else "deep"})
    assert retry.status_code == 200, retry.text
    assert retry.json()["session"]["id"] == sid
    assert models.read_model(sid) == original
    assert models.bridge.created == 1


def test_cold_resume_and_turn_use_saved_model_instead_of_request_override(models):
    models.save_model("public", "deep")
    response = models.client.post("/api/sessions/public/turns", json={
        "prompt": "follow up", "request_id": "resume-turn", "model_id": "fast",
    })
    assert response.status_code == 200, response.text
    resumed = next(params for method, params in models.bridge.calls if method == "session.resume")
    assert resumed["model_config"]["id"] == "deep"
    from interface.model_catalog import resolve_route
    snapshot, _ = resolve_route(models.catalog, resumed["model_config"]["model"])
    assert snapshot["id"] == "deep"
    assert models.read_model("public")["model_id"] == "deep"
    assert response.json()["session"]["model_id"] == "deep"


def test_cold_resume_serializes_same_conversation_model_change_only(models):
    models.save_model("public", "deep")
    resume_started = threading.Event()
    release_resume = asyncio.Event()
    prompt_started = threading.Event()
    release_prompt = asyncio.Event()
    original_rpc = models.bridge.rpc

    async def delayed_rpc(method, params):
        if method == "session.resume":
            resume_started.set()
            await release_resume.wait()
        if method == "prompt.submit":
            prompt_started.set()
            await release_prompt.wait()
        return await original_rpc(method, params)

    models.bridge.rpc = delayed_rpc
    with ThreadPoolExecutor(max_workers=2) as pool:
        turn = pool.submit(models.client.post, "/api/sessions/public/turns", json={
            "prompt": "follow up", "request_id": "cold-resume-race",
        })
        try:
            assert resume_started.wait(timeout=3)
            same_conversation = pool.submit(_put, models, "public", "fast")
            other_conversation = _put(models, "parent", "deep")
            assert other_conversation.status_code == 200, other_conversation.text
            assert not same_conversation.done()
            models.portal.call(release_resume.set)
            response = turn.result(timeout=3)
            assert response.status_code == 200, response.text
            assert prompt_started.wait(timeout=3)
            rejected = same_conversation.result(timeout=3)
            assert rejected.status_code == 409, rejected.text
            assert models.read_model("public")["model_id"] == "deep"
        finally:
            models.portal.call(release_resume.set)
            models.portal.call(release_prompt.set)


def test_compression_tip_uses_root_selection_and_physical_gateway_target(models):
    models.db.end_session("public", "compression")
    models.db.create_session("compressed", "tui", parent_session_id="public")
    response = _put(models, "compressed", "deep")
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == "public"
    assert models.read_model("public")["model_id"] == "deep"
    assert models.read_model("compressed")["model_revision"] == 0
    assert models.bridge.calls[0][1]["session_id"] == "compressed"


def test_fork_inherits_source_once_and_retry_preserves_later_target_choice(models):
    models.save_model("public", "deep")
    history = models.client.get("/api/sessions/public").json()["messages"]
    assistant = next(m for m in history if m["role"] == "assistant")
    payload = {"fork_cursor": assistant["fork_cursor"], "request_id": "model-fork"}
    first = models.client.post("/api/sessions/public/forks", json=payload)
    assert first.status_code == 200, first.text
    fork_id = first.json()["session"]["id"]
    assert first.json()["session"]["model_id"] == "deep"
    assert models.read_model(fork_id)["model_id"] == "deep"
    models.save_model("public", "fast")
    models.save_model(fork_id, "fast")
    saved_fork = models.read_model(fork_id)
    retried = models.client.post("/api/sessions/public/forks", json=payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["session"]["id"] == fork_id
    assert models.read_model(fork_id) == saved_fork


def test_live_response_and_session_deletion_include_model_state(models):
    saved = models.save_model("public", "deep")
    models.save_live("public", status="completed", run_id="completed-run")
    live = models.client.get("/api/sessions/public/live")
    assert live.status_code == 200, live.text
    assert live.json()["model_id"] == "deep"
    assert live.json()["model_revision"] == saved["model_revision"]
    deleted = models.client.delete("/api/sessions/public")
    assert deleted.status_code == 200, deleted.text
    assert models.read_model("public")["model_revision"] == 0


@pytest.mark.parametrize("method", ["session.create", "session.resume", "session.model.set"])
def test_browser_websocket_cannot_supply_runtime_model_configuration(models, method):
    with models.client.websocket_connect("/api/tui/ws") as socket:
        socket.send_json({"id": "injected", "method": method, "params": {
            "session_id": "public", "model_config": {"model": "unapproved", "api_key": "injected"},
        }})
        result = socket.receive_json()
    assert result["type"] == "rpc.error"
    assert models.bridge.calls == []

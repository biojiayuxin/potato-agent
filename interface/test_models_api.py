from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _DummyBridgeRegistry:
    def __init__(self) -> None:
        self.closed_user_ids: list[str] = []

    async def get_existing(self, user_id: str):
        return None

    async def close_for_reconfigure(self, user_id: str) -> bool:
        self.closed_user_ids.append(user_id)
        return True


def _make_temp_env() -> tuple[str, str, Path]:
    base_dir = Path(tempfile.mkdtemp(prefix="potato-interface-models-test-"))
    auth_db = str(base_dir / "interface.db")
    mapping_path = str(base_dir / "users_mapping.yaml")
    home_dir = base_dir / "hmx_alice"
    hermes_home = home_dir / ".hermes"
    workdir = home_dir / "work"
    hermes_home.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    Path(mapping_path).write_text(
        f"""
start_port: 8643
hermes:
  api_server_host: 127.0.0.1
  api_server_model_name: Hermes
  model:
    default: gpt-5.4
    provider: custom
    base_url: https://primary.example/v1
    api_key: sk-primary
  extra_env:
    OPENAI_API_KEY: sk-primary
  model_options:
    primary: primary
    options:
      - id: primary
        name: Main
        provider: custom
        model: gpt-5.4
        base_url: https://primary.example/v1
        api_key: sk-primary
      - id: fast
        name: Fast
        provider: custom
        model: gpt-5.4-mini
        base_url: https://fast.example/v1
        api_key: sk-fast
        context_length: 500000
        api_mode: codex_responses
users:
  - username: alice
    email: alice@example.com
    display_name: Alice
    linux_user: hmx_alice
    home_dir: {home_dir}
    hermes_home: {hermes_home}
    workdir: {workdir}
    api_port: 8655
    api_key: sk-user
    api_server_model_name: Hermes
    systemd_service: hermes-alice.service
""".lstrip(),
        encoding="utf-8",
    )
    return auth_db, mapping_path, hermes_home


def _build_client_and_user(monkeypatch):
    auth_db, mapping_path, hermes_home = _make_temp_env()
    os.environ["INTERFACE_AUTH_DB"] = auth_db
    os.environ["POTATO_AGENT_MAPPING_PATH"] = mapping_path
    os.environ["INTERFACE_SESSION_SECRET"] = "test-secret"

    for module_name in list(sys.modules):
        if module_name == "interface" or module_name.startswith("interface."):
            sys.modules.pop(module_name, None)

    import interface.auth_db as auth_db_mod
    from interface import app as interface_app_mod

    importlib.reload(auth_db_mod)
    importlib.reload(interface_app_mod)

    interface_app_mod.ensure_auth_db()
    interface_app_mod.ensure_display_store()
    interface_app_mod.app.state.tui_gateway_bridges = _DummyBridgeRegistry()
    user = auth_db_mod.upsert_user(
        username="alice",
        email="alice@example.com",
        password="password123",
        mapping_username="alice",
        name="Alice",
    )

    monkeypatch.setattr(
        "interface.model_options.pwd.getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(
        "interface.model_options._set_owner_and_mode",
        lambda path, uid, gid, mode: None,
    )
    monkeypatch.setattr("interface.model_options.atomic_yaml_write", None)

    client = TestClient(interface_app_mod.app)
    token = interface_app_mod._create_session_token(user.id)
    client.cookies.set(interface_app_mod.SESSION_COOKIE_NAME, token)
    return client, interface_app_mod, user, hermes_home


def test_get_models_returns_whitelist_without_api_keys(monkeypatch) -> None:
    client, _, _, hermes_home = _build_client_and_user(monkeypatch)
    try:
        (hermes_home / "config.yaml").write_text(
            """
model:
  default: gpt-5.4-mini
  provider: custom
  base_url: https://fast.example/v1
  api_key: sk-fast
  context_length: 500000
  api_mode: codex_responses
""".lstrip(),
            encoding="utf-8",
        )

        response = client.get("/api/models")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["active_id"] == "fast"
        assert [model["id"] for model in payload["data"]] == ["primary", "fast"]
        assert payload["data"][0]["is_primary"] is True
        assert payload["data"][1]["is_active"] is True
        assert "base_url" not in payload["data"][0]
        assert "api_key" not in payload["data"][0]
        assert "https://primary.example" not in response.text
        assert "sk-primary" not in response.text
        assert "sk-fast" not in response.text
    finally:
        client.close()


def test_get_models_reads_active_model_through_helper_when_not_root(monkeypatch) -> None:
    client, interface_app_mod, _, _ = _build_client_and_user(monkeypatch)
    calls: list[str] = []

    def fake_get_active_model_id(username: str) -> str:
        calls.append(username)
        return "fast"

    monkeypatch.setattr(interface_app_mod.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        interface_app_mod.privileged_client,
        "get_active_model_id",
        fake_get_active_model_id,
    )

    try:
        response = client.get("/api/models")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["active_id"] == "fast"
        assert payload["data"][1]["is_active"] is True
        assert calls == ["alice"]
    finally:
        client.close()


def test_authenticated_api_request_refreshes_idle_activity(monkeypatch) -> None:
    client, _, user, _ = _build_client_and_user(monkeypatch)
    try:
        from interface.runtime_state import (
            ensure_runtime_state_store,
            get_runtime_state,
            mark_runtime_started,
        )

        db_path = Path(os.environ["INTERFACE_AUTH_DB"])
        ensure_runtime_state_store(db_path)
        mark_runtime_started(user.id, db_path=db_path)
        old_activity_at = int(time.time()) - 3600
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                UPDATE runtime_state
                SET runtime_started_at = ?,
                    last_user_message_at = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (old_activity_at, old_activity_at, old_activity_at, user.id),
            )
            conn.commit()

        response = client.get("/api/models")
        assert response.status_code == 200, response.text
        state = get_runtime_state(user.id, db_path=db_path)
        assert state is not None
        assert int(state["last_user_message_at"]) > old_activity_at
    finally:
        client.close()


def test_auth_session_poll_does_not_refresh_idle_activity(monkeypatch) -> None:
    client, _, user, _ = _build_client_and_user(monkeypatch)
    try:
        from interface.runtime_state import (
            ensure_runtime_state_store,
            get_runtime_state,
            mark_runtime_started,
        )

        db_path = Path(os.environ["INTERFACE_AUTH_DB"])
        ensure_runtime_state_store(db_path)
        mark_runtime_started(user.id, db_path=db_path)
        old_activity_at = int(time.time()) - 3600
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                UPDATE runtime_state
                SET runtime_started_at = ?,
                    last_user_message_at = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (old_activity_at, old_activity_at, old_activity_at, user.id),
            )
            conn.commit()

        response = client.get("/api/auth/session")
        assert response.status_code == 200, response.text
        state = get_runtime_state(user.id, db_path=db_path)
        assert state is not None
        assert int(state["last_user_message_at"]) == old_activity_at
    finally:
        client.close()


def test_put_active_model_rejects_non_whitelist_id(monkeypatch) -> None:
    client, _, _, _ = _build_client_and_user(monkeypatch)
    try:
        response = client.put("/api/models/active", json={"id": "not-allowed"})
        assert response.status_code == 400
        assert response.json()["detail"] == "Model is not allowed"
    finally:
        client.close()


def test_put_active_model_rejects_active_live_session(monkeypatch) -> None:
    client, _, user, _ = _build_client_and_user(monkeypatch)
    try:
        from interface.display_store import save_live_session_state

        save_live_session_state(
            user.id,
            "session-1",
            run_id="run-1",
            live_session_id="live-1",
            assistant_message_id="assistant-1",
            status="awaiting_approval",
            pending_approval={"command": "echo ok"},
            last_error="",
            last_event_seq=1,
            db_path=Path(os.environ["INTERFACE_AUTH_DB"]),
        )

        response = client.put("/api/models/active", json={"id": "fast"})
        assert response.status_code == 409
        assert "Cannot switch models" in response.json()["detail"]
    finally:
        client.close()


def test_put_active_model_updates_user_config(monkeypatch) -> None:
    client, _, _, hermes_home = _build_client_and_user(monkeypatch)
    try:
        response = client.put("/api/models/active", json={"id": "fast"})
        assert response.status_code == 200, response.text
        assert response.json()["active_id"] == "fast"

        config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
        assert config["model"] == {
            "default": "gpt-5.4-mini",
            "provider": "custom",
            "base_url": "https://fast.example/v1",
            "api_key": "sk-fast",
            "context_length": 500000,
            "api_mode": "codex_responses",
        }
        assert config["agent"] == {"reasoning_effort": "xhigh"}
        assert (hermes_home / ".env").read_text(encoding="utf-8") == (
            "OPENAI_API_KEY=sk-fast\n"
        )
    finally:
        client.close()

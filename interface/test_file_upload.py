from __future__ import annotations

from contextlib import contextmanager
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

import interface.auth_db as auth_db_mod
import interface.display_store as display_store_mod
import interface.mapping as mapping_mod
import interface.runtime_state as runtime_state_mod
from interface import app as interface_app_mod


class _DummyBridgeRegistry:
    async def get_existing(self, user_id: str):
        return None

    async def close_for_reconfigure(self, user_id: str) -> bool:
        return True


class _TurnBridge:
    user_id = "user-id"

    async def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "session.create":
            return {"session_id": "live-1"}
        if method == "session.title":
            return {"session_key": "logical-1"}
        raise AssertionError(f"unexpected bridge method: {method}")


class _TurnBridgeRegistry:
    def __init__(self) -> None:
        self.bridge = _TurnBridge()

    async def get_or_create(self, user_id: str, target):
        return self.bridge

    async def maybe_close_if_unused(self, user_id: str) -> None:
        return None


class _TurnRunManager:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, Any]] = []

    async def attach_bridge(self, bridge) -> None:
        return None

    async def ensure_session_bound(self, **kwargs: Any) -> None:
        return None

    async def submit_turn(self, **kwargs: Any) -> dict[str, Any]:
        self.submit_calls.append(kwargs)
        return {
            "run_id": "run-1",
            "session_id": kwargs["session_id"],
            "live_session_id": kwargs["live_session_id"],
            "assistant_message_id": "assistant-1",
            "messages": [
                {
                    "id": "user-1",
                    "role": "user",
                    "content": kwargs["prompt"],
                    "done": True,
                },
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "",
                    "done": False,
                },
            ],
            "live": None,
        }


class _FakeSessionDb:
    def get_session(self, session_id: str) -> dict[str, Any]:
        return {
            "id": session_id,
            "source": "tui",
            "title": "Draft",
            "preview": "",
            "started_at": 0,
            "last_active": 0,
        }

    def close(self) -> None:
        return None


@contextmanager
def _fake_open_session_db(target):
    yield _FakeSessionDb()


def _build_client_and_user(monkeypatch):
    base_dir = Path(tempfile.mkdtemp(prefix="potato-interface-upload-test-"))
    auth_db = base_dir / "interface.db"
    mapping_path = base_dir / "users_mapping.yaml"
    home_dir = base_dir / "hmx_alice"
    hermes_home = home_dir / ".hermes"
    workdir = home_dir / "work"
    hermes_home.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(
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

    monkeypatch.setenv("INTERFACE_AUTH_DB", str(auth_db))
    monkeypatch.setenv("POTATO_AGENT_MAPPING_PATH", str(mapping_path))
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "test-secret")
    monkeypatch.setattr(auth_db_mod, "DEFAULT_AUTH_DB_PATH", auth_db)
    monkeypatch.setattr(display_store_mod, "DEFAULT_AUTH_DB_PATH", auth_db)
    monkeypatch.setattr(runtime_state_mod, "DEFAULT_AUTH_DB_PATH", auth_db)
    monkeypatch.setattr(mapping_mod, "DEFAULT_MAPPING_PATH", mapping_path)
    monkeypatch.setattr(interface_app_mod, "DEFAULT_MAPPING_PATH", mapping_path)
    interface_app_mod.mapping_store = mapping_mod.MappingStore(mapping_path)
    monkeypatch.setattr(
        interface_app_mod,
        "get_user_by_id",
        lambda user_id: auth_db_mod.get_user_by_id(user_id, db_path=auth_db),
    )
    monkeypatch.setattr(
        interface_app_mod,
        "get_runtime_state",
        lambda user_id: runtime_state_mod.get_runtime_state(user_id, db_path=auth_db),
    )
    monkeypatch.setattr(
        interface_app_mod,
        "mark_foreground_activity",
        lambda user_id: runtime_state_mod.mark_foreground_activity(user_id, db_path=auth_db),
    )
    monkeypatch.setattr(interface_app_mod.os, "geteuid", lambda: 0)
    interface_app_mod.privileged_client.force_helper = False
    interface_app_mod.app.state.tui_gateway_bridges = _DummyBridgeRegistry()

    auth_db_mod.ensure_auth_db(auth_db)
    display_store_mod.ensure_display_store(auth_db)
    user = auth_db_mod.upsert_user(
        username="alice",
        email="alice@example.com",
        password="password123",
        mapping_username="alice",
        name="Alice",
        db_path=auth_db,
    )

    monkeypatch.setattr(
        interface_app_mod.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456),
    )
    monkeypatch.setattr(interface_app_mod.os, "chown", lambda path, uid, gid: None)

    client = TestClient(interface_app_mod.app)
    token = interface_app_mod._create_session_token(user.id)
    client.cookies.set(interface_app_mod.SESSION_COOKIE_NAME, token)
    return client, home_dir


def test_upload_file_stores_attachment(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    try:
        response = client.post(
            "/api/files/upload",
            files={"file": ("notes.txt", b"hello potato", "text/plain")},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["name"] == "notes.txt"
        assert payload["size"] == len(b"hello potato")
        assert payload["content_type"] == "text/plain"
        assert Path(payload["path"]).read_bytes() == b"hello potato"
        assert Path(payload["path"]).is_relative_to(home_dir)
    finally:
        client.close()


def test_upload_file_rejects_file_over_limit(monkeypatch) -> None:
    client, _ = _build_client_and_user(monkeypatch)
    monkeypatch.setattr(interface_app_mod, "MAX_UPLOAD_SIZE_BYTES", 1024 * 1024)
    try:
        response = client.post(
            "/api/files/upload",
            files={"file": ("large.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
        )
        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "Upload file too large (> 1 MB)."
    finally:
        client.close()


def test_submit_turn_rejects_total_attachment_size_over_limit(monkeypatch) -> None:
    client, _ = _build_client_and_user(monkeypatch)
    monkeypatch.setattr(interface_app_mod, "MAX_UPLOAD_SIZE_BYTES", 10 * 1024 * 1024)
    try:
        response = client.post(
            "/api/sessions/draft/turns",
            json={
                "prompt": "",
                "attachments": [
                    {
                        "name": "a.txt",
                        "size": 6 * 1024 * 1024,
                        "localPath": "/tmp/a.txt",
                    },
                    {
                        "name": "b.txt",
                        "size": 5 * 1024 * 1024,
                        "localPath": "/tmp/b.txt",
                    },
                ],
            },
        )
        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "Total attachment size too large (> 10 MB)."
    finally:
        client.close()


def test_submit_turn_rejects_invalid_mode(monkeypatch) -> None:
    client, _ = _build_client_and_user(monkeypatch)
    try:
        response = client.post(
            "/api/sessions/draft/turns",
            json={"prompt": "hello", "mode": "pla n"},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "Invalid turn mode"
    finally:
        client.close()


def test_submit_turn_passes_plan_mode_to_run_manager(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    auth_db = home_dir.parent / "interface.db"
    run_manager = _TurnRunManager()
    monkeypatch.setattr(
        interface_app_mod.app.state,
        "tui_gateway_bridges",
        _TurnBridgeRegistry(),
        raising=False,
    )
    monkeypatch.setattr(
        interface_app_mod.app.state,
        "session_run_manager",
        run_manager,
        raising=False,
    )
    monkeypatch.setattr(interface_app_mod, "_open_session_db", _fake_open_session_db)
    monkeypatch.setattr(
        interface_app_mod,
        "get_display_messages",
        lambda user_id, session_id: display_store_mod.get_display_messages(
            user_id,
            session_id,
            db_path=auth_db,
        ),
    )
    monkeypatch.setattr(
        interface_app_mod,
        "get_display_session_meta",
        lambda user_id, session_id: display_store_mod.get_display_session_meta(
            user_id,
            session_id,
            db_path=auth_db,
        ),
    )
    monkeypatch.setattr(
        interface_app_mod,
        "get_live_session_state",
        lambda user_id, session_id: display_store_mod.get_live_session_state(
            user_id,
            session_id,
            db_path=auth_db,
        ),
    )
    try:
        response = client.post(
            "/api/sessions/draft/turns",
            json={"prompt": "make a plan", "mode": "plan"},
        )
        assert response.status_code == 200, response.text
    finally:
        client.close()

    assert len(run_manager.submit_calls) == 1
    assert run_manager.submit_calls[0]["mode"] == "plan"

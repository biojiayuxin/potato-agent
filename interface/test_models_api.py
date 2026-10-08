from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import interface.auth_db as auth_db_mod
import interface.display_store as display_store_mod
import interface.mapping as mapping_mod
import interface.runtime_state as runtime_state_mod
from interface import app as interface_app_mod


def test_removed_user_model_api_cannot_mutate_runtime(monkeypatch):
    client, app_mod, _, hermes_home = _build_client_and_user(monkeypatch)
    try:
        config = hermes_home / "config.yaml"
        config.write_text("model:\n  default: fast\n")
        response = client.put("/api/models/active", json={"id": "deep"})
        assert response.status_code == 404
        assert config.read_text() == "model:\n  default: fast\n"
        assert app_mod.app.state.tui_gateway_bridges.closed_user_ids == []
    finally:
        client.close()


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
  model_catalog: true
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
    model_proxy_token: pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz
    api_server_model_name: Hermes
    systemd_service: hermes-alice.service
""".lstrip(),
        encoding="utf-8",
    )
    return auth_db, mapping_path, hermes_home


def _build_client_and_user(monkeypatch):
    auth_db, mapping_path, hermes_home = _make_temp_env()
    auth_db_path = Path(auth_db)
    mapping_file = Path(mapping_path)

    monkeypatch.setenv("INTERFACE_AUTH_DB", auth_db)
    monkeypatch.setenv("POTATO_AGENT_MAPPING_PATH", mapping_path)
    monkeypatch.setenv("INTERFACE_SESSION_SECRET", "test-secret")
    monkeypatch.setattr(auth_db_mod, "DEFAULT_AUTH_DB_PATH", auth_db_path)
    monkeypatch.setattr(display_store_mod, "DEFAULT_AUTH_DB_PATH", auth_db_path)
    monkeypatch.setattr(runtime_state_mod, "DEFAULT_AUTH_DB_PATH", auth_db_path)
    monkeypatch.setattr(mapping_mod, "DEFAULT_MAPPING_PATH", mapping_file)
    monkeypatch.setattr(interface_app_mod, "DEFAULT_MAPPING_PATH", mapping_file)
    interface_app_mod.mapping_store = mapping_mod.MappingStore(mapping_file)
    monkeypatch.setattr(
        interface_app_mod,
        "get_user_by_id",
        lambda user_id: auth_db_mod.get_user_by_id(user_id, db_path=auth_db_path),
    )
    monkeypatch.setattr(
        interface_app_mod,
        "get_runtime_state",
        lambda user_id: runtime_state_mod.get_runtime_state(user_id, db_path=auth_db_path),
    )
    monkeypatch.setattr(
        interface_app_mod,
        "mark_foreground_activity",
        lambda user_id: runtime_state_mod.mark_foreground_activity(user_id, db_path=auth_db_path),
    )
    monkeypatch.setattr(
        interface_app_mod,
        "list_live_session_states",
        lambda user_id: display_store_mod.list_live_session_states(user_id, db_path=auth_db_path),
    )
    monkeypatch.setattr(interface_app_mod.os, "geteuid", lambda: 0)
    interface_app_mod.privileged_client.force_helper = False

    auth_db_mod.ensure_auth_db(auth_db_path)
    display_store_mod.ensure_display_store(auth_db_path)
    interface_app_mod.app.state.tui_gateway_bridges = _DummyBridgeRegistry()
    user = auth_db_mod.upsert_user(
        username="alice",
        email="alice@example.com",
        password="password123",
        mapping_username="alice",
        name="Alice",
        db_path=auth_db_path,
    )

    from interface.test_model_support import install_catalog, make_catalog
    install_catalog(monkeypatch, make_catalog())
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
  base_url: http://127.0.0.1:8765/v1
  api_key: pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz
  context_length: 500000
  api_mode: codex_responses
""".lstrip(),
            encoding="utf-8",
        )

        response = client.get("/api/models")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "active_id" not in payload
        assert [model["id"] for model in payload["data"]] == ["deep", "fast"]
        assert payload["data"][0]["is_primary"] is True
        assert all("is_active" not in item for item in payload["data"])
        assert "base_url" not in payload["data"][0]
        assert "api_key" not in payload["data"][0]
        assert "https://primary.example" not in response.text
        assert "sk-primary" not in response.text
        assert "sk-fast" not in response.text
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


def test_live_session_poll_is_lightweight_and_does_not_refresh_activity(
    monkeypatch,
) -> None:
    client, interface_app_mod, user, _ = _build_client_and_user(monkeypatch)
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

        display_store_mod.save_display_messages(
            user.id,
            "session-1",
            [{"id": "m1", "role": "assistant", "content": "working"}],
            db_path=db_path,
        )
        display_store_mod.save_live_session_state(
            user.id,
            "session-1",
            run_id="run-1",
            live_session_id="live-1",
            assistant_message_id="m1",
            status="awaiting_approval",
            pending_approval={
                "approval_id": "run-1:3",
                "command": "echo ok",
                "description": "Approve",
            },
            db_path=db_path,
        )
        monkeypatch.setattr(
            interface_app_mod,
            "get_live_session_state",
            lambda user_id, session_id: display_store_mod.get_live_session_state(
                user_id, session_id, db_path=db_path
            ),
        )
        monkeypatch.setattr(
            interface_app_mod,
            "get_display_session_meta",
            lambda user_id, session_id: display_store_mod.get_display_session_meta(
                user_id, session_id, db_path=db_path
            ),
        )
        monkeypatch.setattr(
            interface_app_mod,
            "get_display_messages",
            lambda user_id, session_id: display_store_mod.get_display_messages(
                user_id, session_id, db_path=db_path
            ),
        )
        monkeypatch.setattr(
            interface_app_mod,
            "get_live_poll_snapshot",
            lambda user_id, session_id, **kwargs: display_store_mod.get_live_poll_snapshot(
                user_id,
                session_id,
                db_path=db_path,
                **kwargs,
            ),
        )
        monkeypatch.setattr(
            interface_app_mod,
            "find_live_session_id_by_run_id",
            lambda user_id, run_id: display_store_mod.find_live_session_id_by_run_id(
                user_id, run_id, db_path=db_path
            ),
        )
        monkeypatch.setattr(
            interface_app_mod,
            "get_turn_submission_receipt",
            lambda user_id, request_id: display_store_mod.get_turn_submission_receipt(
                user_id, request_id, db_path=db_path
            ),
        )
        monkeypatch.setattr(
            interface_app_mod,
            "get_session_pin_state",
            lambda user_id, session_id: display_store_mod.get_session_pin_state(
                user_id, session_id, db_path=db_path,
            ),
        )
        visibility_checks: list[str] = []

        class _VisibilityOnlyDB:
            def is_internal_session(self, session_id: str) -> bool:
                visibility_checks.append(session_id)
                return False

            def __getattr__(self, name: str):
                raise AssertionError(
                    f"live polling may only check session visibility, attempted {name}"
                )

        monkeypatch.setattr(
            interface_app_mod,
            "_open_session_db",
            lambda _target: contextlib.nullcontext(_VisibilityOnlyDB()),
        )

        response = client.get("/api/sessions/session-1/live")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["session_id"] == "session-1"
        assert payload["messages"][0]["content"] == "working"
        assert payload["live"]["status"] == "awaiting_approval"
        assert payload["live"]["pending_approval"]["command"] == "echo ok"
        assert payload["live"]["pending_approval"]["approval_id"] == "run-1:3"
        assert payload["file_tree_refresh"] is False
        unchanged_response = client.get(
            "/api/sessions/session-1/live?after_run_id=run-1&after_event_seq=0"
        )
        assert unchanged_response.status_code == 200
        assert unchanged_response.json()["messages"] is None
        assert unchanged_response.json()["file_tree_refresh"] is False
        display_store_mod.append_session_event(
            user.id,
            "session-1",
            run_id="run-1",
            seq=1,
            event_type="tool.complete",
            db_path=db_path,
        )
        display_store_mod.save_live_session_state(
            user.id,
            "session-1",
            last_event_seq=1,
            last_workspace_event_seq=1,
            db_path=db_path,
        )
        changed_response = client.get(
            "/api/sessions/session-1/live?after_run_id=run-1&after_event_seq=0"
        )
        assert changed_response.status_code == 200
        assert changed_response.json()["file_tree_refresh"] is True
        assert visibility_checks == ["session-1"] * 3

        class _RevisionManager:
            async def get_workspace_change_revision(self, user_id: str) -> str:
                assert user_id == user.id
                return "revision-1"

        monkeypatch.setattr(
            interface_app_mod.app.state,
            "session_run_manager",
            _RevisionManager(),
            raising=False,
        )
        revision_response = client.get("/api/files/revision")
        assert revision_response.status_code == 200
        assert revision_response.json() == {"revision": "revision-1"}
        pinned = display_store_mod.set_session_pinned(user.id, "session-1", True, db_path=db_path)
        recovered_response = client.get("/api/turns/run-1")
        assert recovered_response.status_code == 200
        assert recovered_response.json()["session"]["id"] == "session-1"
        assert recovered_response.json()["session"]["pin_order"] == pinned["pin_order"]
        assert recovered_response.json()["live"]["run_id"] == "run-1"
        display_store_mod.create_turn_submission_receipt(
            user.id,
            "pending-request",
            requested_session_id="draft",
            db_path=db_path,
        )
        pending_response = client.get("/api/turns/pending-request")
        assert pending_response.status_code == 202
        assert pending_response.json()["pending"] is True
        assert pending_response.json()["expires_at"] > int(time.time())
        display_store_mod.create_turn_submission_receipt(
            user.id,
            "old-request",
            requested_session_id="session-1",
            db_path=db_path,
        )
        display_store_mod.finish_turn_submission_receipt(
            user.id,
            "old-request",
            session_id="session-1",
            db_path=db_path,
        )
        stale_response = client.get("/api/turns/old-request")
        assert stale_response.status_code == 409
        assert "no longer the current run" in stale_response.json()["detail"]
        state = get_runtime_state(user.id, db_path=db_path)
        assert state is not None
        assert int(state["last_user_message_at"]) == old_activity_at
    finally:
        client.close()

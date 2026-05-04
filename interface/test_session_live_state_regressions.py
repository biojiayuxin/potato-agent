from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import importlib
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _make_temp_env() -> tuple[str, str, Path]:
    base_dir = Path(tempfile.mkdtemp(prefix="potato-interface-live-state-test-"))
    auth_db = str(base_dir / "interface.db")
    mapping_path = str(base_dir / "users_mapping.yaml")
    hermes_home = base_dir / "hmx_alice" / ".hermes"
    hermes_home.mkdir(parents=True, exist_ok=True)
    Path(mapping_path).write_text(
        f"""
hermes:
  api_server_host: 127.0.0.1
users:
  - username: alice
    email: alice@example.com
    display_name: Alice
    linux_user: hmx_alice
    home_dir: {base_dir / 'hmx_alice'}
    hermes_home: {hermes_home}
    workdir: {base_dir / 'hmx_alice' / 'work'}
    api_port: 8655
    api_key: sk-user
    api_server_model_name: Hermes
    systemd_service: hermes-alice.service
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return auth_db, mapping_path, hermes_home / "state.db"


def _seed_state_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                user_id TEXT,
                model TEXT,
                model_config TEXT,
                system_prompt TEXT,
                parent_session_id TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,
                billing_provider TEXT,
                billing_base_url TEXT,
                billing_mode TEXT,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                cost_status TEXT,
                cost_source TEXT,
                pricing_version TEXT,
                title TEXT,
                api_call_count INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_details TEXT,
                codex_reasoning_items TEXT,
                reasoning_content TEXT
            )
            """
        )
        conn.execute(
            "CREATE TABLE compression_tips (root_session_id TEXT PRIMARY KEY, tip_session_id TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO sessions (id, source, model, started_at, message_count, tool_call_count, title) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sess_live_1", "tui", "Hermes", 1714500000, 2, 0, ""),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            ("sess_live_1", "user", "hello", 1714500001, "", ""),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            ("sess_live_1", "assistant", "world", 1714500002, "", ""),
        )
        conn.commit()
    finally:
        conn.close()


def _build_client_and_user():
    auth_db, mapping_path, state_db_path = _make_temp_env()
    os.environ["INTERFACE_AUTH_DB"] = auth_db
    os.environ["POTATO_AGENT_MAPPING_PATH"] = mapping_path
    os.environ["INTERFACE_SESSION_SECRET"] = "test-secret"
    _seed_state_db(state_db_path)

    for module_name in list(sys.modules):
        if module_name == "interface" or module_name.startswith("interface."):
            sys.modules.pop(module_name, None)

    import interface.auth_db as auth_db_mod
    from interface import app as interface_app_mod

    importlib.reload(auth_db_mod)
    importlib.reload(interface_app_mod)

    interface_app_mod.ensure_auth_db()
    interface_app_mod.ensure_display_store()
    user = auth_db_mod.upsert_user(
        username="alice",
        email="alice@example.com",
        password="password123",
        mapping_username="alice",
        name="Alice",
    )

    client = TestClient(interface_app_mod.app)
    token = interface_app_mod._create_session_token(user.id)
    client.cookies.set(interface_app_mod.SESSION_COOKIE_NAME, token)
    return client, interface_app_mod, user


def test_session_detail_exposes_live_state() -> None:
    client, _, user = _build_client_and_user()
    try:
        from interface.display_store import save_display_messages, save_live_session_state

        save_display_messages(
            user.id,
            "sess_live_1",
            [
                {"id": "user-1", "role": "user", "content": "hello", "done": True},
                {"id": "assistant-1", "role": "assistant", "content": "partial", "done": False},
            ],
            draft_title="hello",
        )
        save_live_session_state(
            user.id,
            "sess_live_1",
            run_id="run-1",
            live_session_id="live-1",
            assistant_message_id="assistant-1",
            status="running",
            pending_approval=None,
            last_error="",
            last_event_seq=3,
            db_path=Path(os.environ["INTERFACE_AUTH_DB"]),
        )

        response = client.get("/api/sessions/sess_live_1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["live"]["status"] == "running"
        assert payload["live"]["live_session_id"] == "live-1"
        assert payload["session"]["is_running"] is True
    finally:
        client.close()


def test_session_list_includes_display_only_live_session() -> None:
    client, _, user = _build_client_and_user()
    try:
        from interface.display_store import save_display_messages, save_live_session_state

        save_display_messages(
            user.id,
            "sess_display_only",
            [
                {"id": "user-1", "role": "user", "content": "draft", "done": True},
                {"id": "assistant-1", "role": "assistant", "content": "", "done": False},
            ],
            draft_title="draft title",
        )
        save_live_session_state(
            user.id,
            "sess_display_only",
            run_id="run-display",
            live_session_id="live-display",
            assistant_message_id="assistant-1",
            status="starting",
            pending_approval=None,
            last_error="",
            last_event_seq=1,
            db_path=Path(os.environ["INTERFACE_AUTH_DB"]),
        )

        response = client.get("/api/sessions")
        assert response.status_code == 200, response.text
        payload = response.json()
        session_row = next(
            session for session in payload["sessions"] if session["id"] == "sess_display_only"
        )
        assert session_row["title"] == "draft title"
        assert session_row["is_running"] is True
        assert session_row["live"]["live_session_id"] == "live-display"
    finally:
        client.close()


def run() -> None:
    test_session_detail_exposes_live_state()
    test_session_list_includes_display_only_live_session()


if __name__ == "__main__":
    run()

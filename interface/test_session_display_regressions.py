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
    base_dir = Path(tempfile.mkdtemp(prefix="potato-interface-session-test-"))
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
            ("sess_tui_1", "tui", "Hermes", 1714500000, 4, 1, ""),
        )
        conn.execute(
            "INSERT INTO sessions (id, source, model, started_at, ended_at, end_reason, message_count, tool_call_count, title) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sess_tui_root", "tui", "Hermes", 1714501000, 1714501050, "compression", 2, 1, "compression root"),
        )
        conn.execute(
            "INSERT INTO sessions (id, source, model, started_at, parent_session_id, message_count, tool_call_count, title) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("sess_tui_tip", "tui", "Hermes", 1714501051, "sess_tui_root", 2, 0, "compression tip"),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_1",
                "user",
                "列出 test_dir 中的文件",
                1714500001,
                "",
                "",
            ),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_1",
                "assistant",
                "`🛠️ list_directory test_dir`",
                1714500002,
                "need to inspect the folder",
                '[{"id":"call_1","index":0,"function":{"name":"list_directory","arguments":"{\"path\":\"test_dir\"}"}}]',
            ),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_name) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_1",
                "tool",
                '{"files":["test.py","test2.py"]}',
                1714500003,
                "",
                "list_directory",
            ),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_1",
                "assistant",
                "test_dir 里有 2 个文件：test.py 和 test2.py",
                1714500004,
                "",
                "",
            ),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_root",
                "user",
                "这是一个需要压缩的超长对话",
                1714501001,
                "",
                "",
            ),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_root",
                "assistant",
                "`🛠️ pre-compression tool`",
                1714501002,
                "",
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_tip",
                "user",
                "压缩之后继续回答",
                1714501052,
                "",
                "",
            ),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, reasoning, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess_tui_tip",
                "assistant",
                "压缩后的新 tip session 仍在继续",
                1714501053,
                "",
                "",
            ),
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


def test_session_list_uses_display_store_draft_title() -> None:
    client, _, user = _build_client_and_user()
    try:
        from interface.display_store import save_display_messages

        save_display_messages(
            user.id,
            "sess_tui_1",
            [],
            draft_title="列出 test_dir",
        )

        response = client.get("/api/sessions")
        assert response.status_code == 200, response.text
        payload = response.json()
        session_row = next(
            session for session in payload["sessions"] if session["id"] == "sess_tui_1"
        )
        assert session_row["title"] == "列出 test_dir"
    finally:
        client.close()


def test_session_detail_prefers_saved_display_transcript() -> None:
    client, _, user = _build_client_and_user()
    try:
        from interface.display_store import save_display_messages

        save_display_messages(
            user.id,
            "sess_tui_1",
            [
                {
                    "id": "user-1",
                    "role": "user",
                    "content": "列出 test_dir 中的文件",
                    "progressLines": [],
                    "done": True,
                },
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "test_dir 里有 2 个文件：test.py 和 test2.py",
                    "progressLines": ["`🛠️ list_directory test_dir`"],
                    "done": True,
                },
            ],
            draft_title="列出 test_dir",
        )

        response = client.get("/api/sessions/sess_tui_1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["session"]["title"] == "列出 test_dir"
        assert len(payload["messages"]) == 2
        assert payload["messages"][1]["progressLines"] == ["`🛠️ list_directory test_dir`"]
        assert payload["messages"][1]["content"] == "test_dir 里有 2 个文件：test.py 和 test2.py"
    finally:
        client.close()


def test_display_sync_merges_progress_with_fallback_tool_context() -> None:
    client, _, _ = _build_client_and_user()
    try:
        response = client.put(
            "/api/sessions/sess_tui_1/display",
            json={
                "draft_title": "列出 test_dir",
                "messages": [
                    {
                        "id": "user-1",
                        "role": "user",
                        "content": "列出 test_dir 中的文件",
                        "progressLines": [],
                        "done": True,
                    },
                    {
                        "id": "assistant-1",
                        "role": "assistant",
                        "content": "test_dir 里有 2 个文件：test.py 和 test2.py",
                        "progressLines": ["`🛠️ list_directory test_dir`"],
                        "done": True,
                    },
                ],
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["messages"][1]["progressLines"] == ["`🛠️ list_directory test_dir`"]
        assert payload["messages"][1]["content"] == "test_dir 里有 2 个文件：test.py 和 test2.py"
    finally:
        client.close()


def test_session_detail_falls_back_when_display_transcript_missing() -> None:
    client, _, _ = _build_client_and_user()
    try:
        response = client.get("/api/sessions/sess_tui_1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["session"]["id"] == "sess_tui_1"
        assert len(payload["messages"]) == 2
        assert payload["messages"][1]["progressLines"] == ["`🛠️ list_directory test_dir`"]
        assert payload["messages"][1]["content"] == "test_dir 里有 2 个文件：test.py 和 test2.py"
    finally:
        client.close()


def test_tui_compression_chain_uses_logical_root_and_tip_resume_id() -> None:
    client, _, _ = _build_client_and_user()
    try:
        response = client.get("/api/sessions")
        assert response.status_code == 200, response.text
        payload = response.json()

        compression_row = next(
            session for session in payload["sessions"] if session["id"] == "sess_tui_root"
        )
        assert compression_row["resume_session_id"] == "sess_tui_tip"

        detail_response = client.get("/api/sessions/sess_tui_root")
        assert detail_response.status_code == 200, detail_response.text
        detail_payload = detail_response.json()
        assert detail_payload["session"]["id"] == "sess_tui_root"
        assert detail_payload["session"]["resume_session_id"] == "sess_tui_tip"
        assert detail_payload["messages"][-1]["content"] == "压缩后的新 tip session 仍在继续"
    finally:
        client.close()


def run() -> None:
    test_session_list_uses_display_store_draft_title()
    test_session_detail_prefers_saved_display_transcript()
    test_display_sync_merges_progress_with_fallback_tool_context()
    test_session_detail_falls_back_when_display_transcript_missing()
    test_tui_compression_chain_uses_logical_root_and_tip_resume_id()


if __name__ == "__main__":
    run()

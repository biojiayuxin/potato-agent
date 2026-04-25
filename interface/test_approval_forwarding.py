from __future__ import annotations

import sys
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _make_temp_env() -> tuple[str, str]:
    base_dir = tempfile.mkdtemp(prefix="potato-interface-test-")
    auth_db = str(Path(base_dir) / "interface.db")
    mapping_path = str(Path(base_dir) / "users_mapping.yaml")
    Path(mapping_path).write_text(
        """
hermes:
  api_server_host: 127.0.0.1
users:
  - username: alice
    email: alice@example.com
    display_name: Alice
    linux_user: hmx_alice
    home_dir: /tmp/hmx_alice
    hermes_home: /tmp/hmx_alice/.hermes
    workdir: /tmp/hmx_alice/work
    api_port: 8655
    api_key: sk-user
    api_server_model_name: Hermes
    systemd_service: hermes-alice.service
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return auth_db, mapping_path


def run() -> None:
    import os

    auth_db, mapping_path = _make_temp_env()
    os.environ["INTERFACE_AUTH_DB"] = auth_db
    os.environ["POTATO_AGENT_MAPPING_PATH"] = mapping_path
    os.environ["INTERFACE_SESSION_SECRET"] = "test-secret"

    from interface import app as interface_app_mod
    from interface.auth_db import ensure_auth_db, upsert_user

    interface_app_mod.ensure_auth_db()
    user = upsert_user(
        username="alice",
        email="alice@example.com",
        password="password123",
        mapping_username="alice",
        name="Alice",
    )

    transport = AsyncMock()
    transport.post.return_value = type(
        "MockResponse",
        (),
        {
            "status_code": 200,
            "json": lambda self: {"ok": True, "approval_id": "appr_1", "choice": "session"},
            "text": '{"ok": true}',
            "headers": {"content-type": "application/json"},
        },
    )()

    with TestClient(interface_app_mod.app) as client:
        interface_app_mod.app.state.http = transport
        token = interface_app_mod._create_session_token(user.id)
        client.cookies.set(interface_app_mod.SESSION_COOKIE_NAME, token)
        response = client.post(
            "/api/chat/approvals/appr_1",
            json={"choice": "session"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["choice"] == "session"
    transport.post.assert_awaited_once()
    _, kwargs = transport.post.await_args
    assert kwargs["headers"]["Authorization"] == "Bearer sk-user"
    assert kwargs["json"] == {"choice": "session"}


if __name__ == "__main__":
    run()

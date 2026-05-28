from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def _write_configs(tmp_path: Path) -> tuple[Path, Path]:
    mapping_path = tmp_path / "users_mapping.yaml"
    proxy_path = tmp_path / "model_proxy.yaml"
    home_dir = tmp_path / "hmx_alice"
    mapping_path.write_text(
        f"""
start_port: 8643
hermes:
  model:
    default: gpt-5.4
    provider: custom
    base_url: http://127.0.0.1:8765/v1
    api_key: alice-local-token
  model_options:
    primary: primary
    options:
      - id: primary
        name: Main
        provider: custom
        model: gpt-5.4
      - id: fast
        name: Fast
        provider: custom
        model: gpt-5.4-mini
      - id: alt
        name: Alt
        provider: custom
        model: gpt-5.4
users:
  - username: alice
    email: alice@example.com
    display_name: Alice
    linux_user: hmx_alice
    home_dir: {home_dir}
    hermes_home: {home_dir / ".hermes"}
    workdir: {home_dir / "work"}
    api_port: 8655
    api_key: sk-user
    systemd_service: hermes-alice.service
""".lstrip(),
        encoding="utf-8",
    )
    proxy_path.write_text(
        """
listen:
  host: 127.0.0.1
  port: 8765
models:
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
  - id: alt
    name: Alt
    provider: custom
    model: gpt-5.4
    base_url: https://alt.example/v1
    api_key: sk-alt
""".lstrip(),
        encoding="utf-8",
    )
    return mapping_path, proxy_path


def _client(tmp_path: Path, monkeypatch):
    mapping_path, proxy_path = _write_configs(tmp_path)
    monkeypatch.setenv("POTATO_AGENT_MAPPING_PATH", str(mapping_path))
    monkeypatch.setenv("POTATO_MODEL_PROXY_CONFIG_PATH", str(proxy_path))
    import interface.model_proxy as model_proxy

    importlib.reload(model_proxy)
    return TestClient(model_proxy.app), model_proxy


def test_proxy_rejects_missing_or_invalid_token(monkeypatch, tmp_path) -> None:
    client, _ = _client(tmp_path, monkeypatch)

    assert client.get("/v1/models").status_code == 401
    assert client.get(
        "/v1/models", headers={"authorization": "Bearer wrong-token"}
    ).status_code == 401


def test_proxy_lists_authorized_models_without_api_keys(monkeypatch, tmp_path) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    response = client.get(
        "/v1/models", headers={"authorization": "Bearer alice-local-token"}
    )

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["data"]] == [
        "Main",
        "Fast",
        "Alt",
    ]
    assert "sk-primary" not in response.text
    assert "https://primary.example" not in response.text


def test_proxy_rejects_unallowed_or_unknown_models(monkeypatch, tmp_path) -> None:
    client, _ = _client(tmp_path, monkeypatch)

    forbidden = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer alice-local-token"},
        json={"model": "not-whitelisted", "messages": []},
    )
    assert forbidden.status_code == 403

    proxy_path = Path(os.environ["POTATO_MODEL_PROXY_CONFIG_PATH"])
    proxy_path.write_text(
        proxy_path.read_text(encoding="utf-8").replace(
            "    name: Fast", "    name: ProxyOnly"
        ),
        encoding="utf-8",
    )
    unknown = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer alice-local-token"},
        json={"model": "Fast", "messages": []},
    )
    assert unknown.status_code == 404


def test_proxy_sanitizes_null_required_in_responses_tool_schemas(
    monkeypatch, tmp_path
) -> None:
    client, model_proxy = _client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def aiter_bytes(self):
            yield b'{"ok":true}'

        async def aclose(self):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def build_request(self, method, url, *, content, headers, params):
            captured["content"] = content
            return SimpleNamespace()

        async def send(self, request, *, stream):
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(model_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/v1/responses",
        headers={"authorization": "Bearer alice-local-token"},
        json={
            "model": "Main",
            "input": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "name": "session_search",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "filters": {
                                "type": "object",
                                "properties": {"source": {"type": "string"}},
                                "required": None,
                            },
                        },
                        "required": None,
                    },
                },
                {
                    "type": "function",
                    "name": "valid_tool",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    forwarded = __import__("json").loads(captured["content"].decode("utf-8"))
    params = forwarded["tools"][0]["parameters"]
    assert params["required"] == []
    assert params["properties"]["filters"]["required"] == []
    assert forwarded["tools"][1]["parameters"]["required"] == ["path"]


def test_proxy_normalizes_malformed_responses_sse_terminal_output(
    monkeypatch, tmp_path
) -> None:
    client, model_proxy = _client(tmp_path, monkeypatch)

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_bytes(self):
            yield (
                b"data: {\"type\":\"response.created\",\"response\":{\"id\":\"resp_1\",\"object\":\"response\",\"status\":\"in_progress\",\"output\":[]}}\n\n"
            )
            yield (
                b"data: {\"type\":\"response.output_text.delta\",\"item_id\":\"msg_1\",\"output_index\":0,\"content_index\":0,\"delta\":\"hello\"}\n\n"
            )
            yield (
                b"data: {\"type\":\"response.completed\",\"response\":{\"id\":\"resp_1\",\"object\":\"response\",\"status\":\"completed\",\"output\":null}}\n\n"
            )

        async def aclose(self):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def build_request(self, method, url, *, content, headers, params):
            return SimpleNamespace()

        async def send(self, request, *, stream):
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(model_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/v1/responses",
        headers={"authorization": "Bearer alice-local-token"},
        json={"model": "Main", "input": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200, response.text
    chunks = [
        line[6:]
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    completed = __import__("json").loads(chunks[-1])
    assert completed["response"]["output"] == [
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "hello"}],
            "id": "msg_1",
        }
    ]
    assert completed["response"]["output_text"] == "hello"


def test_proxy_forwards_original_model_and_upstream_key(monkeypatch, tmp_path) -> None:
    client, model_proxy = _client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def aiter_bytes(self):
            yield b'{"ok":true}'

        async def aclose(self):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def build_request(self, method, url, *, content, headers, params):
            captured["method"] = method
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            captured["params"] = dict(params)
            return SimpleNamespace()

        async def send(self, request, *, stream):
            captured["stream"] = stream
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(model_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer alice-local-token"},
        json={"model": "Fast", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200, response.text
    assert captured["url"] == "https://fast.example/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-fast"
    assert b'"model":"gpt-5.4-mini"' in captured["content"]


def test_proxy_routes_duplicate_upstream_models_by_name(monkeypatch, tmp_path) -> None:
    client, model_proxy = _client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def aiter_bytes(self):
            yield b'{"ok":true}'

        async def aclose(self):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def build_request(self, method, url, *, content, headers, params):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            return SimpleNamespace()

        async def send(self, request, *, stream):
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(model_proxy.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer alice-local-token"},
        json={"model": "Alt", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200, response.text
    assert captured["url"] == "https://alt.example/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-alt"
    assert b'"model":"gpt-5.4"' in captured["content"]

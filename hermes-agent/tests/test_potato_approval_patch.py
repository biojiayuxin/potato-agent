from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


pytest.importorskip("aiohttp")


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from interface.hermes_api_approval_patch import apply_patch


apply_patch()


def _make_adapter(api_key: str = "") -> APIServerAdapter:
    extra = {}
    if api_key:
        extra["key"] = api_key
    return APIServerAdapter(PlatformConfig(enabled=True, extra=extra))


def _create_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app["api_server_adapter"] = adapter
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    app.router.add_post("/v1/approvals/{approval_id}", adapter._handle_resolve_approval)
    return app


class TestApprovalPatchChatCompletions:
    @pytest.mark.asyncio
    async def test_stream_emits_approval_required_event(self):
        adapter = _make_adapter()
        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            async def _mock_run_agent(**kwargs):
                cb = kwargs.get("stream_delta_callback")
                if cb:
                    stream_q = cb.__closure__[0].cell_contents  # _stream_q from adapter handler
                    stream_q.put(
                        (
                            "__approval_required__",
                            {
                                "approval_id": "appr_test",
                                "session_id": "sess_test",
                                "command": "rm -rf tmp/build",
                                "description": "recursive delete",
                                "pattern_key": "recursive delete",
                                "pattern_keys": ["recursive delete"],
                                "options": ["once", "session", "always", "deny"],
                            },
                        )
                    )
                    cb("Done")
                return (
                    {"final_response": "Done", "messages": [], "api_calls": 1},
                    {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                )

            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                resp = await cli.post(
                    "/v1/chat/completions",
                    json={
                        "model": "test",
                        "messages": [{"role": "user", "content": "delete temp"}],
                        "stream": True,
                    },
                )

                assert resp.status == 200
                body = await resp.text()
                assert "event: hermes.approval.required" in body
                assert '"approval_id": "appr_test"' in body
                assert '"command": "rm -rf tmp/build"' in body
                assert "data: [DONE]" in body


class TestApprovalPatchResolveEndpoint:
    @pytest.mark.asyncio
    async def test_resolve_endpoint_returns_404_for_unknown_approval(self):
        adapter = _make_adapter(api_key="sk-secret")
        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/v1/approvals/appr_missing",
                json={"choice": "once"},
                headers={"Authorization": "Bearer sk-secret"},
            )
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_resolve_endpoint_forwards_choice_to_approval_resolver(self):
        adapter = _make_adapter(api_key="sk-secret")
        adapter._pending_approvals["appr_ok"] = {
            "status": "pending",
            "created_at": time.time(),
            "session_key": "api_server:test-session",
        }
        app = _create_app(adapter)

        async with TestClient(TestServer(app)) as cli:
            with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
                resp = await cli.post(
                    "/v1/approvals/appr_ok",
                    json={"choice": "session"},
                    headers={"Authorization": "Bearer sk-secret"},
                )

            assert resp.status == 200
            payload = await resp.json()
            assert payload["ok"] is True
            assert payload["choice"] == "session"
            mock_resolve.assert_called_once_with(
                "api_server:test-session", "session", resolve_all=False
            )


def test_sitecustomize_loads_patch_when_flag_enabled(monkeypatch):
    called = []

    def _apply() -> None:
        called.append(True)

    monkeypatch.setenv("POTATO_AGENT_ENABLE_APPROVAL_PATCH", "1")
    with patch("interface.hermes_api_approval_patch.apply_patch", _apply):
        import importlib
        import sitecustomize

        importlib.reload(sitecustomize)

    assert len(called) >= 1

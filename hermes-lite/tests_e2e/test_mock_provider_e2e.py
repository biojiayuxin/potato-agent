from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import pytest


LITE_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PROFILE = LITE_ROOT / "runtime-profile.yaml"
DEFAULT_TEST_PYTHONS = (
    Path("/opt/potato-hermes-lite/current/venv/bin/python3"),
    Path("/opt/hermes-agent-venv/bin/python3"),
)


def _test_python() -> Path:
    configured = os.environ.get("HERMES_LITE_E2E_PYTHON", "").strip()
    if configured:
        python = Path(configured)
        if python.is_file():
            return python
        pytest.fail(f"Hermes Lite E2E Python does not exist: {python}")
    for python in DEFAULT_TEST_PYTHONS:
        if python.is_file():
            return python
    pytest.fail(
        "no Hermes Lite E2E Python found; set HERMES_LITE_E2E_PYTHON to a "
        "Python environment containing the pinned Lite dependencies"
    )


def _site_packages(python: Path) -> Path:
    candidates = sorted((python.parent.parent / "lib").glob("python*/site-packages"))
    if not candidates:
        pytest.fail(f"no site-packages found for {python}")
    return candidates[-1]


def _chat_chunk(
    delta: dict[str, Any], *, finish_reason: str | None = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-hermes-lite-e2e",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


class _MockProviderState:
    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self.plans = plans
        self.requests: list[dict[str, Any]] = []
        self.request_paths: list[str] = []
        self.request_headers: list[dict[str, str]] = []
        self.condition = threading.Condition()
        self.first_chunk_sent = threading.Event()
        self.release_stream = threading.Event()

    def record(
        self, body: dict[str, Any], headers: dict[str, str], path: str
    ) -> dict[str, Any]:
        with self.condition:
            index = len(self.requests)
            self.requests.append(body)
            self.request_paths.append(path)
            self.request_headers.append(headers)
            self.condition.notify_all()
        if index >= len(self.plans):
            return {"kind": "text", "text": f"Mock reply {index + 1}"}
        return self.plans[index]

    def wait_for_requests(self, count: int, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.requests) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    pytest.fail(
                        f"mock provider received {len(self.requests)} requests, "
                        f"expected at least {count}"
                    )
                self.condition.wait(remaining)


class _DaemonThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class MockProvider(AbstractContextManager["MockProvider"]):
    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self.state = _MockProviderState(plans)
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path.rstrip("/") != "/v1/models":
                    self.send_error(404)
                    return
                self._send_json(
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": "mock-model",
                                "object": "model",
                                "created": 1,
                                "owned_by": "mock",
                            }
                        ],
                    }
                )

            def do_POST(self) -> None:
                request_path = self.path.rstrip("/")
                if request_path not in {"/v1/chat/completions", "/v1/responses"}:
                    self.send_error(404)
                    return
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                    body = json.loads(self.rfile.read(length))
                except (ValueError, json.JSONDecodeError):
                    self.send_error(400)
                    return
                if not isinstance(body, dict):
                    self.send_error(400)
                    return
                plan = state.record(
                    body,
                    {key.lower(): value for key, value in self.headers.items()},
                    request_path,
                )
                if request_path == "/v1/responses":
                    if not body.get("stream"):
                        self.send_error(400, "mock Responses endpoint requires stream=true")
                        return
                    self._send_responses_stream(plan)
                elif body.get("stream"):
                    self._send_stream(plan)
                else:
                    self._send_json(
                        {
                            "id": "chatcmpl-hermes-lite-e2e",
                            "object": "chat.completion",
                            "created": 1,
                            "model": "mock-model",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {
                                        "role": "assistant",
                                        "content": str(plan.get("text") or "Mock reply"),
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                    )

            def _send_json(self, value: dict[str, Any]) -> None:
                encoded = json.dumps(value).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                self.wfile.flush()

            def _write_sse(self, value: dict[str, Any] | str) -> bool:
                if isinstance(value, str):
                    encoded = f"data: {value}\n\n".encode("utf-8")
                else:
                    encoded = f"data: {json.dumps(value)}\n\n".encode("utf-8")
                try:
                    self.wfile.write(encoded)
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            def _send_stream(self, plan: dict[str, Any]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                kind = plan.get("kind", "text")
                if kind == "tool":
                    command = str(plan["command"])
                    chunks = [
                        _chat_chunk(
                            {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-hermes-lite-e2e",
                                        "type": "function",
                                        "function": {
                                            "name": "terminal",
                                            "arguments": "",
                                        },
                                    }
                                ],
                            }
                        ),
                        _chat_chunk(
                            {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": json.dumps(
                                                {"command": command}
                                            )
                                        },
                                    }
                                ]
                            }
                        ),
                        _chat_chunk({}, finish_reason="tool_calls"),
                    ]
                else:
                    text = str(plan.get("text") or "Mock reply")
                    midpoint = max(1, len(text) // 2)
                    chunks = [
                        _chat_chunk(
                            {"role": "assistant", "content": text[:midpoint]}
                        )
                    ]
                    if kind == "blocked":
                        if not self._write_sse(chunks[0]):
                            return
                        state.first_chunk_sent.set()
                        state.release_stream.wait(timeout=15.0)
                        chunks = [_chat_chunk({"content": text[midpoint:]})]
                    elif text[midpoint:]:
                        chunks.append(_chat_chunk({"content": text[midpoint:]}))
                    chunks.append(_chat_chunk({}, finish_reason="stop"))

                for chunk in chunks:
                    if not self._write_sse(chunk):
                        return
                self._write_sse(
                    {
                        "id": "chatcmpl-hermes-lite-e2e",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "mock-model",
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                        },
                    }
                )
                self._write_sse("[DONE]")

            def _send_responses_stream(self, plan: dict[str, Any]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                text = str(plan.get("text") or "Mock Responses reply")
                response_id = "resp-hermes-lite-e2e"
                message_id = "msg-hermes-lite-e2e"
                midpoint = max(1, len(text) // 2)
                message = {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": text,
                            "annotations": [],
                            "logprobs": [],
                        }
                    ],
                }
                events = [
                    {
                        "type": "response.output_text.delta",
                        "sequence_number": 1,
                        "item_id": message_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": text[:midpoint],
                        "logprobs": [],
                    },
                    {
                        "type": "response.output_text.delta",
                        "sequence_number": 2,
                        "item_id": message_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": text[midpoint:],
                        "logprobs": [],
                    },
                    {
                        "type": "response.output_item.done",
                        "sequence_number": 3,
                        "output_index": 0,
                        "item": message,
                    },
                    {
                        "type": "response.completed",
                        "sequence_number": 4,
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "created_at": 1,
                            "completed_at": 1,
                            "status": "completed",
                            "error": None,
                            "incomplete_details": None,
                            "instructions": None,
                            "metadata": {},
                            "model": "mock-model",
                            # The runtime reconstructs content from output_item.done;
                            # Codex has returned null here in production.
                            "output": None,
                            "parallel_tool_calls": True,
                            "temperature": None,
                            "tool_choice": "auto",
                            "tools": [],
                            "top_p": None,
                            "usage": {
                                "input_tokens": 10,
                                "input_tokens_details": {"cached_tokens": 0},
                                "output_tokens": 2,
                                "output_tokens_details": {"reasoning_tokens": 0},
                                "total_tokens": 12,
                            },
                        },
                    },
                ]
                for event in events:
                    if not self._write_sse(event):
                        return
                self._write_sse("[DONE]")

        self.server = _DaemonThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "MockProvider":
        self.thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.state.release_stream.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5.0)


class GatewayProcess(AbstractContextManager["GatewayProcess"]):
    def __init__(
        self,
        root: Path,
        provider: MockProvider,
        *,
        api_mode: str = "chat_completions",
    ) -> None:
        self.root = root
        self.home = root / "home"
        self.hermes_home = self.home / ".hermes"
        self.work = root / "work"
        self.tmp = root / "tmp"
        self.state_home = root / "state"
        for path in (
            self.home,
            self.hermes_home,
            self.work,
            self.tmp,
            self.state_home,
        ):
            path.mkdir(parents=True, exist_ok=True)
        (self.hermes_home / "config.yaml").write_text(
            "\n".join(
                [
                    "model:",
                    "  default: mock-model",
                    "  provider: custom",
                    f"  base_url: {provider.base_url}",
                    "  api_key: mock-key",
                    f"  api_mode: {api_mode}",
                    "agent:",
                    "  max_turns: 4",
                    "  api_max_retries: 1",
                    "  task_completion_guidance: false",
                    "  environment_probe: false",
                    "approvals:",
                    "  mode: manual",
                    "terminal:",
                    f"  cwd: {self.work}",
                    "  timeout: 5",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        python = _test_python()
        site_packages = _site_packages(python)
        env = {
            "AGENT_BROWSER_ENGINE": "chrome",
            "BROWSER_CDP_URL": "",
            "CAMOFOX_URL": "",
            "HOME": str(self.home),
            "HERMES_BUNDLED_SKILLS": str(LITE_ROOT / "skills"),
            "HERMES_DISABLE_CRON": "1",
            "HERMES_DISABLE_GATEWAY_PLATFORMS": "1",
            "HERMES_DISABLE_KANBAN": "1",
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "HERMES_DISABLE_MCP": "1",
            "HERMES_HOME": str(self.hermes_home),
            "HERMES_OPTIONAL_SKILLS": str(LITE_ROOT / "optional-skills"),
            "HERMES_PYTHON_SRC_ROOT": str(LITE_ROOT),
            "HERMES_RUNTIME_PROFILE_PATH": str(RUNTIME_PROFILE),
            "HERMES_SKIP_NODE_BOOTSTRAP": "1",
            "HERMES_TUI_GATEWAY_SHUTDOWN_GRACE_S": "0.2",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": "127.0.0.1,localhost",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join((str(LITE_ROOT), str(site_packages))),
            "PYTHONUNBUFFERED": "1",
            "TERMINAL_CWD": str(self.work),
            "TERMINAL_ENV": "local",
            "TMPDIR": str(self.tmp),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(self.state_home),
        }
        self.process = subprocess.Popen(
            [str(python), "-S", "-B", "-m", "tui_gateway.entry"],
            cwd=self.work,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.deferred: list[dict[str, Any]] = []
        self.all_messages: list[dict[str, Any]] = []
        self.stderr: list[str] = []
        self.request_id = 0
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                self.stderr.append(f"non-JSON stdout: {line.rstrip()}")
                continue
            if isinstance(value, dict):
                self.all_messages.append(value)
                self.messages.put(value)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line.rstrip())

    def diagnostics(self) -> str:
        protocol = json.dumps(self.all_messages[-30:], ensure_ascii=False, indent=2)
        errors = "\n".join(self.stderr[-80:])
        return f"recent protocol messages:\n{protocol}\nrecent stderr:\n{errors}"

    def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 30.0
    ) -> dict[str, Any]:
        for index, message in enumerate(self.deferred):
            if predicate(message):
                return self.deferred.pop(index)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(
                    "timed out waiting for gateway message\n" + self.diagnostics()
                )
            try:
                message = self.messages.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if self.process.poll() is not None:
                    pytest.fail(
                        f"gateway exited with {self.process.returncode}\n"
                        + self.diagnostics()
                    )
                continue
            if predicate(message):
                return message
            self.deferred.append(message)

    def wait_event(
        self, event_type: str, *, session_id: str | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        def matches(message: dict[str, Any]) -> bool:
            params = message.get("params")
            return (
                message.get("method") == "event"
                and isinstance(params, dict)
                and params.get("type") == event_type
                and (session_id is None or params.get("session_id") == session_id)
            )

        return self.wait_for(matches, timeout=timeout)

    def send(self, method: str, params: dict[str, Any]) -> int:
        self.request_id += 1
        request_id = self.request_id
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            + "\n"
        )
        self.process.stdin.flush()
        return request_id

    def response(self, request_id: int, timeout: float = 30.0) -> dict[str, Any]:
        return self.wait_for(
            lambda message: message.get("id") == request_id, timeout=timeout
        )

    def rpc(
        self, method: str, params: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        response = self.response(self.send(method, params), timeout=timeout)
        assert "error" not in response, response
        return response["result"]

    def __enter__(self) -> "GatewayProcess":
        ready = self.wait_event("gateway.ready", timeout=15.0)
        assert ready["params"]["payload"]["skin"]
        return self

    def __exit__(self, *_args: Any) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2.0)
        self._reader.join(timeout=1.0)
        self._stderr_reader.join(timeout=1.0)


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event["params"].get("payload")
    assert isinstance(payload, dict)
    return payload


def _create_session(gateway: GatewayProcess) -> dict[str, Any]:
    result = gateway.rpc(
        "session.create", {"cols": 100, "cwd": str(gateway.work)}
    )
    assert result["session_id"]
    assert result["stored_session_id"]
    return result


def test_prompt_submit_and_resume_use_only_lite_runtime(tmp_path: Path) -> None:
    with MockProvider(
        [
            {"kind": "text", "text": "Mock reply one"},
            {"kind": "text", "text": "Mock reply two"},
        ]
    ) as provider:
        with GatewayProcess(tmp_path / "gateway", provider) as gateway:
            created = _create_session(gateway)
            sid = created["session_id"]

            submit = gateway.rpc(
                "prompt.submit", {"session_id": sid, "text": "first prompt"}
            )
            assert submit == {"status": "streaming"}
            complete = _payload(
                gateway.wait_event("message.complete", session_id=sid)
            )
            assert complete["status"] == "complete"
            assert complete["text"] == "Mock reply one"

            provider.state.wait_for_requests(1)
            first_request = provider.state.requests[0]
            assert first_request["model"] == "mock-model"
            assert first_request["stream"] is True
            assert any(
                message.get("role") == "user"
                and message.get("content") == "first prompt"
                for message in first_request["messages"]
            )
            expected_tools = (LITE_ROOT / "manifests" / "model-tools.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            actual_tools = [
                item["function"]["name"] for item in first_request.get("tools", [])
            ]
            assert set(actual_tools) <= set(expected_tools)
            assert {"terminal", "read_file", "write_file", "patch"} <= set(
                actual_tools
            )
            assert (
                provider.state.request_headers[0].get("authorization")
                == "Bearer mock-key"
            )

            resumed = gateway.rpc(
                "session.resume",
                {"session_id": created["stored_session_id"], "cols": 100},
            )
            resumed_sid = resumed["session_id"]
            serialized_messages = json.dumps(resumed["messages"], ensure_ascii=False)
            assert "first prompt" in serialized_messages
            assert "Mock reply one" in serialized_messages

            second_submit = gateway.rpc(
                "prompt.submit",
                {"session_id": resumed_sid, "text": "second prompt"},
            )
            assert second_submit == {"status": "streaming"}
            second_complete = _payload(
                gateway.wait_event("message.complete", session_id=resumed_sid)
            )
            assert second_complete["status"] == "complete"
            assert second_complete["text"] == "Mock reply two"
            provider.state.wait_for_requests(2)

            state_db = gateway.hermes_home / "state.db"
            assert state_db.is_file()
            assert state_db.is_relative_to(tmp_path)


def test_codex_responses_prompt_submit_streams_to_completion(
    tmp_path: Path,
) -> None:
    with MockProvider(
        [{"kind": "text", "text": "Mock Codex Responses reply"}]
    ) as provider:
        with GatewayProcess(
            tmp_path / "gateway", provider, api_mode="codex_responses"
        ) as gateway:
            sid = _create_session(gateway)["session_id"]
            submit = gateway.rpc(
                "prompt.submit",
                {"session_id": sid, "text": "codex responses prompt"},
            )
            assert submit == {"status": "streaming"}

            complete = _payload(
                gateway.wait_event("message.complete", session_id=sid)
            )
            assert complete["status"] == "complete"
            assert complete["text"] == "Mock Codex Responses reply"

            provider.state.wait_for_requests(1)
            request = provider.state.requests[0]
            assert provider.state.request_paths[0] == "/v1/responses"
            assert request["model"] == "mock-model"
            assert request["stream"] is True
            assert request["store"] is False
            assert request["tool_choice"] == "auto"
            assert request["parallel_tool_calls"] is True
            assert request["reasoning"] == {
                "effort": "medium",
                "summary": "auto",
            }
            assert request["include"] == ["reasoning.encrypted_content"]
            assert "messages" not in request
            assert "codex responses prompt" in json.dumps(
                request["input"], ensure_ascii=False
            )

            expected_tools = (LITE_ROOT / "manifests" / "model-tools.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            actual_tools = [item["name"] for item in request.get("tools", [])]
            assert set(actual_tools) <= set(expected_tools)
            assert {"terminal", "read_file", "write_file", "patch"} <= set(
                actual_tools
            )
            assert (
                provider.state.request_headers[0].get("authorization")
                == "Bearer mock-key"
            )


def test_session_interrupt_stops_a_streaming_turn(tmp_path: Path) -> None:
    with MockProvider(
        [{"kind": "blocked", "text": "Partial response must stop"}]
    ) as provider:
        with GatewayProcess(tmp_path / "gateway", provider) as gateway:
            sid = _create_session(gateway)["session_id"]
            assert gateway.rpc(
                "prompt.submit", {"session_id": sid, "text": "interrupt me"}
            ) == {"status": "streaming"}

            gateway.wait_event("message.delta", session_id=sid)
            assert provider.state.first_chunk_sent.wait(timeout=5.0)
            interrupt_id = gateway.send("session.interrupt", {"session_id": sid})
            interrupt = gateway.response(interrupt_id, timeout=10.0)
            assert interrupt["result"] == {"status": "interrupted"}
            provider.state.release_stream.set()

            complete = _payload(
                gateway.wait_event("message.complete", session_id=sid, timeout=20.0)
            )
            assert complete["status"] == "interrupted"


def test_approval_deny_prevents_dangerous_terminal_command(tmp_path: Path) -> None:
    protected = tmp_path / "must-survive"
    protected.mkdir()
    marker = protected / "marker.txt"
    marker.write_text("preserve me", encoding="utf-8")
    command = f"rm -rf {protected}"

    with MockProvider(
        [
            {"kind": "tool", "command": command},
            {"kind": "text", "text": "The command was denied."},
        ]
    ) as provider:
        with GatewayProcess(tmp_path / "gateway", provider) as gateway:
            sid = _create_session(gateway)["session_id"]
            assert gateway.rpc(
                "prompt.submit",
                {"session_id": sid, "text": "try the protected command"},
            ) == {"status": "streaming"}

            approval = _payload(
                gateway.wait_event("approval.request", session_id=sid, timeout=20.0)
            )
            assert approval["command"] == command
            assert approval["approval_id"]
            assert marker.read_text(encoding="utf-8") == "preserve me"
            stale = gateway.rpc(
                "approval.respond",
                {
                    "session_id": sid,
                    "choice": "deny",
                    "approval_id": "not-the-pending-approval",
                },
                timeout=10.0,
            )
            assert stale["resolved"] == 0
            assert marker.read_text(encoding="utf-8") == "preserve me"
            result = gateway.rpc(
                "approval.respond",
                {
                    "session_id": sid,
                    "choice": "deny",
                    "approval_id": approval["approval_id"],
                },
                timeout=10.0,
            )
            assert result["resolved"] == 1

            complete = _payload(
                gateway.wait_event("message.complete", session_id=sid, timeout=20.0)
            )
            assert complete["status"] == "complete"
            assert complete["text"] == "The command was denied."
            assert marker.read_text(encoding="utf-8") == "preserve me"
            provider.state.wait_for_requests(2)
            follow_up = json.dumps(
                provider.state.requests[1]["messages"], ensure_ascii=False
            )
            assert "BLOCKED" in follow_up

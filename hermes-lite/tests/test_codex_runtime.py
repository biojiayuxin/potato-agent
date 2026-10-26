from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import codex_runtime
from agent.codex_responses_adapter import _normalize_codex_response


class _EventStream(list):
    def __init__(self, events):
        super().__init__(events)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Responses:
    def __init__(self, stream: _EventStream) -> None:
        self.stream = stream
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream


class _Agent:
    def __init__(self) -> None:
        self._interrupt_requested = False
        self.text_deltas: list[str] = []
        self.reasoning_deltas: list[str] = []
        self.activities: list[str] = []

    def _fire_stream_delta(self, text: str) -> None:
        self.text_deltas.append(text)

    def _fire_reasoning_delta(self, text: str) -> None:
        self.reasoning_deltas.append(text)

    def _touch_activity(self, description: str) -> None:
        self.activities.append(description)

    def _client_log_context(self) -> str:
        return "test-client"


def test_codex_runtime_is_owned_by_lite_source(runtime_paths) -> None:
    origin = Path(codex_runtime.__file__).resolve()
    assert origin.is_relative_to(runtime_paths.source)


def test_run_codex_stream_consumes_raw_responses_events(runtime_paths) -> None:
    message = SimpleNamespace(
        type="message",
        role="assistant",
        status="completed",
        content=[SimpleNamespace(type="output_text", text="Lite response")],
    )
    stream = _EventStream(
        [
            {"type": "response.output_text.delta", "delta": "Lite "},
            {"type": "response.reasoning_summary_text.delta", "delta": "checking"},
            {"type": "response.output_text.delta", "delta": "response"},
            {"type": "response.output_item.done", "item": message},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp-lite-test",
                    "status": "completed",
                    "output": None,
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            },
        ]
    )
    responses = _Responses(stream)
    client = SimpleNamespace(responses=responses)
    agent = _Agent()
    first_delta: list[bool] = []

    result = codex_runtime.run_codex_stream(
        agent,
        {
            "model": "mock-model",
            "instructions": "test",
            "input": [{"role": "user", "content": "hello"}],
            "store": False,
        },
        client=client,
        on_first_delta=lambda: first_delta.append(True),
    )

    assert result.id == "resp-lite-test"
    assert result.status == "completed"
    assert result.output == [message]
    assert result.output_text == "Lite response"
    assert responses.calls[0]["stream"] is True
    assert agent.text_deltas == ["Lite ", "response"]
    assert agent.reasoning_deltas == ["checking"]
    assert len(agent.activities) == 5
    assert first_delta == [True]
    assert stream.closed is True


def test_codex_app_server_is_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="not supported"):
        codex_runtime.run_codex_app_server_turn()


@pytest.mark.parametrize("status,expected", [
    ("completed", "stop"),
    ("incomplete", "incomplete"),
    ("in_progress", "incomplete"),
    (None, "incomplete"),
])
@pytest.mark.parametrize("summary,encrypted", [
    ("No further answer is needed.", None),
    ("", "opaque-reasoning-fixture"),
    ("", None),
])
def test_reasoning_only_response_respects_completion_status(status, expected, summary, encrypted):
    response = SimpleNamespace(status=status, output=[SimpleNamespace(
        type="reasoning", id="rs-fixture",
        summary=[SimpleNamespace(type="summary_text", text=summary)] if summary else [],
        encrypted_content=encrypted,
    )])

    message, finish_reason = _normalize_codex_response(response)

    assert finish_reason == expected
    assert message.content == ""
    assert not message.tool_calls
    assert message.reasoning == (summary or None)
    if encrypted:
        assert message.codex_reasoning_items[0]["encrypted_content"] == encrypted


def test_completed_response_with_tool_call_still_executes_tools():
    response = SimpleNamespace(status="completed", output=[SimpleNamespace(
        type="function_call", status="completed", id="fc-fixture", call_id="call-fixture",
        name="read_file", arguments='{"path":"example.txt"}',
    )])
    message, finish_reason = _normalize_codex_response(response)
    assert finish_reason == "tool_calls"
    assert message.tool_calls[0].function.name == "read_file"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_reasoning_only_response_does_not_hide_provider_errors(status):
    response = SimpleNamespace(status=status, error=SimpleNamespace(message="provider failure"),
                               output=[SimpleNamespace(type="reasoning", summary=[])])
    with pytest.raises(RuntimeError, match="provider failure"):
        _normalize_codex_response(response)


def test_reasoning_stream_without_completion_event_remains_incomplete():
    response = codex_runtime._consume_codex_event_stream([
        {"type": "response.output_item.done", "item": SimpleNamespace(type="reasoning", summary=[])},
    ], model="mock-model")
    _, finish_reason = _normalize_codex_response(response)
    assert response.status is None
    assert finish_reason == "incomplete"


@pytest.mark.parametrize("status", ["completed", "incomplete", "failed"])
def test_terminal_event_supplies_status_when_response_omits_it(status):
    response = codex_runtime._consume_codex_event_stream([
        {"type": f"response.{status}", "response": {}},
    ], model="mock-model")
    assert response.status == status

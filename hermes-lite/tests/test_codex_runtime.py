from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import codex_runtime


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

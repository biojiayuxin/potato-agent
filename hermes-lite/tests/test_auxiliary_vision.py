from __future__ import annotations

from agent.auxiliary_client import _build_call_kwargs


def test_codex_vision_kwargs_do_not_require_anthropic_adapter() -> None:
    kwargs = _build_call_kwargs(
        "custom",
        "gpt-5.6-terra",
        [{"role": "user", "content": []}],
        temperature=0.1,
        max_tokens=2_000,
        timeout=120.0,
        base_url="http://127.0.0.1:8765/v1",
    )

    assert kwargs["temperature"] == 0.1
    assert "max_tokens" not in kwargs


def test_anthropic_47_auxiliary_kwargs_still_omit_temperature() -> None:
    kwargs = _build_call_kwargs(
        "custom",
        "anthropic/claude-opus-4.7",
        [{"role": "user", "content": "describe the image"}],
        temperature=0.1,
    )

    assert "temperature" not in kwargs

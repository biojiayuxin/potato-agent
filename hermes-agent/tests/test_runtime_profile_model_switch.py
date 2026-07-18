"""Regression tests for live model-runtime profile enforcement."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = REPO_ROOT.parent / "packaging" / "hermes" / "runtime-profile.yaml"


def _run(code: str, *, home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("HERMES_"):
            env.pop(key, None)
    home.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(home / ".hermes"),
            "HERMES_RUNTIME_PROFILE_PATH": str(PROFILE_PATH),
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_profile_rejects_live_switch_before_mutating_agent(tmp_path):
    code = r'''
from types import SimpleNamespace

from agent.agent_runtime_helpers import switch_model
from runtime_profile import RuntimeProfileError

agent = SimpleNamespace(model="current", provider="custom")
try:
    switch_model(
        agent,
        new_model="forbidden-model",
        new_provider="openrouter",
        api_key="unused",
        base_url="https://openrouter.ai/api/v1",
        api_mode="chat_completions",
    )
except RuntimeProfileError as exc:
    assert "Model switch target provider 'openrouter'" in str(exc), exc
else:
    raise AssertionError("non-custom live model switch bypassed the profile")

assert agent.model == "current"
assert agent.provider == "custom"
assert not hasattr(agent, "api_mode")
'''
    result = _run(code, home=tmp_path / "switch")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_rejects_tampered_recovery_and_restore_snapshots(tmp_path):
    code = r'''
from types import SimpleNamespace

from agent.agent_runtime_helpers import (
    restore_primary_runtime,
    try_recover_primary_transport,
)
from agent.chat_completion_helpers import try_activate_fallback

def forbidden_close(*args, **kwargs):
    raise AssertionError("working client was closed before profile validation")

restore_agent = SimpleNamespace(
    _fallback_activated=True,
    _fallback_index=4,
    _rate_limited_until=0,
    _primary_runtime={
        "model": "forbidden",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_mode": "anthropic_messages",
        "compressor_provider": "anthropic",
        "compressor_api_mode": "anthropic_messages",
    },
    model="current",
    provider="custom",
    api_mode="chat_completions",
)
assert restore_primary_runtime(restore_agent) is False
assert (restore_agent.model, restore_agent.provider, restore_agent.api_mode) == (
    "current", "custom", "chat_completions"
)

class ReadTimeout(Exception):
    pass

recovery_agent = SimpleNamespace(
    _fallback_activated=False,
    _is_openrouter_url=lambda: False,
    provider="custom",
    client=object(),
    _close_openai_client=forbidden_close,
    _primary_runtime={
        "model": "forbidden",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_mode": "chat_completions",
    },
)
assert try_recover_primary_transport(
    recovery_agent,
    ReadTimeout("transient"),
    retry_count=1,
    max_retries=1,
) is False
assert recovery_agent.provider == "custom"

fallback_agent = SimpleNamespace(
    _fallback_activated=False,
    _fallback_index=0,
    _fallback_chain=[{"provider": "openrouter", "model": "forbidden"}],
    _primary_runtime={"provider": "custom"},
    provider="custom",
    model="current",
    base_url="http://127.0.0.1:9/v1",
    _try_activate_fallback=lambda: False,
)
assert try_activate_fallback(fallback_agent) is False
assert (fallback_agent.provider, fallback_agent.model) == ("custom", "current")
'''
    result = _run(code, home=tmp_path / "recovery")
    assert result.returncode == 0, result.stderr or result.stdout

from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import write_profile


def _run_script(runtime_paths, script: str, *, profile=None) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(runtime_paths.home),
            "HERMES_HOME": str(runtime_paths.hermes_home),
            "HERMES_RUNTIME_PROFILE_PATH": str(profile or runtime_paths.profile),
            "PYTHONPATH": str(runtime_paths.source),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runtime_paths.home,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def test_transport_registry_is_lazy_and_rejects_unknown_modes(runtime_paths) -> None:
    result = _run_script(
        runtime_paths,
        """
import json
import sys
import agent.transports as transports

chat_module = "agent.transports.chat_completions"
codex_module = "agent.transports.codex"
before = [name for name in (chat_module, codex_module) if name in sys.modules]
chat = transports.get_transport("chat_completions")
after_chat = [name for name in (chat_module, codex_module) if name in sys.modules]
codex = transports.get_transport("codex_responses")
after_codex = [name for name in (chat_module, codex_module) if name in sys.modules]
unknown = transports.get_transport("anthropic_messages")

class ForbiddenTransport:
    pass

transports.register_transport("bedrock_converse", ForbiddenTransport)
print(json.dumps({
    "before": before,
    "after_chat": after_chat,
    "after_codex": after_codex,
    "chat_mode": chat.api_mode,
    "codex_mode": codex.api_mode,
    "unknown": unknown is None,
    "unknown_imported": "agent.transports.anthropic" in sys.modules,
    "forbidden_registered": "bedrock_converse" in transports._REGISTRY,
}))
""",
    )

    assert result == {
        "before": [],
        "after_chat": ["agent.transports.chat_completions"],
        "after_codex": [
            "agent.transports.chat_completions",
            "agent.transports.codex",
        ],
        "chat_mode": "chat_completions",
        "codex_mode": "codex_responses",
        "unknown": True,
        "unknown_imported": False,
        "forbidden_registered": False,
    }


def test_transport_registry_obeys_narrow_profile(runtime_paths) -> None:
    profile = write_profile(
        runtime_paths.home / "chat-only-profile.yaml",
        api_modes=["chat_completions"],
    )
    result = _run_script(
        runtime_paths,
        """
import json
import sys
import agent.transports as transports

codex = transports.get_transport("codex_responses")
print(json.dumps({
    "denied": codex is None,
    "imported": "agent.transports.codex" in sys.modules,
    "registered": "codex_responses" in transports._REGISTRY,
}))
""",
        profile=profile,
    )

    assert result == {"denied": True, "imported": False, "registered": False}


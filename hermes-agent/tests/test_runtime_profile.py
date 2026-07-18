"""Regression tests for the deployment-enforced Hermes runtime profile."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = [
    "terminal",
    "process",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "vision_analyze",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_back",
    "browser_press",
    "browser_get_images",
    "browser_vision",
    "browser_console",
    "browser_cdp",
    "browser_dialog",
    "skills_list",
    "skill_view",
    "skill_manage",
    "execute_code",
    "todo",
    "memory",
    "session_search",
    "delegate_task",
]


def _profile_data() -> dict:
    return {
        "schema_version": 1,
        "name": "potato-test",
        "revision": 1,
        "toolsets": {
            "enabled": [
                "terminal",
                "file",
                "vision",
                "browser",
                "skills",
                "code_execution",
                "todo",
                "memory",
                "session_search",
                "delegation",
            ],
            "disabled": ["web", "clarify", "kanban", "tts"],
        },
        "expected_tools": EXPECTED_TOOLS,
        "plugins": {
            "allow_user": False,
            "allow_project": False,
            "allow_entrypoint": False,
            "allowed_general_keys": [],
            "forbidden_kinds": ["platform"],
        },
        "providers": {
            "model": ["custom"],
            "api_modes": ["codex_responses", "chat_completions"],
            "browser": "local",
            "memory": "builtin",
            "context_engine": "compressor",
            "web": [],
        },
        "mcp": {"enabled": False},
        "runtime": {
            "allow_lazy_installs": False,
            "lsp_enabled": True,
            "lsp_install_strategy": "manual",
            "terminal_backend": "local",
            "skills_dependency_strategy": "user_managed",
        },
    }


def _run(
    code: str, *, profile_path: Path | None, home: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("HERMES_"):
            env.pop(key, None)
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["HERMES_HOME"] = str(home / ".hermes")
    if profile_path is not None:
        env["HERMES_RUNTIME_PROFILE_PATH"] = str(profile_path)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def _write_profile(tmp_path: Path) -> Path:
    path = tmp_path / "runtime-profile.yaml"
    path.write_text(yaml.safe_dump(_profile_data(), sort_keys=False), encoding="utf-8")
    return path


def test_profile_preassembly_is_exact_and_forbidden_modules_stay_unloaded(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = f"""
import json
import sys
import model_tools

for entry in model_tools.registry._tools.values():
    entry.check_fn = lambda: True

defs = model_tools.get_tool_definitions(quiet_mode=True, skip_tool_search_assembly=True)
names = [item["function"]["name"] for item in defs]
expected = {EXPECTED_TOOLS!r}
assert len(names) == len(expected), (names, expected)
assert set(names) == set(expected), (names, expected)
assert "web_search" not in names
assert "clarify" not in names
assert not any(name.startswith("kanban_") for name in names)

for prefix in (
    "tools.web_tools", "tools.mcp_tool", "tools.tts_tool",
    "plugins.browser", "plugins.web", "plugins.platforms",
    "plugins.memory", "plugins.context_engine",
):
    assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules), prefix

assert "disabled by runtime profile" in model_tools.handle_function_call("video_analyze", {{}})
assert "disabled by runtime profile" in model_tools.registry.dispatch("video_analyze", {{}})
print(json.dumps(names))
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_is_process_fixed_and_disables_lazy_installs(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
import os
from runtime_profile import get_runtime_profile
p1 = get_runtime_profile()
os.environ["HERMES_RUNTIME_PROFILE_PATH"] = "/definitely/missing.yaml"
p2 = get_runtime_profile()
assert p1 is p2
assert os.environ["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_parser_rejects_policy_drift(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = f"""
from copy import deepcopy
from pathlib import Path
from runtime_profile import RuntimeProfile, RuntimeProfileError

base = {_profile_data()!r}
cases = []
item = deepcopy(base); item["unexpected"] = True; cases.append(item)
item = deepcopy(base); item["toolsets"]["disabled"].append("terminal"); cases.append(item)
item = deepcopy(base); item["providers"]["web"] = ["ddgs"]; cases.append(item)
item = deepcopy(base); item["runtime"]["lsp_install_strategy"] = "auto"; cases.append(item)
item = deepcopy(base); item["plugins"]["allow_user"] = True; cases.append(item)
item = deepcopy(base); item["expected_tools"].append("terminal"); cases.append(item)
item = deepcopy(base); item["toolsets"]["enabled"].append("web"); cases.append(item)
item = deepcopy(base); item["expected_tools"][-1] = "web_search"; cases.append(item)

for index, data in enumerate(cases):
    try:
        RuntimeProfile.from_mapping(Path("case.yaml"), data)
    except RuntimeProfileError:
        continue
    raise AssertionError(f"policy drift case {{index}} was accepted")
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_rejects_reserved_request_override_keys(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
from types import SimpleNamespace
from unittest.mock import patch

from agent.agent_init import _merge_custom_provider_extra_body
from agent.transports import get_transport
from providers import get_provider_profile
from runtime_profile import (
    PROFILE_RESERVED_REQUEST_KEYS,
    RuntimeProfileError,
    validate_profile_request_boundary,
)

import agent.transports.chat_completions  # noqa: F401
import agent.transports.codex  # noqa: F401

messages = [{"role": "user", "content": "hello"}]
tools = [{
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Run a command",
        "parameters": {"type": "object", "properties": {}},
    },
}]
transports = (
    (
        get_transport("chat_completions"),
        {"provider_profile": get_provider_profile("custom")},
    ),
    (get_transport("codex_responses"), {}),
)

for transport, params in transports:
    for key in PROFILE_RESERVED_REQUEST_KEYS:
        for overrides, expected_path in (
            ({key: "attacker-value"}, f"request_overrides.{key}"),
            (
                {"extra_body": {key: "attacker-value"}},
                f"request_overrides.extra_body.{key}",
            ),
        ):
            try:
                transport.build_kwargs(
                    model="test-model",
                    messages=messages,
                    tools=tools,
                    request_overrides=overrides,
                    **params,
                )
            except RuntimeProfileError as exc:
                assert expected_path in str(exc), (transport.api_mode, key, exc)
            else:
                raise AssertionError(
                    f"{transport.api_mode} accepted reserved override {expected_path}"
                )
    for capability_key in ("plugins", "web_search_options"):
        for overrides in (
            {capability_key: {"enabled": True}},
            {"extra_body": {capability_key: {"enabled": True}}},
        ):
            try:
                transport.build_kwargs(
                    model="test-model",
                    messages=messages,
                    tools=tools,
                    request_overrides=overrides,
                    **params,
                )
            except RuntimeProfileError as exc:
                assert capability_key in str(exc), exc
            else:
                raise AssertionError(
                    f"{transport.api_mode} accepted capability field {capability_key}"
                )

for invalid_mode in ("anthropic_messages", "bedrock_converse", "unknown"):
    try:
        validate_profile_request_boundary(
            {"model": "test-model", "messages": messages},
            api_mode=invalid_mode,
        )
    except RuntimeProfileError as exc:
        assert invalid_mode in str(exc), exc
    else:
        raise AssertionError(f"final boundary accepted API mode {invalid_mode}")

for capability_key in ("plugins", "web_search_options", "unknown_capability"):
    try:
        validate_profile_request_boundary(
            {
                "model": "test-model",
                "messages": messages,
                capability_key: {"enabled": True},
            },
            api_mode="chat_completions",
        )
    except RuntimeProfileError as exc:
        assert capability_key in str(exc), exc
    else:
        raise AssertionError(
            f"final boundary accepted top-level field {capability_key}"
        )

malformed_tool_requests = (
    (
        "chat_completions",
        {
            "model": "test-model",
            "messages": messages,
            "tools": [{
                "type": "web_search",
                "function": tools[0]["function"],
            }],
        },
    ),
    (
        "codex_responses",
        {
            "model": "test-model",
            "input": messages,
            "tools": [{"type": "web_search", "name": "terminal"}],
        },
    ),
)
for api_mode, request in malformed_tool_requests:
    try:
        validate_profile_request_boundary(request, api_mode=api_mode)
    except RuntimeProfileError as exc:
        assert "malformed tool definition" in str(exc), exc
    else:
        raise AssertionError(f"{api_mode} accepted a hosted tool shape")

for api_mode, tool_choice in (
    (
        "chat_completions",
        {"type": "web_search_preview"},
    ),
    (
        "codex_responses",
        {"type": "web_search_preview"},
    ),
):
    try:
        validate_profile_request_boundary(
            {
                "model": "test-model",
                "messages" if api_mode == "chat_completions" else "input": messages,
                "tool_choice": tool_choice,
            },
            api_mode=api_mode,
        )
    except RuntimeProfileError as exc:
        assert "malformed tool_choice" in str(exc), exc
    else:
        raise AssertionError(f"{api_mode} accepted a hosted tool_choice")

for tools_value in (None, []):
    request = {
        "model": "test-model",
        "messages": messages,
        "tool_choice": {
            "type": "function",
            "function": {"name": "terminal"},
        },
    }
    if tools_value is not None:
        request["tools"] = tools_value
    try:
        validate_profile_request_boundary(request, api_mode="chat_completions")
    except RuntimeProfileError as exc:
        assert "not present" in str(exc), exc
    else:
        raise AssertionError("tool_choice selected a tool absent from the request")

fake_agent = SimpleNamespace(
    provider="custom",
    model="test-model",
    base_url="https://custom.invalid/v1",
    request_overrides={},
)
try:
    _merge_custom_provider_extra_body(
        fake_agent,
        [{
            "base_url": "https://custom.invalid/v1",
            "model": "test-model",
            "extra_body": {"tools": [{"name": "attacker"}]},
        }],
    )
except RuntimeProfileError as exc:
    assert "custom_providers[].extra_body.tools" in str(exc), exc
else:
    raise AssertionError("custom provider extra_body accepted a tools override")

from run_agent import AIAgent

with patch("run_agent.get_tool_definitions", return_value=tools), \
     patch("run_agent.check_toolset_requirements", return_value={}), \
     patch("run_agent.OpenAI"):
    try:
        AIAgent(
            model="test-model",
            provider="custom",
            api_mode="chat_completions",
            base_url="https://custom.invalid/v1",
            api_key="unused",
            request_overrides={"tools": [{"name": "attacker"}]},
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    except RuntimeProfileError as exc:
        assert "request_overrides.tools" in str(exc), exc
    else:
        raise AssertionError("AIAgent accepted a reserved request override")
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_final_wire_bodies_keep_canonical_tools(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
import json

import httpx
from openai import OpenAI

from agent.transports import get_transport
from providers import get_provider_profile

import agent.transports.chat_completions  # noqa: F401
import agent.transports.codex  # noqa: F401

messages = [{"role": "user", "content": "hello"}]
tools = [{
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Run a command",
        "parameters": {"type": "object", "properties": {}},
    },
}]
safe_overrides = {
    "service_tier": "default",
    "extra_body": {"metadata": {"profile_test": True}},
}

chat_kwargs = get_transport("chat_completions").build_kwargs(
    model="test-model",
    messages=messages,
    tools=tools,
    provider_profile=get_provider_profile("custom"),
    request_overrides=safe_overrides,
)
responses_kwargs = get_transport("codex_responses").build_kwargs(
    model="test-model",
    messages=messages,
    tools=tools,
    request_overrides=safe_overrides,
)

captured = {}
def capture(request):
    captured[request.url.path] = json.loads(request.content)
    return httpx.Response(418, json={"error": {"message": "captured"}})

client = OpenAI(
    api_key="unused",
    base_url="https://capture.invalid/v1",
    max_retries=0,
    http_client=httpx.Client(transport=httpx.MockTransport(capture)),
)
for call in (
    lambda: client.chat.completions.create(**chat_kwargs),
    lambda: client.responses.create(**responses_kwargs, stream=True),
):
    try:
        call()
    except Exception:
        pass

chat_body = captured["/v1/chat/completions"]
responses_body = captured["/v1/responses"]
assert [item["function"]["name"] for item in chat_body["tools"]] == ["terminal"]
assert [item["name"] for item in responses_body["tools"]] == ["terminal"]
assert chat_body["messages"] == messages
assert responses_body["input"][0]["content"] == "hello"
assert chat_body["model"] == "test-model"
assert responses_body["model"] == "test-model"
assert chat_body["metadata"] == {"profile_test": True}
assert responses_body["metadata"] == {"profile_test": True}
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_auxiliary_requests_reject_capability_overrides_before_send(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agent.auxiliary_client import (
    _CodexCompletionsAdapter,
    async_call_llm,
    call_llm,
)
from runtime_profile import RuntimeProfileError

messages = [{"role": "user", "content": "hello"}]
resolution = (
    "custom",
    "test-model",
    "https://custom.invalid/v1",
    "unused",
    "chat_completions",
)

for capability_key in ("tools", "plugins", "web_search_options", "unknown_capability"):
    create = MagicMock()
    client = SimpleNamespace(
        base_url="https://custom.invalid/v1",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    with patch(
        "agent.auxiliary_client._resolve_task_provider_model",
        return_value=resolution,
    ), patch(
        "agent.auxiliary_client._get_task_extra_body",
        return_value={},
    ), patch(
        "agent.auxiliary_client._get_cached_client",
        return_value=(client, "test-model"),
    ):
        try:
            call_llm(
                provider="custom",
                model="test-model",
                messages=messages,
                extra_body={capability_key: {"enabled": True}},
            )
        except RuntimeProfileError as exc:
            assert capability_key in str(exc), exc
        else:
            raise AssertionError(
                f"sync auxiliary request accepted {capability_key}"
            )
    assert not create.called, capability_key


async def check_async():
    for capability_key in ("tools", "plugins", "web_search_options", "unknown_capability"):
        create = AsyncMock()
        client = SimpleNamespace(
            base_url="https://custom.invalid/v1",
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        )
        with patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            return_value=resolution,
        ), patch(
            "agent.auxiliary_client._get_task_extra_body",
            return_value={},
        ), patch(
            "agent.auxiliary_client._get_cached_client",
            return_value=(client, "test-model"),
        ):
            try:
                await async_call_llm(
                    provider="custom",
                    model="test-model",
                    messages=messages,
                    extra_body={capability_key: {"enabled": True}},
                )
            except RuntimeProfileError as exc:
                assert capability_key in str(exc), exc
            else:
                raise AssertionError(
                    f"async auxiliary request accepted {capability_key}"
                )
        assert create.await_count == 0, capability_key


asyncio.run(check_async())

responses_create = MagicMock()
real_client = SimpleNamespace(
    responses=SimpleNamespace(create=responses_create),
)
adapter = _CodexCompletionsAdapter(real_client, "test-model")
for extra_body in (
    {"input": [{"role": "user", "content": "attacker"}]},
    {"plugins": [{"id": "web"}]},
    {"web_search_options": {}},
    {"unknown_capability": {"enabled": True}},
):
    try:
        adapter.create(messages=messages, extra_body=extra_body)
    except RuntimeProfileError:
        pass
    else:
        raise AssertionError(f"Codex auxiliary adapter accepted {extra_body}")
assert not responses_create.called
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_no_profile_preserves_reserved_override_behavior(tmp_path):
    code = """
from agent.transports import get_transport
from agent.auxiliary_client import _build_call_kwargs
from runtime_profile import get_runtime_profile

import agent.transports.chat_completions  # noqa: F401
import agent.transports.codex  # noqa: F401

assert get_runtime_profile() is None
messages = [{"role": "user", "content": "hello"}]
canonical_tools = [{
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Run a command",
        "parameters": {"type": "object", "properties": {}},
    },
}]
replacement_tools = [{
    "type": "function",
    "function": {
        "name": "upstream_override",
        "description": "Upstream compatibility",
        "parameters": {"type": "object", "properties": {}},
    },
}]

chat_kwargs = get_transport("chat_completions").build_kwargs(
    model="test-model",
    messages=messages,
    tools=canonical_tools,
    request_overrides={"tools": replacement_tools},
)
assert chat_kwargs["tools"] == replacement_tools

responses_kwargs = get_transport("codex_responses").build_kwargs(
    model="test-model",
    messages=messages,
    tools=canonical_tools,
    request_overrides={"tools": replacement_tools},
)
assert responses_kwargs["tools"] == replacement_tools

auxiliary_kwargs = _build_call_kwargs(
    provider="custom",
    model="test-model",
    messages=messages,
    extra_body={
        "tools": replacement_tools,
        "plugins": [{"id": "upstream-plugin"}],
        "unknown_capability": {"enabled": True},
    },
)
assert auxiliary_kwargs["extra_body"]["tools"] == replacement_tools
assert auxiliary_kwargs["extra_body"]["plugins"] == [{"id": "upstream-plugin"}]
assert auxiliary_kwargs["extra_body"]["unknown_capability"] == {"enabled": True}
"""
    result = _run(code, profile_path=None, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_blocks_auto_detected_model_provider(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
from run_agent import AIAgent
try:
    AIAgent(
        model="test-model",
        provider=None,
        base_url="https://api.x.ai/v1",
        api_key="unused",
        quiet_mode=True,
        skip_memory=True,
    )
except ValueError as exc:
    assert "Effective model provider 'xai'" in str(exc), exc
else:
    raise AssertionError("URL-inferred xai provider bypassed the profile")
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_rejects_non_custom_agent_fallback(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
from run_agent import AIAgent
try:
    AIAgent(
        model="test-model",
        provider="custom",
        base_url="http://127.0.0.1:9/v1",
        api_key="unused",
        api_mode="chat_completions",
        fallback_model={"provider": "openrouter", "model": "fallback"},
        quiet_mode=True,
        skip_memory=True,
    )
except ValueError as exc:
    assert "Fallback model provider 'openrouter'" in str(exc), exc
else:
    raise AssertionError("non-custom fallback provider bypassed the profile")
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_restricts_auxiliary_routing_to_custom(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
from types import SimpleNamespace
from unittest.mock import patch
from runtime_profile import RuntimeProfileError
from agent import auxiliary_client as aux

fake = SimpleNamespace(api_key="key", base_url="http://127.0.0.1:9/v1")
runtime = {
    "provider": "custom",
    "model": "vision-model",
    "base_url": "http://127.0.0.1:9/v1",
    "api_key": "key",
    "api_mode": "chat_completions",
}
with patch.object(aux, "OpenAI", return_value=fake), \
     patch.object(aux, "_try_openrouter", side_effect=AssertionError("openrouter")), \
     patch.object(aux, "_try_nous", side_effect=AssertionError("nous")), \
     patch.object(aux, "_resolve_api_key_provider", side_effect=AssertionError("api-key fallback")):
    client, model = aux.resolve_provider_client(
        "auto", model="vision-model", main_runtime=runtime
    )
    assert client is fake
    assert model == "vision-model"

    provider, vision_client, vision_model = aux.resolve_vision_provider_client(
        provider="auto",
        model="vision-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="key",
    )
    assert provider == "custom"
    assert vision_client is fake
    assert vision_model == "vision-model"

for provider, mode in (("openrouter", None), ("custom", "anthropic_messages")):
    try:
        aux.resolve_provider_client(provider, model="x", api_mode=mode)
    except RuntimeProfileError:
        continue
    raise AssertionError(f"auxiliary policy accepted provider={provider} mode={mode}")
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_disables_all_automatic_install_paths(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
from unittest.mock import patch

from hermes_cli import dep_ensure
with patch.dict(dep_ensure._DEP_CHECKS, {"browser": lambda: False}), \
     patch.object(dep_ensure, "_find_install_script", side_effect=AssertionError("installer lookup")):
    assert dep_ensure.ensure_dependency("browser", interactive=False) is False

from tools import lazy_deps
assert lazy_deps._allow_lazy_installs() is False

from tools import browser_tool
browser_tool._cached_agent_browser = None
browser_tool._agent_browser_resolved = False
def only_npx(name, *args, **kwargs):
    return "/usr/bin/npx" if name == "npx" else None
with patch.object(browser_tool.shutil, "which", side_effect=only_npx), \
     patch.object(browser_tool.Path, "is_dir", return_value=False):
    try:
        browser_tool._find_agent_browser()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("bare npx agent-browser fallback was accepted")

from agent.lsp import install as lsp_install
with patch.object(lsp_install, "_existing_binary", return_value=None), \
     patch.object(lsp_install, "_do_install", side_effect=AssertionError("lsp install")):
    assert lsp_install.try_install("pyright", strategy="auto") is None

from agent.lsp.manager import LSPService
from hermes_cli import config as config_module
with patch.object(config_module, "load_config", return_value={"lsp": {"enabled": True, "install_strategy": "auto"}}), \
     patch.object(LSPService, "__init__", return_value=None) as init:
    LSPService.create_from_config()
    assert init.call_args.kwargs["enabled"] is True
    assert init.call_args.kwargs["install_strategy"] == "manual"

from agent.secret_sources import bitwarden
with patch.object(bitwarden.Path, "exists", return_value=False), \
     patch.object(bitwarden.shutil, "which", return_value=None), \
     patch.object(bitwarden, "install_bws", side_effect=AssertionError("bws install")):
    assert bitwarden.find_bws(install_if_missing=True) is None

from tools import tirith_security
tirith_security._resolved_path = None
tirith_security._install_thread = None
with patch.object(tirith_security, "is_platform_supported", return_value=True), \
     patch.object(tirith_security.shutil, "which", return_value=None), \
     patch.object(tirith_security, "_hermes_bin_dir", return_value="/missing"), \
     patch.object(tirith_security, "_install_tirith", side_effect=AssertionError("tirith install")), \
     patch.object(tirith_security.threading, "Thread", side_effect=AssertionError("install thread")):
    assert tirith_security.ensure_installed() is None

from pathlib import Path
from hermes_cli import main as cli_main
with patch.object(cli_main, "_ensure_tui_node", return_value=None), \
     patch.object(cli_main, "_find_bundled_tui", return_value=None), \
     patch.object(cli_main, "_tui_need_npm_install", return_value=True), \
     patch.object(cli_main, "_is_termux_startup_environment", return_value=False), \
     patch.object(cli_main.shutil, "which", side_effect=lambda name: f"/bin/{name}"), \
     patch.object(cli_main.subprocess, "run", side_effect=AssertionError("npm install")):
    try:
        cli_main._make_tui_argv(Path("/unused/tui"), tui_dev=False)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("TUI npm install policy did not fail closed")
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_tool_ceiling_blocks_direct_and_executor_dispatch(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def tool_defs(*names):
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def fail(label):
    def _raise(*args, **kwargs):
        raise AssertionError(label)
    return _raise


def tool_call(call_id):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="clarify",
            arguments='{"question":"should not run"}',
        ),
    )


with patch("run_agent.get_tool_definitions", return_value=tool_defs("todo")), \\
     patch("run_agent.check_toolset_requirements", return_value={}), \\
     patch("run_agent.OpenAI"):
    agent = AIAgent(
        model="test-model",
        provider="custom",
        api_mode="chat_completions",
        base_url="http://127.0.0.1:9/v1",
        api_key="unused",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        clarify_callback=fail("clarify callback ran"),
    )

agent.tool_delay = 0
agent.tool_progress_callback = fail("progress callback ran")
agent.tool_start_callback = fail("start callback ran")
agent.tool_complete_callback = fail("complete callback ran")
agent._tool_guardrails.before_call = MagicMock(side_effect=fail("guardrail ran"))

expected = {"error": "Tool disabled by runtime profile: clarify"}
with patch(
    "hermes_cli.middleware.apply_tool_request_middleware",
    side_effect=fail("request middleware ran"),
), patch(
    "hermes_cli.plugins.get_pre_tool_call_block_message",
    side_effect=fail("plugin hook ran"),
), patch(
    "tools.clarify_tool.clarify_tool",
    side_effect=fail("clarify handler ran"),
), patch(
    "run_agent.handle_function_call",
    side_effect=fail("registry handler ran"),
):
    assert json.loads(
        agent._invoke_tool("clarify", {"question": "direct"}, "task")
    ) == expected

    sequential_messages = []
    agent._execute_tool_calls_sequential(
        SimpleNamespace(tool_calls=[tool_call("sequential")]),
        sequential_messages,
        "task",
    )
    assert json.loads(sequential_messages[0]["content"]) == expected

    concurrent_messages = []
    agent._execute_tool_calls_concurrent(
        SimpleNamespace(tool_calls=[tool_call("concurrent-1"), tool_call("concurrent-2")]),
        concurrent_messages,
        "task",
    )
    assert [json.loads(message["content"]) for message in concurrent_messages] == [
        expected,
        expected,
    ]

agent._tool_guardrails.before_call.assert_not_called()
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_mcp_bottom_layer_is_fail_closed(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from tools import mcp_tool
from tools.registry import registry

mcp_tool._MCP_AVAILABLE = True
with patch.object(
    mcp_tool,
    "_load_mcp_config",
    side_effect=AssertionError("MCP config was loaded"),
) as load_config, patch.object(
    mcp_tool,
    "_stop_mcp_loop",
    side_effect=AssertionError("MCP loop was stopped"),
) as stop_loop, patch.object(
    mcp_tool.asyncio,
    "new_event_loop",
    side_effect=AssertionError("MCP loop was started"),
) as new_loop:
    assert mcp_tool.register_mcp_servers({"blocked": {"command": "false"}}) == []
    assert mcp_tool.discover_mcp_tools() == []
    assert mcp_tool.get_mcp_status() == []
    assert mcp_tool.probe_mcp_server_tools() == {}
    assert mcp_tool.is_mcp_tool_parallel_safe("mcp_blocked_tool") is False
    assert mcp_tool.shutdown_mcp_servers() is None
    assert mcp_tool._kill_orphaned_mcp_children() is None

    try:
        mcp_tool._ensure_mcp_loop()
    except RuntimeError as exc:
        assert "disabled by the runtime profile" in str(exc)
    else:
        raise AssertionError("MCP loop startup bypassed the runtime profile")

    try:
        asyncio.run(mcp_tool._connect_server("blocked", {"command": "false"}))
    except RuntimeError as exc:
        assert "disabled by the runtime profile" in str(exc)
    else:
        raise AssertionError("MCP connection bypassed the runtime profile")

    server = mcp_tool.MCPServerTask("blocked")
    server._tools = [SimpleNamespace(name="tool", description="")]
    blocked_tool_name = "mcp_blocked_tool"
    assert registry.get_entry(blocked_tool_name) is None
    assert mcp_tool._register_server_tools("blocked", server, {}) == []
    assert registry.get_entry(blocked_tool_name) is None

    try:
        asyncio.run(server.start({"command": "false"}))
    except RuntimeError as exc:
        assert "disabled by the runtime profile" in str(exc)
    else:
        raise AssertionError("MCPServerTask.start bypassed the runtime profile")

load_config.assert_not_called()
stop_loop.assert_not_called()
new_loop.assert_not_called()
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_plugins_fail_closed_before_import(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
import tempfile
from pathlib import Path
from hermes_cli.plugins import PluginManager
from runtime_profile import get_runtime_profile

profile = get_runtime_profile()
assert not profile.allows_plugin(source="mystery", kind="standalone", key="x")
assert not profile.allows_plugin(source="bundled", kind="platform", key="x")

with tempfile.TemporaryDirectory() as raw:
    plugin_dir = Path(raw) / "future-platform"
    plugin_dir.mkdir()
    manifest = plugin_dir / "plugin.yaml"
    manifest.write_text("name: future\\nkind: platform-v2\\n", encoding="utf-8")
    assert PluginManager()._parse_manifest(
        manifest, plugin_dir, "bundled", "platforms"
    ) is None
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_discovers_only_custom_model_provider(tmp_path):
    profile_path = _write_profile(tmp_path)
    code = """
import sys
from providers import get_provider_profile, list_providers
assert get_provider_profile("custom").name == "custom"
assert get_provider_profile("nvidia") is None
assert [profile.name for profile in list_providers()] == ["custom"]
mods = [name for name in sys.modules if name.startswith("plugins.model_providers.")]
assert mods == ["plugins.model_providers.custom"], mods
"""
    result = _run(code, profile_path=profile_path, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout


def test_no_profile_keeps_upstream_discover_all_behavior(tmp_path):
    code = """
import sys
import model_tools
names = set(model_tools.get_all_tool_names())
assert {"web_search", "clarify", "text_to_speech", "video_analyze"} <= names
assert "tools.web_tools" in sys.modules
assert "tools.tts_tool" in sys.modules
"""
    result = _run(code, profile_path=None, home=tmp_path / "home")
    assert result.returncode == 0, result.stderr or result.stdout

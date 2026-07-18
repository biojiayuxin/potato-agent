"""Fail-closed policy gates for the generic gateway runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]

_PROFILE_YAML = """\
schema_version: 1
name: gateway-test
revision: 1
toolsets:
  enabled:
    - terminal
    - file
    - vision
    - browser
    - skills
    - code_execution
    - todo
    - memory
    - session_search
    - delegation
  disabled: [cronjob, kanban]
expected_tools:
  - terminal
  - process
  - read_file
  - write_file
  - patch
  - search_files
  - vision_analyze
  - browser_navigate
  - browser_snapshot
  - browser_click
  - browser_type
  - browser_scroll
  - browser_back
  - browser_press
  - browser_get_images
  - browser_vision
  - browser_console
  - browser_cdp
  - browser_dialog
  - skills_list
  - skill_view
  - skill_manage
  - execute_code
  - todo
  - memory
  - session_search
  - delegate_task
plugins:
  allow_user: false
  allow_project: false
  allow_entrypoint: false
  allowed_general_keys: []
  forbidden_kinds: [platform]
providers:
  model: [custom]
  api_modes: [chat_completions]
  browser: local
  memory: builtin
  context_engine: compressor
  web: []
mcp:
  enabled: false
runtime:
  allow_lazy_installs: false
  lsp_enabled: true
  lsp_install_strategy: manual
  terminal_backend: local
  skills_dependency_strategy: user_managed
"""

_NEGATIVE_SMOKE = r"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway import run as gateway_run

for env_var in (
    gateway_run._DISABLE_GATEWAY_PLATFORMS_ENV,
    gateway_run._DISABLE_MCP_ENV,
    gateway_run._DISABLE_CRON_ENV,
    gateway_run._DISABLE_KANBAN_ENV,
):
    assert gateway_run._gateway_subsystem_disabled(env_var), env_var

runner = gateway_run.GatewayRunner(
    GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test")
        },
        sessions_dir=gateway_run._hermes_home / "sessions",
    )
)

# The direct factory is guarded too, so future callers cannot bypass start().
assert runner._create_adapter(
    Platform.TELEGRAM,
    PlatformConfig(enabled=True, token="test"),
) is None
gateway_run._start_cron_ticker(gateway_run.threading.Event())

scheduled = []

class _Task:
    def add_done_callback(self, callback):
        return None

    def cancel(self):
        return None

def _capture_task(coro):
    scheduled.append(getattr(coro, "__qualname__", repr(coro)))
    coro.close()
    return _Task()

runner.hooks.discover_and_load = lambda: None
runner.hooks.emit = AsyncMock()
runner._send_update_notification = AsyncMock(return_value=True)
runner._send_restart_notification = AsyncMock()
runner._schedule_resume_pending_sessions = lambda *args, **kwargs: 0
runner._wire_teams_pipeline_runtime = lambda: None

async def _exercise_runner():
    with (
        patch("hermes_cli.plugins.discover_plugins"),
        patch("agent.shell_hooks.register_from_config"),
        patch("hermes_cli.config.load_config", return_value={}),
        patch(
            "tools.process_registry.process_registry.recover_from_checkpoint",
            return_value=0,
        ),
        patch(
            "gateway.channel_directory.build_channel_directory",
            new=AsyncMock(return_value={"platforms": {}}),
        ),
        patch("gateway.run.asyncio.create_task", side_effect=_capture_task),
    ):
        assert await runner.start() is True
        assert await runner._execute_mcp_reload(None) == (
            "MCP is disabled by runtime policy."
        )

    assert runner.adapters == {}
    assert not any("kanban_notifier" in name for name in scheduled), scheduled
    assert not any("kanban_dispatcher" in name for name in scheduled), scheduled
    assert not any("platform_reconnect" in name for name in scheduled), scheduled

asyncio.run(_exercise_runner())

class _Runner:
    def __init__(self, config):
        self.adapters = {}
        self.should_exit_cleanly = False
        self.should_exit_with_failure = False
        self.exit_reason = None
        self.exit_code = None
        self._restart_requested = False
        self._restart_via_service = False

    async def start(self):
        return True

    async def wait_for_shutdown(self):
        return None

async def _exercise_process_entrypoint():
    with (
        patch.object(gateway_run, "GatewayRunner", _Runner),
        patch("gateway.status.get_running_pid", return_value=None),
        patch("gateway.status.acquire_gateway_runtime_lock", return_value=True),
        patch("gateway.status.write_pid_file"),
        patch("gateway.status.remove_pid_file"),
        patch("gateway.status.release_gateway_runtime_lock"),
        patch("tools.skills_sync.sync_skills"),
        patch("hermes_logging.setup_logging"),
    ):
        assert await gateway_run.start_gateway(
            config=GatewayConfig(), verbosity=None
        ) is True

asyncio.run(_exercise_process_entrypoint())

for module_name in (
    "gateway.platforms.telegram",
    "gateway.platforms.whatsapp",
    "gateway.platforms.slack",
    "tools.mcp_tool",
    "cron.scheduler",
    "hermes_cli.kanban_db",
):
    assert module_name not in sys.modules, module_name
"""

_NO_PROFILE_COMPAT_SMOKE = r"""
import asyncio
import sys
import types
from unittest.mock import patch

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway import run as gateway_run

for env_var in (
    gateway_run._DISABLE_GATEWAY_PLATFORMS_ENV,
    gateway_run._DISABLE_MCP_ENV,
    gateway_run._DISABLE_CRON_ENV,
    gateway_run._DISABLE_KANBAN_ENV,
):
    assert not gateway_run._gateway_subsystem_disabled(env_var), env_var

telegram_module = types.ModuleType("gateway.platforms.telegram")

class _TelegramAdapter:
    def __init__(self, config):
        self.config = config

telegram_module.TelegramAdapter = _TelegramAdapter
telegram_module.check_telegram_requirements = lambda: True
sys.modules[telegram_module.__name__] = telegram_module

runner = object.__new__(gateway_run.GatewayRunner)
runner.config = GatewayConfig()
adapter = runner._create_adapter(
    Platform.TELEGRAM,
    PlatformConfig(enabled=True, token="test"),
)
assert isinstance(adapter, _TelegramAdapter)

mcp_calls = {"discover": 0, "shutdown": 0}
mcp_module = types.ModuleType("tools.mcp_tool")

def _discover():
    mcp_calls["discover"] += 1
    return []

def _shutdown():
    mcp_calls["shutdown"] += 1

mcp_module.discover_mcp_tools = _discover
mcp_module.shutdown_mcp_servers = _shutdown
sys.modules[mcp_module.__name__] = mcp_module

cron_calls = []

def _cron_target(*args, **kwargs):
    cron_calls.append((args, kwargs))

gateway_run._start_cron_ticker = _cron_target

class _Runner:
    def __init__(self, config):
        self.adapters = {}
        self.should_exit_cleanly = False
        self.should_exit_with_failure = False
        self.exit_reason = None
        self.exit_code = None
        self._restart_requested = False
        self._restart_via_service = False

    async def start(self):
        return True

    async def wait_for_shutdown(self):
        return None

async def _exercise():
    with (
        patch.object(gateway_run, "GatewayRunner", _Runner),
        patch("gateway.status.get_running_pid", return_value=None),
        patch("gateway.status.acquire_gateway_runtime_lock", return_value=True),
        patch("gateway.status.write_pid_file"),
        patch("gateway.status.remove_pid_file"),
        patch("gateway.status.release_gateway_runtime_lock"),
        patch("tools.skills_sync.sync_skills"),
        patch("hermes_logging.setup_logging"),
    ):
        assert await gateway_run.start_gateway(
            config=GatewayConfig(), verbosity=None
        ) is True

asyncio.run(_exercise())

assert mcp_calls == {"discover": 1, "shutdown": 1}, mcp_calls
assert len(cron_calls) == 1, cron_calls
"""

_PROFILE_SEMANTICS_SMOKE = r"""
from types import SimpleNamespace
from unittest.mock import patch

from gateway import run as gateway_run

enabled_profile = SimpleNamespace(
    forbidden_plugin_kinds=frozenset(),
    mcp_enabled=True,
    enabled_toolsets=("cronjob", "kanban"),
    disabled_toolsets=frozenset(),
)
with patch("runtime_profile.get_runtime_profile", return_value=enabled_profile):
    assert not gateway_run._gateway_subsystem_disabled(
        gateway_run._DISABLE_GATEWAY_PLATFORMS_ENV
    )
    assert not gateway_run._gateway_subsystem_disabled(
        gateway_run._DISABLE_MCP_ENV
    )
    assert not gateway_run._gateway_subsystem_disabled(
        gateway_run._DISABLE_CRON_ENV
    )
    assert not gateway_run._gateway_subsystem_disabled(
        gateway_run._DISABLE_KANBAN_ENV
    )
"""


def _run_subprocess(
    code: str,
    *,
    tmp_path: Path,
    profile: bool = False,
    feature_env: bool = False,
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    hermes_home = home / ".hermes"
    hermes_home.mkdir(parents=True)
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("HERMES_"):
            env.pop(key, None)
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(hermes_home),
            "PYTHONPATH": str(PROJECT_ROOT),
        }
    )
    if profile:
        profile_path = tmp_path / "runtime-profile.yaml"
        profile_path.write_text(_PROFILE_YAML, encoding="utf-8")
        env["HERMES_RUNTIME_PROFILE_PATH"] = str(profile_path)
    if feature_env:
        env.update(
            {
                "HERMES_DISABLE_GATEWAY_PLATFORMS": "1",
                "HERMES_DISABLE_MCP": "true",
                "HERMES_DISABLE_CRON": "yes",
                "HERMES_DISABLE_KANBAN": "on",
                "HERMES_DISABLE_LAZY_INSTALLS": "1",
            }
        )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


@pytest.mark.parametrize("gate_source", ["profile", "environment"])
def test_gateway_subsystems_fail_closed(tmp_path: Path, gate_source: str) -> None:
    result = _run_subprocess(
        _NEGATIVE_SMOKE,
        tmp_path=tmp_path,
        profile=gate_source == "profile",
        feature_env=gate_source == "environment",
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_no_profile_preserves_gateway_subsystems(tmp_path: Path) -> None:
    result = _run_subprocess(_NO_PROFILE_COMPAT_SMOKE, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr or result.stdout


def test_profile_gates_follow_profile_fields(tmp_path: Path) -> None:
    result = _run_subprocess(_PROFILE_SEMANTICS_SMOKE, tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr or result.stdout

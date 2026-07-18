"""Process-level tests for the global automatic-install kill switch."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_policy_probe(
    code: str, *, home: Path, disabled: bool
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("HERMES_"):
            env.pop(key, None)
    home.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(home / ".hermes"),
            "PYTHONPATH": str(REPO_ROOT),
            # The repository-wide pytest fixture disables Tirith. Override it
            # so this subprocess reaches the real background-install branch.
            "TIRITH_ENABLED": "1",
        }
    )
    if disabled:
        env["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_env_only_kill_switch_blocks_every_automatic_install_path(tmp_path):
    code = r'''
from unittest.mock import patch

from runtime_profile import automatic_installs_disabled, get_runtime_profile

assert get_runtime_profile() is None
assert automatic_installs_disabled() is True

from hermes_cli import dep_ensure
with patch.dict(dep_ensure._DEP_CHECKS, {"browser": lambda: False}), \
     patch.object(dep_ensure, "_find_install_script", side_effect=AssertionError("installer lookup")):
    assert dep_ensure.ensure_dependency("browser", interactive=False) is False

from tools import lazy_deps
with patch("hermes_cli.config.load_config", return_value={"security": {"allow_lazy_installs": True}}):
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
lsp_install._install_results.clear()
with patch.object(lsp_install, "_existing_binary", return_value=None), \
     patch.object(lsp_install, "_do_install", side_effect=AssertionError("lsp install")):
    assert lsp_install.try_install("pyright", strategy="auto") is None

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

from hermes_cli import main as cli_main
with patch.object(cli_main.shutil, "which", return_value=None), \
     patch.object(cli_main.subprocess, "run", side_effect=AssertionError("node bootstrap")):
    cli_main._ensure_tui_node()
'''
    result = _run_policy_probe(code, home=tmp_path / "disabled", disabled=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_no_profile_without_kill_switch_preserves_upstream_install_semantics(tmp_path):
    code = r'''
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime_profile import automatic_installs_disabled, get_runtime_profile

assert get_runtime_profile() is None
assert automatic_installs_disabled() is False

from tools import lazy_deps
with patch("hermes_cli.config.load_config", return_value={"security": {}}):
    assert lazy_deps._allow_lazy_installs() is True

from hermes_cli import dep_ensure
with patch.dict(dep_ensure._DEP_CHECKS, {"browser": lambda: False}), \
     patch.object(dep_ensure, "_find_install_script", side_effect=RuntimeError("installer reached")):
    try:
        dep_ensure.ensure_dependency("browser", interactive=False)
    except RuntimeError as exc:
        assert str(exc) == "installer reached"
    else:
        raise AssertionError("dependency installer lookup was unexpectedly blocked")

from tools import browser_tool
browser_tool._cached_agent_browser = None
browser_tool._agent_browser_resolved = False
def only_npx(name, *args, **kwargs):
    return "/usr/bin/npx" if name == "npx" else None
with patch.object(browser_tool.shutil, "which", side_effect=only_npx), \
     patch.object(browser_tool.Path, "is_dir", return_value=False):
    assert browser_tool._find_agent_browser() == "npx agent-browser"

from agent.lsp import install as lsp_install
lsp_install._install_results.clear()
with patch.object(lsp_install, "_existing_binary", return_value=None), \
     patch.object(lsp_install, "_do_install", return_value="/fake/pyright") as install:
    assert lsp_install.try_install("pyright", strategy="auto") == "/fake/pyright"
    install.assert_called_once_with("pyright")

from agent.secret_sources import bitwarden
with patch.object(bitwarden.Path, "exists", return_value=False), \
     patch.object(bitwarden.shutil, "which", return_value=None), \
     patch.object(bitwarden, "install_bws", return_value=Path("/fake/bws")) as install:
    assert bitwarden.find_bws(install_if_missing=True) == Path("/fake/bws")
    install.assert_called_once_with()

from tools import tirith_security
tirith_security._resolved_path = None
tirith_security._install_thread = None
thread = SimpleNamespace(start=lambda: None, is_alive=lambda: False)
with patch.object(tirith_security, "is_platform_supported", return_value=True), \
     patch.object(tirith_security.shutil, "which", return_value=None), \
     patch.object(tirith_security, "_hermes_bin_dir", return_value="/missing"), \
     patch.object(tirith_security, "_read_failure_reason", return_value=None), \
     patch.object(tirith_security.threading, "Thread", return_value=thread) as thread_factory:
    assert tirith_security.ensure_installed() is None
    thread_factory.assert_called_once()

from hermes_cli import main as cli_main
with patch.object(cli_main.shutil, "which", return_value=None), \
     patch.object(cli_main.subprocess, "run", return_value=SimpleNamespace(stdout="")) as run:
    cli_main._ensure_tui_node()
    run.assert_called_once()
'''
    result = _run_policy_probe(code, home=tmp_path / "enabled", disabled=False)
    assert result.returncode == 0, result.stderr or result.stdout

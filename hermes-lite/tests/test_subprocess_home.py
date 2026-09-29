from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="Potato local Linux runtime")


@pytest.fixture
def user_workspace(runtime_paths, monkeypatch):
    legacy_home = runtime_paths.hermes_home / "home"
    legacy_home.mkdir()
    (legacy_home / "keep.txt").write_text("existing user data", encoding="utf-8")
    workspace = runtime_paths.home / "project space"
    workspace.mkdir()
    (runtime_paths.hermes_home / "config.yaml").write_text(
        "terminal:\n  backend: local\n  auto_source_bashrc: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("TERMINAL_CWD", str(workspace))
    monkeypatch.chdir(workspace)
    yield workspace
    assert (legacy_home / "keep.txt").read_text(encoding="utf-8") == "existing user data"


@pytest.mark.parametrize("profile", ["absent", "default", "context"])
def test_child_environment_builders_keep_user_home(
    runtime_paths, user_workspace, profile
):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.environments.local import (
        _make_run_env,
        _sanitize_subprocess_env,
        hermes_subprocess_env,
    )

    profile_dir = runtime_paths.hermes_home
    if profile == "context":
        profile_dir = runtime_paths.hermes_home / "profiles" / "resumed"
        (profile_dir / "home").mkdir(parents=True)
    elif profile == "absent":
        # Exercise the existing no-legacy-home behavior without removing data.
        profile_dir = runtime_paths.hermes_home / "profiles" / "fresh"
        profile_dir.mkdir(parents=True)
    token = set_hermes_home_override(profile_dir)
    try:
        environments = [
            _make_run_env({}),
            _sanitize_subprocess_env(os.environ),
            hermes_subprocess_env(),
            hermes_subprocess_env(inherit_credentials=True),
        ]
        for env in environments:
            assert env["HOME"] == str(runtime_paths.home)
            assert env["HERMES_HOME"] == str(profile_dir)
            assert env["TERMINAL_CWD"] == str(user_workspace)
        assert Path.home() == runtime_paths.home
    finally:
        reset_hermes_home_override(token)


def test_terminal_cd_home_agrees_with_prompt_and_file_tools(
    runtime_paths, user_workspace
):
    from agent.prompt_builder import build_environment_hints
    from tools.environments.local import LocalEnvironment
    from tools.file_tools import _resolve_path_for_task

    hints = build_environment_hints()
    assert f"User home directory: {runtime_paths.home}" in hints
    assert f"Current working directory: {user_workspace}" in hints
    env = LocalEnvironment(cwd=str(user_workspace), timeout=5)
    try:
        result = env.execute('printf "%s\\n" "$HOME" ~ "$PWD"')
        assert result["returncode"] == 0
        assert result["output"].splitlines() == [
            str(runtime_paths.home), str(runtime_paths.home), str(user_workspace)
        ]
        result = env.execute("cd ~; printf 'at home' > home-probe.txt; pwd")
        assert result["returncode"] == 0
        assert result["output"].strip() == str(runtime_paths.home)
        assert _resolve_path_for_task("~/home-probe.txt").read_text() == "at home"
        result = env.execute("pwd")
        assert result["returncode"] == 0
        assert result["output"].strip() == str(runtime_paths.home)
    finally:
        env.cleanup()


@pytest.mark.parametrize("use_pty", [False, True])
def test_background_cd_uses_user_home(
    runtime_paths, user_workspace, monkeypatch, use_pty
):
    from tools import process_registry as module

    if use_pty:
        pytest.importorskip("ptyprocess")
    monkeypatch.setattr(module, "CHECKPOINT_PATH", runtime_paths.hermes_home / "processes.json")
    registry = module.ProcessRegistry()
    session = registry.spawn_local(
        'printf "%s\\n" "$HOME" "$PWD"; cd; pwd',
        cwd=str(user_workspace),
        use_pty=use_pty,
    )
    try:
        if use_pty:
            assert session._pty is not None, "PTY launch fell back to pipe mode"
        result = registry.wait(session.id, timeout=5)
        assert result["status"] == "exited"
        assert result["exit_code"] == 0
        assert result["output"].splitlines() == [
            str(runtime_paths.home), str(user_workspace), str(runtime_paths.home)
        ]
    finally:
        if not session.exited:
            registry.kill_process(session.id)
        if session._reader_thread is not None:
            session._reader_thread.join(timeout=5)


@pytest.mark.parametrize("mode", ["project", "strict"])
def test_execute_code_uses_user_home(runtime_paths, user_workspace, monkeypatch, mode):
    from tools import code_execution_tool as module

    monkeypatch.setattr(module, "_load_config", lambda: {"mode": mode, "timeout": 5})
    result = json.loads(module.execute_code(
        "import json, os\n"
        "from pathlib import Path\n"
        "print(json.dumps({'home': os.environ['HOME'], "
        "'expanded_home': str(Path.home()), 'cwd': os.getcwd()}))\n",
        task_id="home-regression",
        enabled_tools=["terminal"],
    ))
    assert result["status"] == "success", result
    payload = json.loads(result["output"])
    assert payload["home"] == str(runtime_paths.home)
    assert payload["expanded_home"] == str(runtime_paths.home)
    if mode == "project":
        assert payload["cwd"] == str(user_workspace)
    else:
        assert Path(payload["cwd"]).name.startswith("hermes_sandbox_")

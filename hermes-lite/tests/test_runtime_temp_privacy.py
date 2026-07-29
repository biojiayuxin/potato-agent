from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_local_environment_uses_private_temp_tree_and_cleans_results(tmp_path) -> None:
    from tools.environments.local import LocalEnvironment
    from tools.tool_result_storage import _write_to_sandbox

    temp_root = tmp_path / "shared-temp"
    temp_root.mkdir(mode=0o777)
    old_umask = os.umask(0o022)
    try:
        env = LocalEnvironment(
            cwd=str(tmp_path),
            timeout=10,
            env={"TMPDIR": str(temp_root)},
        )
    finally:
        os.umask(old_umask)

    session_temp = Path(env.get_temp_dir())
    try:
        assert session_temp.parent == temp_root
        assert _mode(session_temp) == 0o700
        assert _mode(Path(env._snapshot_path)) == 0o600
        assert _mode(Path(env._cwd_file)) == 0o600

        result_path = session_temp / "hermes-results" / "result.txt"
        assert _write_to_sandbox("private output", str(result_path), env) is True
        assert result_path.read_text(encoding="utf-8") == "private output"
        assert _mode(result_path.parent) == 0o700
        assert _mode(result_path) == 0o600
    finally:
        env.cleanup()

    assert not session_temp.exists()


@pytest.mark.skipif(os.name == "nt", reason="uses the POSIX script-fd path")
def test_local_environment_keeps_shell_script_out_of_argv_and_preserves_stdin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools.environments import local as local_module

    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    captured_argv: list[list[str]] = []
    original_popen = subprocess.Popen

    def recording_popen(args, *popen_args, **popen_kwargs):
        captured_argv.append([str(item) for item in args])
        return original_popen(args, *popen_args, **popen_kwargs)

    monkeypatch.setattr(local_module.subprocess, "Popen", recording_popen)
    env = local_module.LocalEnvironment(
        cwd=str(tmp_path),
        timeout=10,
        env={"TMPDIR": str(temp_root)},
    )
    sentinel = "argv-private-command-7f81"
    try:
        result = env.execute(f"printf %s {sentinel}")
        assert result == {"output": sentinel, "returncode": 0}

        payload = "stdin remains independent\n"
        result = env.execute("cat > stdin-output.txt", stdin_data=payload)
        assert result["returncode"] == 0
        assert (tmp_path / "stdin-output.txt").read_text(encoding="utf-8") == payload
    finally:
        env.cleanup()

    assert captured_argv
    assert all(
        sentinel not in argument
        for invocation in captured_argv
        for argument in invocation
    )
    assert all("-c" not in invocation for invocation in captured_argv)

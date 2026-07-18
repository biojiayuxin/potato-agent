from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _environment(runtime_paths) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(runtime_paths.home),
            "HERMES_HOME": str(runtime_paths.hermes_home),
            "HERMES_RUNTIME_PROFILE_PATH": str(runtime_paths.profile),
            "HERMES_GATEWAY_LOCK_DIR": str(
                runtime_paths.state_home / "gateway-locks"
            ),
            "PYTHONPATH": str(runtime_paths.source),
            "XDG_STATE_HOME": str(runtime_paths.state_home),
        }
    )
    return env


def _start(runtime_paths, *extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "potato_hermes_lite.cli",
            "gateway",
            "run",
            *extra,
        ],
        cwd=runtime_paths.home,
        env=_environment(runtime_paths),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not reached before timeout")


def _pid_from(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload["pid"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal lifecycle contract")
def test_gateway_guard_enforces_single_process_replace_and_clean_stop(
    runtime_paths,
) -> None:
    pid_path = runtime_paths.hermes_home / "gateway.pid"
    state_path = runtime_paths.hermes_home / "gateway_state.json"
    first = _start(runtime_paths)
    replacement = None
    try:
        _wait_until(lambda: _pid_from(pid_path) == first.pid)
        _wait_until(
            lambda: json.loads(state_path.read_text(encoding="utf-8")).get(
                "gateway_state"
            )
            == "running"
        )

        duplicate = subprocess.run(
            [
                sys.executable,
                "-m",
                "potato_hermes_lite.cli",
                "gateway",
                "run",
            ],
            cwd=runtime_paths.home,
            env=_environment(runtime_paths),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert duplicate.returncode != 0
        assert "already running" in (duplicate.stdout + duplicate.stderr)
        assert first.poll() is None

        replacement = _start(runtime_paths, "--replace")
        assert first.wait(timeout=10) == 0
        _wait_until(lambda: _pid_from(pid_path) == replacement.pid)

        os.kill(replacement.pid, signal.SIGTERM)
        assert replacement.wait(timeout=10) == 0
        _wait_until(lambda: not pid_path.exists())
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["gateway_state"] == "stopped"
        assert state["exit_reason"] == "signal"
    finally:
        _stop(first)
        if replacement is not None:
            _stop(replacement)


def test_gateway_guard_fails_closed_without_runtime_profile(
    runtime_paths,
) -> None:
    env = _environment(runtime_paths)
    env.pop("HERMES_RUNTIME_PROFILE_PATH")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "potato_hermes_lite.cli",
            "gateway",
            "run",
        ],
        cwd=runtime_paths.home,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "requires HERMES_RUNTIME_PROFILE_PATH" in (result.stdout + result.stderr)


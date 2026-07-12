from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from pathlib import Path
from typing import Sequence


SESSION_DB_INNER_TIMEOUT_SECONDS = 60.0
SESSION_DB_HELPER_TIMEOUT_SECONDS = 70.0


def run_process_group(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    input_text: str | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with a timeout that terminates its whole process group."""
    normalized_command = [str(item) for item in command]
    process = subprocess.Popen(
        normalized_command,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(
            input=input_text,
            timeout=max(float(timeout_seconds), 0.1),
        )
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            normalized_command,
            timeout_seconds,
            output=stdout,
            stderr=stderr,
        ) from exc

    return subprocess.CompletedProcess(
        normalized_command,
        int(process.returncode or 0),
        stdout=stdout,
        stderr=stderr,
    )

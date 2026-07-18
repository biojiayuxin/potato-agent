"""Profile-sealed foreground guard used by per-user systemd services."""

from __future__ import annotations

import os
import signal
import threading
import time

from gateway.status import (
    acquire_gateway_runtime_lock,
    get_running_pid,
    release_gateway_runtime_lock,
    remove_pid_file,
    terminate_pid,
    write_pid_file,
    write_runtime_status,
)
from runtime_profile import RuntimeProfileError, get_runtime_profile


def require_potato_profile():
    profile = get_runtime_profile()
    if profile is None:
        raise RuntimeProfileError(
            "Potato Hermes Lite requires HERMES_RUNTIME_PROFILE_PATH"
        )
    if profile.name != "potato":
        raise RuntimeProfileError(
            f"Potato Hermes Lite requires profile 'potato', got {profile.name!r}"
        )
    return profile


def _wait_for_exit(pid: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = get_running_pid(cleanup_stale=True)
        if current != pid:
            return True
        time.sleep(0.05)
    return False


def run_gateway_guard(*, replace: bool, verbosity: int | None = 0) -> int:
    del verbosity  # The guard has no messaging subsystem to configure.
    require_potato_profile()

    existing = get_running_pid(cleanup_stale=True)
    if existing is not None and existing != os.getpid():
        if not replace:
            raise RuntimeError(f"Potato Hermes runtime is already running (PID {existing})")
        terminate_pid(existing)
        if not _wait_for_exit(existing):
            terminate_pid(existing, force=True)
            if not _wait_for_exit(existing, timeout=5.0):
                raise RuntimeError(f"Could not replace Potato Hermes runtime PID {existing}")

    if not acquire_gateway_runtime_lock():
        raise RuntimeError("Could not acquire Potato Hermes runtime lock")

    stop = threading.Event()
    previous_handlers: dict[int, object] = {}

    def _request_stop(_signum, _frame) -> None:
        stop.set()

    try:
        for name in ("SIGTERM", "SIGINT"):
            signum = getattr(signal, name, None)
            if signum is not None:
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, _request_stop)
        write_pid_file()
        write_runtime_status(
            gateway_state="running",
            exit_reason=None,
            restart_requested=False,
            active_agents=0,
        )
        stop.wait()
        write_runtime_status(
            gateway_state="stopped",
            exit_reason="signal",
            restart_requested=False,
            active_agents=0,
        )
        return 0
    finally:
        remove_pid_file()
        release_gateway_runtime_lock()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

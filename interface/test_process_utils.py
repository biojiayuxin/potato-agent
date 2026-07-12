from __future__ import annotations

import signal
import subprocess

import pytest

from interface import process_utils


class _FakeProcess:
    def __init__(self, communicate_results):
        self.pid = 4321
        self.returncode = None
        self._communicate_results = iter(communicate_results)
        self.kill_calls = 0

    def communicate(self, *, input=None, timeout=None):
        result = next(self._communicate_results)
        if isinstance(result, BaseException):
            raise result
        return result

    def kill(self) -> None:
        self.kill_calls += 1


def test_process_group_timeout_sends_term_before_raising(monkeypatch) -> None:
    process = _FakeProcess(
        [
            subprocess.TimeoutExpired(["worker"], 1),
            ("partial output", "partial error"),
        ]
    )
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(process_utils.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        process_utils.os,
        "killpg",
        lambda pid, sent_signal: signals.append((pid, sent_signal)),
    )

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        process_utils.run_process_group(["worker"], timeout_seconds=1)

    assert signals == [(process.pid, signal.SIGTERM)]
    assert process.kill_calls == 0
    assert exc_info.value.output == "partial output"
    assert exc_info.value.stderr == "partial error"


def test_process_group_timeout_escalates_to_kill(monkeypatch) -> None:
    process = _FakeProcess(
        [
            subprocess.TimeoutExpired(["worker"], 1),
            subprocess.TimeoutExpired(["worker"], 2),
            ("", ""),
        ]
    )
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(process_utils.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        process_utils.os,
        "killpg",
        lambda pid, sent_signal: signals.append((pid, sent_signal)),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        process_utils.run_process_group(["worker"], timeout_seconds=1)

    assert signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert process.kill_calls == 1


def test_session_db_outer_timeout_exceeds_inner_cleanup_window() -> None:
    assert (
        process_utils.SESSION_DB_HELPER_TIMEOUT_SECONDS
        >= process_utils.SESSION_DB_INNER_TIMEOUT_SECONDS + 5
    )

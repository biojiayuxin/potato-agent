from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest


def _wait_for_path(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    pytest.fail(f"background process did not create {path}")


def _stop_session(registry, session) -> None:
    if not session.exited:
        registry.kill_process(session.id)
    if session._reader_thread is not None:
        session._reader_thread.join(timeout=5)


@pytest.mark.skipif(
    os.name == "nt" or not Path("/proc/self/cmdline").exists(),
    reason="requires Linux /proc",
)
def test_pipe_background_command_is_absent_from_process_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools import process_registry as module

    monkeypatch.setattr(module, "CHECKPOINT_PATH", tmp_path / "processes.json")
    registry = module.ProcessRegistry()
    sentinel = "background-argv-private-4e93b4"
    ready_path = tmp_path / "pipe-ready"
    command = (
        f"private_marker={sentinel}; printf '%s\\n' \"$private_marker\"; "
        f": > {ready_path.name}; while :; do sleep 1; done"
    )
    session = registry.spawn_local(command, cwd=str(tmp_path), use_pty=False)
    try:
        _wait_for_path(ready_path)
        cmdline = Path(f"/proc/{session.pid}/cmdline").read_bytes()
        assert sentinel.encode() not in cmdline
        assert sentinel not in "\0".join(str(arg) for arg in session.process.args)
        assert session.command == command
    finally:
        _stop_session(registry, session)


@pytest.mark.skipif(
    os.name == "nt" or not Path("/proc/self/cmdline").exists(),
    reason="requires Linux /proc",
)
def test_pty_background_command_is_absent_from_process_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("ptyprocess")
    from tools import process_registry as module

    monkeypatch.setattr(module, "CHECKPOINT_PATH", tmp_path / "processes.json")
    registry = module.ProcessRegistry()
    sentinel = "background-pty-argv-private-a61f0d"
    ready_path = tmp_path / "pty-ready"
    command = (
        f"private_marker={sentinel}; printf '%s\\n' \"$private_marker\"; "
        f": > {ready_path.name}; while :; do sleep 1; done"
    )
    session = registry.spawn_local(command, cwd=str(tmp_path), use_pty=True)
    try:
        assert session._pty is not None, "PTY launch unexpectedly fell back to pipe mode"
        _wait_for_path(ready_path)
        cmdline = Path(f"/proc/{session.pid}/cmdline").read_bytes()
        assert sentinel.encode() not in cmdline
        assert sentinel not in "\0".join(str(arg) for arg in session._pty.argv)
        assert session.command == command
    finally:
        _stop_session(registry, session)


def test_private_program_source_preserves_output_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools import process_registry as module

    monkeypatch.setattr(module, "CHECKPOINT_PATH", tmp_path / "processes.json")
    registry = module.ProcessRegistry()
    session = registry.spawn_local(
        "printf 'background-output-preserved'; exit 7",
        cwd=str(tmp_path),
        use_pty=False,
    )
    try:
        result = registry.wait(session.id, timeout=5)
        assert result["status"] == "exited"
        assert result["exit_code"] == 7
        assert "background-output-preserved" in result["output"]
    finally:
        _stop_session(registry, session)


@pytest.mark.skipif(os.name == "nt", reason="requires ptyprocess")
def test_pty_private_program_source_preserves_interactive_stdin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("ptyprocess")
    from tools import process_registry as module

    monkeypatch.setattr(module, "CHECKPOINT_PATH", tmp_path / "processes.json")
    registry = module.ProcessRegistry()
    session = registry.spawn_local(
        "read answer; printf 'interactive-answer=%s\\n' \"$answer\"",
        cwd=str(tmp_path),
        use_pty=True,
    )
    try:
        assert registry.submit_stdin(session.id, "private-input")["status"] == "ok"
        result = registry.wait(session.id, timeout=5)
        assert result["status"] == "exited"
        assert result["exit_code"] == 0
        assert "interactive-answer=private-input" in result["output"]
    finally:
        _stop_session(registry, session)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_process_checkpoint_is_always_owner_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools import process_registry as module

    checkpoint = tmp_path / "processes.json"
    checkpoint.write_text("[]", encoding="utf-8")
    checkpoint.chmod(0o644)
    monkeypatch.setattr(module, "CHECKPOINT_PATH", checkpoint)

    registry = module.ProcessRegistry()
    session = module.ProcessSession(
        id="proc_private_checkpoint",
        command="command containing private arguments",
        pid=os.getpid(),
        started_at=time.time(),
    )
    registry._running[session.id] = session

    old_umask = os.umask(0o022)
    try:
        registry._write_checkpoint()
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
    assert "command containing private arguments" in checkpoint.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_recovery_tightens_legacy_checkpoint_before_reading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools import process_registry as module

    checkpoint = tmp_path / "processes.json"
    checkpoint.write_text("[]", encoding="utf-8")
    checkpoint.chmod(0o644)
    monkeypatch.setattr(module, "CHECKPOINT_PATH", checkpoint)

    assert module.ProcessRegistry().recover_from_checkpoint() == 0
    assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="requires O_NOFOLLOW")
def test_recovery_refuses_checkpoint_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools import process_registry as module

    target = tmp_path / "target.json"
    target.write_text("[]", encoding="utf-8")
    target.chmod(0o644)
    checkpoint = tmp_path / "processes.json"
    checkpoint.symlink_to(target)
    monkeypatch.setattr(module, "CHECKPOINT_PATH", checkpoint)

    assert module.ProcessRegistry().recover_from_checkpoint() == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
    reason="requires POSIX FIFO nonblocking opens",
)
def test_recovery_opens_checkpoint_fifo_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tools import process_registry as module

    checkpoint = tmp_path / "processes.json"
    os.mkfifo(checkpoint)
    monkeypatch.setattr(module, "CHECKPOINT_PATH", checkpoint)

    real_open = os.open
    observed_nonblocking: list[bool] = []

    def recording_open(path, flags, *args, **kwargs):
        if Path(path) == checkpoint:
            observed_nonblocking.append(bool(flags & os.O_NONBLOCK))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", recording_open)

    assert module.ProcessRegistry().recover_from_checkpoint() == 0
    assert observed_nonblocking == [True]


def test_private_script_cleanup_is_idempotent(tmp_path: Path) -> None:
    from tools.process_registry import ProcessRegistry, ProcessSession

    script_dir = tmp_path / "private-script"
    script_dir.mkdir(mode=0o700)
    script_path = script_dir / "command.sh"
    script_path.write_text("private command", encoding="utf-8")
    session = ProcessSession(
        id="proc_private_script",
        command="private command",
        _script_path=str(script_path),
        _script_dir=str(script_dir),
    )

    ProcessRegistry._cleanup_session_script(session)
    ProcessRegistry._cleanup_session_script(session)

    assert not script_path.exists()
    assert not script_dir.exists()
    assert session._script_path is None
    assert session._script_dir is None

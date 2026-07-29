from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from interface import file_stream_worker, file_upload_worker
from interface.file_browser_policy import FileBrowserAccessError
from interface.file_stream_worker import (
    build_file_stream_worker_command,
    stream_file,
)
from interface.file_upload_worker import UploadTooLargeError, store_upload
from interface.request_limits import RequestBodyLimitMiddleware
from interface.subprocess_env import interface_subprocess_env
from interface.tui_gateway_bridge import TuiGatewayBridgeError, _validate_bridge_rpc


async def _request_with_body(
    *, headers: list[tuple[bytes, bytes]], chunks: list[bytes], limit: int
) -> list[dict]:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]

    async def receive():
        return messages.pop(0)

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def consume_app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(
        consume_app,
        limit_for_scope=lambda _scope: limit,
    )
    await middleware(
        {"type": "http", "path": "/test", "headers": headers},
        receive,
        send,
    )
    return sent


@pytest.mark.parametrize(
    ("headers", "chunks", "limit", "expected_status"),
    [
        ([], [b"123", b"456"], 5, 413),
        ([(b"content-length", b"-1")], [b""], 5, 400),
        (
            [(b"content-length", b"2"), (b"content-length", b"3")],
            [b"12"],
            5,
            400,
        ),
        ([(b"content-length", b"2")], [b"123"], 5, 400),
        ([(b"content-length", b"4")], [b"123"], 5, 400),
        ([(b"content-length", b"6")], [b""], 5, 413),
    ],
)
def test_request_body_limit_rejects_forged_or_oversize_bodies(
    headers, chunks, limit, expected_status
) -> None:
    sent = asyncio.run(
        _request_with_body(headers=headers, chunks=chunks, limit=limit)
    )
    assert sent[0]["status"] == expected_status


def test_interface_subprocess_env_drops_static_and_dynamic_secrets(monkeypatch) -> None:
    sentinels = {
        "CREDENTIALS_DIRECTORY": "/run/credentials/potato-interface.service",
        "INTERFACE_RESEND_API_KEY": "resend-secret",
        "INTERFACE_SESSION_SECRET": "interface-secret",
        "INTERFACE_SESSION_SECRET_FILE": "/private/session-secret",
        "INTERFACE_RESEND_API_KEY_FILE": "/private/resend-api-key",
        "OPENAI_API_KEY": "provider-secret",
        "GITHUB_TOKEN": "github-secret",
        "AUXILIARY_TEST_TOKEN": "aux-secret",
        "GATEWAY_RELAY_TEST_SECRET": "relay-secret",
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/google.json",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)

    result = interface_subprocess_env(
        {
            "HOME": "/home/worker",
            "OPENAI_API_KEY": "override-secret",
            "AUXILIARY_EXTRA_TOKEN": "override-aux",
        }
    )
    assert result["HOME"] == "/home/worker"
    for name in (*sentinels, "AUXILIARY_EXTRA_TOKEN"):
        assert name not in result


def test_file_stream_uses_open_fd_when_path_is_replaced(monkeypatch, tmp_path: Path) -> None:
    requested = tmp_path / "result.txt"
    replacement = tmp_path / "replacement.txt"
    requested.write_bytes(b"trusted-open-fd")
    replacement.write_bytes(b"replacement-path")
    output = io.BytesIO()
    monkeypatch.setattr(
        "interface.file_stream_worker.sys.stdout",
        types.SimpleNamespace(buffer=output),
    )
    original_fstat = os.fstat
    swapped = False

    def swapping_fstat(fd: int):
        nonlocal swapped
        result = original_fstat(fd)
        if not swapped:
            os.replace(replacement, requested)
            swapped = True
        return result

    monkeypatch.setattr("interface.file_stream_worker.os.fstat", swapping_fstat)
    stream_file(
        home=tmp_path,
        browser_root=tmp_path,
        requested_path=requested,
        mode="home_only",
        public_data_root=tmp_path / "public",
    )

    header, body = output.getvalue().split(b"\n", 1)
    assert json.loads(header)["size"] == len(b"trusted-open-fd")
    assert body == b"trusted-open-fd"
    assert requested.read_bytes() == b"replacement-path"


def test_file_stream_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    link = tmp_path / "link.txt"
    target.write_text("secret", encoding="utf-8")
    link.symlink_to(target)

    with pytest.raises((OSError, FileBrowserAccessError)):
        stream_file(
            home=tmp_path,
            browser_root=tmp_path,
            requested_path=link,
            mode="home_only",
            public_data_root=tmp_path / "public",
        )


def test_file_stream_rejects_non_regular_file(tmp_path: Path) -> None:
    fifo = tmp_path / "results.fifo"
    os.mkfifo(fifo)

    with pytest.raises(file_stream_worker.FileStreamAccessError):
        stream_file(
            home=tmp_path,
            browser_root=tmp_path,
            requested_path=fifo,
            mode="home_only",
            public_data_root=tmp_path / "public",
        )


def test_file_stream_command_does_not_require_access_to_worker_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    requested = home / "result.txt"
    requested.write_bytes(b"isolated-worker")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"denied")
    private_source_dir = tmp_path / "private-source"
    private_source_dir.mkdir()
    private_worker = private_source_dir / "file_stream_worker.py"
    private_worker.write_text(
        Path(file_stream_worker.__file__).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(file_stream_worker, "__file__", str(private_worker))

    command = build_file_stream_worker_command(
        linux_user="unused",
        home=home,
        browser_root=home,
        requested_path=requested,
        mode="home_only",
        public_data_root=tmp_path / "public",
        python_bin=sys.executable,
        use_runuser=False,
    )
    assert "-I" in command
    assert "-c" in command
    assert not any(str(private_worker) in argument for argument in command)
    denied_command = build_file_stream_worker_command(
        linux_user="unused",
        home=home,
        browser_root=home,
        requested_path=outside,
        mode="home_only",
        public_data_root=tmp_path / "public",
        python_bin=sys.executable,
        use_runuser=False,
    )

    private_source_dir.chmod(0)
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=5)
        denied = subprocess.run(
            denied_command,
            capture_output=True,
            check=False,
            timeout=5,
        )
    finally:
        private_source_dir.chmod(0o700)

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    header, body = result.stdout.split(b"\n", 1)
    assert json.loads(header)["protocol"] == "file-stream-v2"
    assert body == b"isolated-worker"
    assert denied.returncode == 1
    assert denied.stdout == b""
    assert denied.stderr == b"file stream request denied\n"


def test_upload_worker_enforces_limit_and_cleans_temporary_file(tmp_path: Path) -> None:
    with pytest.raises(UploadTooLargeError):
        store_upload(
            source=io.BytesIO(b"12345"),
            home=tmp_path,
            mapping_username="alice",
            filename="data.bin",
            upload_dir_name="uploads",
            max_bytes=4,
        )
    upload_root = tmp_path / "uploads" / "alice"
    assert upload_root.is_dir()
    assert list(upload_root.iterdir()) == []


def test_upload_command_does_not_require_access_to_worker_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    private_source_dir = tmp_path / "private-source"
    private_source_dir.mkdir()
    private_worker = private_source_dir / "file_upload_worker.py"
    private_worker.write_text(
        Path(file_upload_worker.__file__).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(file_upload_worker, "__file__", str(private_worker))

    command = file_upload_worker.build_file_upload_worker_command(
        linux_user="unused",
        home=home,
        mapping_username="alice",
        filename="notes.txt",
        upload_dir_name="uploads",
        max_bytes=1024,
        python_bin=sys.executable,
        use_runuser=False,
    )
    assert "-I" in command
    assert "-c" in command
    assert not any(str(private_worker) in argument for argument in command)

    private_source_dir.chmod(0)
    try:
        result = subprocess.run(
            command,
            input=b"isolated-upload",
            capture_output=True,
            check=False,
            timeout=5,
        )
    finally:
        private_source_dir.chmod(0o700)

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["name"] == "notes.txt"
    assert payload["size"] == len(b"isolated-upload")
    assert Path(payload["path"]).read_bytes() == b"isolated-upload"


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("shell.exec", {}),
        ("config.set", {}),
        ("command.dispatch", {"name": "model"}),
        ("session.create", {"nested": [{"PROFILE_NAME": "escape"}]}),
    ],
)
def test_bridge_rejects_unapproved_methods_and_profile_parameters(method, params) -> None:
    with pytest.raises(TuiGatewayBridgeError):
        _validate_bridge_rpc(method, params)


def test_bridge_allows_only_plan_command_dispatch() -> None:
    _validate_bridge_rpc("command.dispatch", {"name": "plan"})


def test_interface_launcher_fixes_uvicorn_websocket_limit(monkeypatch) -> None:
    from interface import serve

    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    serve.main(["--host", "127.0.0.1", "--port", "3100"])

    assert calls == [
        (
            ("interface.app:app",),
            {
                "host": "127.0.0.1",
                "port": 3100,
                "ws_max_size": 1024 * 1024,
            },
        )
    ]

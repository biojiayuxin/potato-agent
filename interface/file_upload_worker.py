#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

HARD_MAX_UPLOAD_BYTES = 200 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class UploadTooLargeError(RuntimeError):
    pass


def build_file_upload_worker_command(
    *,
    linux_user: str,
    home: Path,
    mapping_username: str,
    filename: str,
    upload_dir_name: str,
    max_bytes: int,
    python_bin: str | None = None,
    use_runuser: bool = True,
) -> list[str]:
    command = [
        "env",
        "-i",
        f"HOME={home}",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "PYTHONUNBUFFERED=1",
        python_bin or sys.executable,
        str(Path(__file__).resolve()),
        "--home",
        str(home),
        "--mapping-username",
        mapping_username,
        "--filename",
        filename,
        "--upload-dir-name",
        upload_dir_name,
        "--max-bytes",
        str(min(max(int(max_bytes), 0), HARD_MAX_UPLOAD_BYTES)),
    ]
    if use_runuser:
        return ["runuser", "-u", linux_user, "--", *command]
    return command


def _sanitize_filename(filename: str) -> str:
    base = Path(filename or "upload.bin").name
    return FILENAME_SANITIZE_RE.sub("_", base).strip("._") or "upload.bin"


def _upload_root(*, home: Path, upload_dir_name: str, mapping_username: str) -> Path:
    configured = Path(upload_dir_name)
    root = (
        configured / mapping_username
        if configured.is_absolute()
        else home / configured / mapping_username
    )
    resolved = root.resolve()
    if not configured.is_absolute():
        try:
            resolved.relative_to(home.resolve())
        except ValueError as exc:
            raise RuntimeError("invalid upload directory") from exc
    return resolved


def store_upload(
    *,
    source: BinaryIO,
    home: Path,
    mapping_username: str,
    filename: str,
    upload_dir_name: str,
    max_bytes: int,
) -> dict[str, object]:
    enforced_limit = min(max(int(max_bytes), 0), HARD_MAX_UPLOAD_BYTES)
    root = _upload_root(
        home=home,
        upload_dir_name=upload_dir_name,
        mapping_username=mapping_username,
    )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)

    safe_name = _sanitize_filename(filename)
    stem = f"{uuid.uuid4().hex}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_name}"
    destination = root / stem
    temporary = root / f".{stem}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(temporary, flags, 0o600)
    total = 0
    try:
        while True:
            chunk = source.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > enforced_limit:
                raise UploadTooLargeError("upload exceeds size limit")
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise

    return {"path": str(destination), "name": safe_name, "size": total}


def run_upload_command(
    command: list[str],
    source: BinaryIO,
    *,
    max_bytes: int,
) -> dict[str, object]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONUNBUFFERED": "1",
        },
    )
    assert process.stdin is not None
    total = 0
    too_large = False
    try:
        while True:
            chunk = source.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            process.stdin.write(chunk)
            if total > max_bytes:
                too_large = True
                break
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate()
    except BaseException:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    if too_large:
        raise UploadTooLargeError("upload exceeds size limit")
    if process.returncode != 0:
        raise RuntimeError("file upload failed")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid file upload response") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError("file upload failed")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--mapping-username", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--upload-dir-name", required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = store_upload(
            source=sys.stdin.buffer,
            home=Path(args.home),
            mapping_username=str(args.mapping_username),
            filename=str(args.filename),
            upload_dir_name=str(args.upload_dir_name),
            max_bytes=int(args.max_bytes),
        )
    except (OSError, RuntimeError):
        print("file upload request denied", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **payload}, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.file_browser_policy import (  # noqa: E402
    FileBrowserAccessError,
    authorize_file_browser_path,
)


COPY_CHUNK_BYTES = 1024 * 1024


def build_file_stream_worker_command(
    *,
    linux_user: str,
    home: Path,
    browser_root: Path,
    requested_path: Path,
    mode: str,
    public_data_root: Path,
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
        "--browser-root",
        str(browser_root),
        "--path",
        str(requested_path),
        "--mode",
        mode,
        "--public-data-root",
        str(public_data_root),
    ]
    if use_runuser:
        return ["runuser", "-u", linux_user, "--", *command]
    return command


def _raise_if_outside_browser_root(
    actual_path: Path,
    *,
    browser_root: Path,
    mode: str,
) -> None:
    if mode != "user_readable":
        return
    try:
        actual_path.relative_to(browser_root.resolve())
    except ValueError as exc:
        raise FileBrowserAccessError(
            "Opening paths outside the selected browser root is disabled"
        ) from exc


def stream_file(
    *,
    home: Path,
    browser_root: Path,
    requested_path: Path,
    mode: str,
    public_data_root: Path,
) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(requested_path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise FileBrowserAccessError("Requested path is not a regular file")

        fd_path = Path(f"/proc/self/fd/{fd}")
        actual_path = Path(os.path.realpath(fd_path))
        authorize_file_browser_path(
            requested_path,
            home=home,
            mode=mode,
            public_data_root=public_data_root,
        )
        authorize_file_browser_path(
            actual_path,
            home=home,
            mode=mode,
            public_data_root=public_data_root,
        )
        _raise_if_outside_browser_root(
            actual_path,
            browser_root=browser_root,
            mode=mode,
        )

        metadata = {
            "protocol": "file-stream-v2",
            "filename": requested_path.name or actual_path.name or "download.bin",
            "size": int(file_stat.st_size),
            "modified": int(file_stat.st_mtime),
        }
        header = json.dumps(
            metadata,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        sys.stdout.buffer.write(header + b"\n")
        sys.stdout.buffer.flush()

        while True:
            chunk = os.read(fd, COPY_CHUNK_BYTES)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    finally:
        os.close(fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--browser-root", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--public-data-root", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        stream_file(
            home=Path(args.home),
            browser_root=Path(args.browser_root),
            requested_path=Path(args.path),
            mode=str(args.mode),
            public_data_root=Path(args.public_data_root),
        )
    except (FileBrowserAccessError, OSError, RuntimeError):
        print("file stream request denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


COPY_CHUNK_BYTES = 1024 * 1024


class FileStreamAccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileStreamPolicy:
    allowed_roots: tuple[Path, ...]
    sensitive_file_basenames: frozenset[str]
    sensitive_directory_names: frozenset[str]


def _load_worker_source() -> str:
    return Path(__file__).read_text(encoding="utf-8")


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
    from interface.file_browser_policy import build_file_stream_policy

    policy_json = json.dumps(
        build_file_stream_policy(
            home=home,
            browser_root=browser_root,
            mode=mode,
            public_data_root=public_data_root,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    command = [
        "env",
        "-i",
        f"HOME={home}",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "PYTHONUNBUFFERED=1",
        python_bin or sys.executable,
        "-I",
        "-c",
        _load_worker_source(),
        "--path",
        str(requested_path),
        "--policy-json",
        policy_json,
    ]
    if use_runuser:
        return ["runuser", "-u", linux_user, "--", *command]
    return command


def _policy_from_payload(payload: object) -> FileStreamPolicy:
    if not isinstance(payload, dict):
        raise FileStreamAccessError("Invalid file stream policy")

    raw_roots = payload.get("allowed_roots")
    raw_files = payload.get("sensitive_file_basenames")
    raw_directories = payload.get("sensitive_directory_names")
    if (
        not isinstance(raw_roots, list)
        or not raw_roots
        or not all(
            isinstance(item, str) and item.startswith("/") for item in raw_roots
        )
        or not isinstance(raw_files, list)
        or not all(isinstance(item, str) for item in raw_files)
        or not isinstance(raw_directories, list)
        or not all(isinstance(item, str) for item in raw_directories)
    ):
        raise FileStreamAccessError("Invalid file stream policy")
    return FileStreamPolicy(
        allowed_roots=tuple(Path(item).resolve() for item in raw_roots),
        sensitive_file_basenames=frozenset(item.casefold() for item in raw_files),
        sensitive_directory_names=frozenset(
            item.casefold() for item in raw_directories
        ),
    )


def _parse_policy(raw: str) -> FileStreamPolicy:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FileStreamAccessError("Invalid file stream policy") from exc
    return _policy_from_payload(payload)


def _build_policy_for_paths(
    *,
    home: Path,
    browser_root: Path,
    mode: str,
    public_data_root: Path,
) -> FileStreamPolicy:
    from interface.file_browser_policy import build_file_stream_policy

    return _policy_from_payload(
        build_file_stream_policy(
            home=home,
            browser_root=browser_root,
            mode=mode,
            public_data_root=public_data_root,
        )
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _authorize_path(path: Path, *, policy: FileStreamPolicy) -> Path:
    candidates = [path.expanduser()]
    resolved = path.expanduser().resolve()
    if resolved != candidates[0]:
        candidates.append(resolved)
    for candidate in candidates:
        folded_parts = tuple(part.casefold() for part in candidate.parts)
        if any(part in policy.sensitive_directory_names for part in folded_parts):
            raise FileStreamAccessError("Sensitive path denied")
        basename = candidate.name.casefold()
        if (
            basename == ".env"
            or basename.startswith(".env.")
            or basename in policy.sensitive_file_basenames
        ):
            raise FileStreamAccessError("Sensitive path denied")
    if not any(_is_within(resolved, root) for root in policy.allowed_roots):
        raise FileStreamAccessError("Path outside allowed roots")
    return resolved


def stream_file(
    *,
    requested_path: Path,
    policy: FileStreamPolicy | None = None,
    home: Path | None = None,
    browser_root: Path | None = None,
    mode: str | None = None,
    public_data_root: Path | None = None,
) -> None:
    if policy is None:
        if (
            home is None
            or browser_root is None
            or mode is None
            or public_data_root is None
        ):
            raise FileStreamAccessError("File stream policy is required")
        policy = _build_policy_for_paths(
            home=home,
            browser_root=browser_root,
            mode=mode,
            public_data_root=public_data_root,
        )

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(requested_path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise FileStreamAccessError("Requested path is not a regular file")

        fd_path = Path(f"/proc/self/fd/{fd}")
        actual_path = Path(os.path.realpath(fd_path))
        _authorize_path(requested_path, policy=policy)
        _authorize_path(actual_path, policy=policy)

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
    parser.add_argument("--path", required=True)
    parser.add_argument("--policy-json", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        stream_file(
            requested_path=Path(args.path),
            policy=_parse_policy(args.policy_json),
        )
    except (FileStreamAccessError, OSError, RuntimeError):
        print("file stream request denied", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from interface.subprocess_env import interface_subprocess_env


MAX_USER_PRIVATE_FILE_BYTES = 4 * 1024 * 1024
USER_FILE_WORKER_TIMEOUT_SECONDS = 15


class UserPrivateFileError(RuntimeError):
    pass


_USER_FILE_WORKER = r"""
import json
import os
import shutil
import stat
import sys
import uuid
from pathlib import Path

MAX_BYTES = int(sys.argv[2])

def fail(message, code=1):
    print(message, file=sys.stderr)
    raise SystemExit(code)

def open_parent(path):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.parent, flags)
    except OSError:
        fail("unsafe parent directory")
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        os.close(descriptor)
        fail("unsafe parent directory")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.close(descriptor)
        fail("unsafe parent directory permissions")
    return descriptor

def remove_entry(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()

action = sys.argv[1]
if action == "prepare":
    try:
        paths = json.loads(sys.stdin.buffer.read(MAX_BYTES + 1))
    except Exception:
        fail("invalid directory request")
    if not isinstance(paths, list):
        fail("invalid directory request")
    for raw_path in paths:
        path = Path(str(raw_path))
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, 0o700)
            info = path.lstat()
        except OSError:
            fail("unable to prepare private directory")
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            fail("unsafe private directory")
    raise SystemExit(0)

if action == "replace-tree":
    source = Path(sys.argv[3])
    destination = Path(sys.argv[4])
    parent_fd = open_parent(destination)
    temp_name = f".{destination.name}.{uuid.uuid4().hex}.tmp"
    temp_path = destination.parent / temp_name
    backup_name = None
    installed = False
    try:
        shutil.copytree(source, temp_path, symlinks=True)
        for root, directories, files in os.walk(temp_path, followlinks=False):
            os.chmod(root, 0o700)
            for name in directories:
                candidate = Path(root) / name
                if not candidate.is_symlink():
                    os.chmod(candidate, 0o700)
            for name in files:
                candidate = Path(root) / name
                if not candidate.is_symlink():
                    executable = bool(candidate.stat().st_mode & 0o111)
                    os.chmod(candidate, 0o700 if executable else 0o600)
        try:
            destination.lstat()
        except FileNotFoundError:
            pass
        else:
            backup_name = f".{destination.name}.{uuid.uuid4().hex}.bak"
            os.replace(
                destination.name,
                backup_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        try:
            os.replace(
                temp_name,
                destination.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            installed = True
        except OSError:
            if backup_name is not None:
                os.replace(
                    backup_name,
                    destination.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                backup_name = None
            raise
        os.fsync(parent_fd)
    except OSError:
        fail("unable to replace private tree")
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)
        if installed and backup_name is not None:
            try:
                remove_entry(destination.parent / backup_name)
            except OSError:
                pass
        os.close(parent_fd)
    raise SystemExit(0)

path = Path(sys.argv[3])
if action == "read":
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise SystemExit(3)
    except OSError:
        fail("unable to open private file")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            fail("unsafe private file")
        if info.st_size > MAX_BYTES:
            fail("private file exceeds size limit")
        chunks = []
        remaining = MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    if len(data) > MAX_BYTES:
        fail("private file exceeds size limit")
    sys.stdout.buffer.write(data)
    raise SystemExit(0)

if action == "write":
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        fail("private file exceeds size limit")
    parent_fd = open_parent(path)
    temp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError:
        fail("unable to write private file")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except OSError:
            pass
        os.close(parent_fd)
    raise SystemExit(0)

fail("unsupported private file action")
"""


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def validate_user_runtime_paths(target: Any, password_entry: Any) -> None:
    home_dir = _resolved(Path(target.home_dir))
    passwd_home_raw = str(getattr(password_entry, "pw_dir", "") or "").strip()
    if passwd_home_raw and home_dir != _resolved(Path(passwd_home_raw)):
        raise UserPrivateFileError(
            "Mapped home directory does not match the Linux account home."
        )

    hermes_home = _resolved(Path(target.hermes_home))
    try:
        hermes_home.relative_to(home_dir)
    except ValueError as exc:
        raise UserPrivateFileError(
            "Hermes home must remain inside the Linux account home."
        ) from exc

    workdir = _resolved(Path(target.workdir))
    try:
        workdir.relative_to(home_dir)
    except ValueError as exc:
        raise UserPrivateFileError(
            "Working directory must remain inside the Linux account home."
        ) from exc


def validate_user_private_path(target: Any, path: Path) -> None:
    hermes_home = _resolved(Path(target.hermes_home))
    try:
        _resolved(path).relative_to(hermes_home)
    except ValueError as exc:
        raise UserPrivateFileError(
            "Managed user file must remain inside the Hermes home."
        ) from exc


def _worker_command(linux_user: str, action: str, *arguments: str) -> list[str]:
    return [
        "runuser",
        "-u",
        linux_user,
        "--",
        "env",
        "-i",
        "PATH=/usr/bin:/bin",
        sys.executable,
        "-I",
        "-c",
        _USER_FILE_WORKER,
        action,
        str(MAX_USER_PRIVATE_FILE_BYTES),
        *arguments,
    ]


def _run_worker(
    linux_user: str,
    action: str,
    *arguments: str,
    input_bytes: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            _worker_command(linux_user, action, *arguments),
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=USER_FILE_WORKER_TIMEOUT_SECONDS,
            env=interface_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise UserPrivateFileError("Private user file operation timed out.") from exc


def _direct_prepare(paths: list[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise UserPrivateFileError("Private user directory must not be a symlink.")
        path.chmod(0o700)


def prepare_user_runtime_directories(target: Any, password_entry: Any) -> None:
    validate_user_runtime_paths(target, password_entry)
    paths = [
        Path(target.home_dir),
        Path(target.workdir),
        Path(target.hermes_home),
        Path(target.hermes_home) / "home",
        Path(target.hermes_home) / "tmp",
    ]
    if os.geteuid() != 0:
        _direct_prepare(paths)
        return

    result = _run_worker(
        str(target.linux_user),
        "prepare",
        input_bytes=json.dumps([str(path) for path in paths]).encode("utf-8"),
    )
    if result.returncode != 0:
        raise UserPrivateFileError("Unable to prepare private user directories.")


def prepare_user_private_directory(
    target: Any, password_entry: Any, path: Path
) -> None:
    validate_user_private_path(target, path)
    if os.geteuid() != 0:
        _direct_prepare([path])
        return
    result = _run_worker(
        str(target.linux_user),
        "prepare",
        input_bytes=json.dumps([str(path)]).encode("utf-8"),
    )
    if result.returncode != 0:
        raise UserPrivateFileError("Unable to prepare private user directory.")


def _direct_read(path: Path) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UserPrivateFileError("Unable to open private user file.") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise UserPrivateFileError("Unsafe private user file.")
        if info.st_size > MAX_USER_PRIVATE_FILE_BYTES:
            raise UserPrivateFileError("Private user file exceeds the size limit.")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            data = handle.read(MAX_USER_PRIVATE_FILE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > MAX_USER_PRIVATE_FILE_BYTES:
        raise UserPrivateFileError("Private user file exceeds the size limit.")
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise UserPrivateFileError("Private user file must be valid UTF-8.") from exc


def read_user_private_text(target: Any, path: Path) -> str | None:
    validate_user_private_path(target, path)
    if os.geteuid() != 0:
        return _direct_read(path)

    result = _run_worker(str(target.linux_user), "read", str(path))
    if result.returncode == 3:
        return None
    if result.returncode != 0:
        raise UserPrivateFileError("Unable to read private user file.")
    if len(result.stdout) > MAX_USER_PRIVATE_FILE_BYTES:
        raise UserPrivateFileError("Private user file exceeds the size limit.")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise UserPrivateFileError("Private user file must be valid UTF-8.") from exc


def _direct_write(path: Path, data: bytes) -> None:
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_fd = os.open(path.parent, parent_flags)
    except OSError as exc:
        raise UserPrivateFileError("Unable to open private user directory.") from exc
    temp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = -1
    try:
        parent_info = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.geteuid():
            raise UserPrivateFileError("Unsafe private user directory.")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as exc:
        raise UserPrivateFileError("Unable to write private user file.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except OSError:
            pass
        os.close(parent_fd)


def write_user_private_text(target: Any, path: Path, body: str) -> None:
    validate_user_private_path(target, path)
    data = body.encode("utf-8")
    if len(data) > MAX_USER_PRIVATE_FILE_BYTES:
        raise UserPrivateFileError("Private user file exceeds the size limit.")
    if os.geteuid() != 0:
        _direct_write(path, data)
        return

    result = _run_worker(
        str(target.linux_user),
        "write",
        str(path),
        input_bytes=data,
    )
    if result.returncode != 0:
        raise UserPrivateFileError("Unable to write private user file.")


def repair_user_private_file(
    target: Any, password_entry: Any, path: Path
) -> bool:
    validate_user_private_path(target, path)
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_fd = os.open(path.parent, parent_flags)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise UserPrivateFileError("Unable to open private user directory.") from exc

    descriptor = -1
    try:
        parent_info = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != int(password_entry.pw_uid)
        ):
            raise UserPrivateFileError("Unsafe private user directory.")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            return False
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UserPrivateFileError("Unsafe private user file.")
        if info.st_uid not in {0, int(password_entry.pw_uid)}:
            raise UserPrivateFileError("Private user file has an unexpected owner.")
        os.fchown(descriptor, int(password_entry.pw_uid), int(password_entry.pw_gid))
        os.fchmod(descriptor, 0o600)
        return True
    except OSError as exc:
        raise UserPrivateFileError("Unable to repair private user file.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _harden_staged_tree(path: Path, *, gid: int) -> None:
    os.chown(path, 0, gid, follow_symlinks=False)
    os.chmod(path, 0o750)
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        os.chown(root_path, 0, gid, follow_symlinks=False)
        os.chmod(root_path, 0o750)
        for name in directories:
            candidate = root_path / name
            os.chown(candidate, 0, gid, follow_symlinks=False)
            if not candidate.is_symlink():
                os.chmod(candidate, 0o750)
        for name in files:
            candidate = root_path / name
            os.chown(candidate, 0, gid, follow_symlinks=False)
            if not candidate.is_symlink():
                executable = bool(candidate.stat().st_mode & 0o111)
                os.chmod(candidate, 0o750 if executable else 0o640)


def _direct_replace_tree(source: Path, destination: Path) -> None:
    temp_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copytree(source, temp_path, symlinks=True)
        for root, directories, files in os.walk(temp_path, followlinks=False):
            os.chmod(root, 0o700)
            for name in directories:
                candidate = Path(root) / name
                if not candidate.is_symlink():
                    os.chmod(candidate, 0o700)
            for name in files:
                candidate = Path(root) / name
                if not candidate.is_symlink():
                    executable = bool(candidate.stat().st_mode & 0o111)
                    os.chmod(candidate, 0o700 if executable else 0o600)
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        elif destination.exists() or destination.is_symlink():
            destination.unlink()
        os.replace(temp_path, destination)
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def replace_user_private_tree(
    target: Any,
    password_entry: Any,
    *,
    source: Path,
    destination: Path,
) -> None:
    validate_user_private_path(target, destination)
    if not source.is_dir() or source.is_symlink():
        raise UserPrivateFileError("Managed source tree must be a real directory.")
    prepare_user_private_directory(target, password_entry, destination.parent)
    if os.geteuid() != 0:
        _direct_replace_tree(source, destination)
        return

    staging_root = Path(tempfile.mkdtemp(prefix="potato-user-tree-"))
    staged_source = staging_root / "payload"
    try:
        shutil.copytree(source, staged_source, symlinks=True)
        _harden_staged_tree(staging_root, gid=int(password_entry.pw_gid))
        result = _run_worker(
            str(target.linux_user),
            "replace-tree",
            str(staged_source),
            str(destination),
        )
        if result.returncode != 0:
            raise UserPrivateFileError("Unable to install managed user tree.")
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

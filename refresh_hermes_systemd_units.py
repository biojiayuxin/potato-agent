#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import ctypes
import difflib
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from interface.hermes_profile import load_runtime_profile
from interface.hermes_service import build_systemd_unit
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore


DEFAULT_UNIT_DIR = Path("/etc/systemd/system")
DEFAULT_BACKUP_ROOT = Path("/var/backups/potato-agent/hermes-units")
DEFAULT_LOCK_PATH = Path("/run/potato-agent/unit-refresh/hermes.lock")
DEFAULT_HERMES_LITE_CURRENT = Path("/opt/potato-hermes-lite/current")
DEFAULT_HERMES_LITE_RELEASES = Path("/opt/potato-hermes-lite/releases")
_UNIT_NAME_RE = re.compile(r"^hermes-[A-Za-z0-9_.@-]+\.service$")
_RENAME_EXCHANGE = 2
_AT_FDCWD = -100


class UnitRefreshError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileSnapshot:
    content: bytes
    version: tuple[int, ...]


@dataclass(frozen=True)
class UnitCandidate:
    target: HermesTarget
    path: Path
    desired: bytes
    original: FileSnapshot | None

    @property
    def changed(self) -> bool:
        return self.original is None or self.original.content != self.desired


@dataclass(frozen=True)
class AppliedSwap:
    candidate: UnitCandidate
    displaced_path: Path
    installed: FileSnapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check or atomically refresh Hermes systemd units without touching "
            "user config, state, sessions, or skills."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Write changed units.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Check every mapped user.")
    scope.add_argument("--user", help="Check one mapping username.")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_PATH)
    parser.add_argument("--unit-dir", type=Path, default=DEFAULT_UNIT_DIR)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument(
        "--expect-count",
        type=int,
        help="Fail unless the mapping contains exactly this many targets.",
    )
    parser.add_argument(
        "--require-existing-set",
        action="store_true",
        help="Fail unless mapped and existing hermes-*.service names match exactly.",
    )
    return parser


def _file_version(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_unit_snapshot(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int | None = None,
    expected_mode: int | None = None,
) -> FileSnapshot | None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise UnitRefreshError(f"unit must be a regular non-symlink file: {path}")
    if file_stat.st_nlink != 1:
        raise UnitRefreshError(
            f"unit must have exactly one hard link, got {file_stat.st_nlink}: {path}"
        )
    if file_stat.st_uid != expected_uid:
        raise UnitRefreshError(
            f"unit owner must be uid {expected_uid}, got {file_stat.st_uid}: {path}"
        )
    if expected_gid is not None and file_stat.st_gid != expected_gid:
        raise UnitRefreshError(
            f"unit group must be gid {expected_gid}, got {file_stat.st_gid}: {path}"
        )
    actual_mode = stat.S_IMODE(file_stat.st_mode)
    if expected_mode is not None and actual_mode != expected_mode:
        raise UnitRefreshError(
            f"unit mode must be {expected_mode:04o}, got {actual_mode:04o}: {path}"
        )
    if actual_mode & 0o022:
        raise UnitRefreshError(f"unit must not be group/world writable: {path}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened_stat = os.fstat(fd)
        if _file_version(opened_stat) != _file_version(file_stat):
            raise UnitRefreshError(f"unit changed while opening: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_stat = os.fstat(fd)
        if _file_version(after_stat) != _file_version(file_stat):
            raise UnitRefreshError(f"unit changed while reading: {path}")
    finally:
        os.close(fd)
    return FileSnapshot(content=b"".join(chunks), version=_file_version(file_stat))


def _validate_unit_name(service_name: str) -> str:
    rendered = str(service_name or "").strip()
    if _UNIT_NAME_RE.fullmatch(rendered) is None or Path(rendered).name != rendered:
        raise UnitRefreshError(f"unsafe Hermes systemd service name: {service_name!r}")
    return rendered


def _validate_unit_directory(path: Path, *, expected_uid: int) -> None:
    try:
        directory_stat = path.lstat()
    except FileNotFoundError as exc:
        raise UnitRefreshError(f"systemd unit directory does not exist: {path}") from exc
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
        raise UnitRefreshError(
            f"systemd unit directory must be a non-symlink directory: {path}"
        )
    if directory_stat.st_uid != expected_uid:
        raise UnitRefreshError(
            f"systemd unit directory owner must be uid {expected_uid}, got "
            f"{directory_stat.st_uid}: {path}"
        )
    if stat.S_IMODE(directory_stat.st_mode) & 0o022:
        raise UnitRefreshError(
            f"systemd unit directory must not be group/world writable: {path}"
        )


def _validate_trusted_ancestors(
    path: Path,
    *,
    label: str,
    expected_uid: int = 0,
) -> None:
    current = path.parent
    while True:
        current_stat = current.lstat()
        if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISDIR(current_stat.st_mode):
            raise UnitRefreshError(f"{label} ancestor must be a directory: {current}")
        if current_stat.st_uid != expected_uid:
            raise UnitRefreshError(
                f"{label} ancestor must be owned by uid {expected_uid}: {current}"
            )
        if stat.S_IMODE(current_stat.st_mode) & 0o022:
            raise UnitRefreshError(
                f"{label} ancestor must not be group/world writable: {current}"
            )
        if current == current.parent:
            return
        current = current.parent


def _validate_trusted_regular_file(
    path: Path,
    *,
    label: str,
    expected_uid: int = 0,
    validate_ancestors: bool = True,
) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise UnitRefreshError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise UnitRefreshError(f"{label} must be a regular non-symlink file: {path}")
    if file_stat.st_uid != expected_uid:
        raise UnitRefreshError(f"{label} must be owned by uid {expected_uid}: {path}")
    if file_stat.st_nlink != 1:
        raise UnitRefreshError(f"{label} must have exactly one hard link: {path}")
    if stat.S_IMODE(file_stat.st_mode) & 0o022:
        raise UnitRefreshError(f"{label} must not be group/world writable: {path}")
    if validate_ancestors:
        _validate_trusted_ancestors(
            path,
            label=label,
            expected_uid=expected_uid,
        )
    return file_stat


def _validate_trusted_runtime_profile(
    path: Path,
    *,
    current_path: Path = DEFAULT_HERMES_LITE_CURRENT,
    releases_dir: Path = DEFAULT_HERMES_LITE_RELEASES,
    expected_uid: int = 0,
    validate_ancestors: bool = True,
) -> Path:
    """Validate a profile, including the single managed Lite activation link."""
    path = Path(os.path.abspath(path))
    current_path = Path(os.path.abspath(current_path))
    releases_dir = Path(os.path.abspath(releases_dir))
    if path != current_path and current_path not in path.parents:
        _validate_trusted_regular_file(
            path,
            label="runtime profile",
            expected_uid=expected_uid,
            validate_ancestors=validate_ancestors,
        )
        return path

    _validate_unit_directory(releases_dir, expected_uid=expected_uid)
    if validate_ancestors:
        _validate_trusted_ancestors(
            releases_dir,
            label="Hermes Lite releases directory",
            expected_uid=expected_uid,
        )
        _validate_trusted_ancestors(
            current_path,
            label="Hermes Lite current link",
            expected_uid=expected_uid,
        )
    try:
        current_stat = current_path.lstat()
        resolved_current = current_path.resolve(strict=True)
        resolved_releases = releases_dir.resolve(strict=True)
        resolved_profile = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise UnitRefreshError(f"runtime profile does not exist: {path}") from exc
    if not stat.S_ISLNK(current_stat.st_mode):
        raise UnitRefreshError(
            f"Hermes Lite current activation must be a symlink: {current_path}"
        )
    if current_stat.st_uid != expected_uid:
        raise UnitRefreshError(
            f"Hermes Lite current symlink must be owned by uid {expected_uid}: "
            f"{current_path}"
        )
    if resolved_current.parent != resolved_releases:
        raise UnitRefreshError(
            "Hermes Lite current symlink must resolve to a direct child of "
            f"{resolved_releases}: {resolved_current}"
        )
    try:
        relative = path.relative_to(current_path)
    except ValueError as exc:
        raise UnitRefreshError(f"runtime profile escaped current release: {path}") from exc
    expected_profile = resolved_current / relative
    if resolved_profile != expected_profile:
        raise UnitRefreshError(
            f"runtime profile escaped current release: {resolved_profile}"
        )
    _validate_trusted_regular_file(
        resolved_profile,
        label="runtime profile target",
        expected_uid=expected_uid,
        validate_ancestors=validate_ancestors,
    )
    return resolved_profile


def _mode_allows_account(
    file_stat: os.stat_result,
    *,
    uid: int,
    gids: set[int],
    required: int,
) -> bool:
    mode = stat.S_IMODE(file_stat.st_mode)
    if file_stat.st_uid == uid:
        granted = (mode >> 6) & 0o7
    elif file_stat.st_gid in gids:
        granted = (mode >> 3) & 0o7
    else:
        granted = mode & 0o7
    return granted & required == required


def _require_account_path_access(
    path: Path,
    *,
    account: pwd.struct_passwd,
    gids: set[int],
    required: int,
    label: str,
) -> None:
    current = path
    first = True
    while True:
        current_stat = current.stat()
        needed = required if first else 0o1
        if not _mode_allows_account(
            current_stat,
            uid=account.pw_uid,
            gids=gids,
            required=needed,
        ):
            raise UnitRefreshError(
                f"target account {account.pw_name} lacks required access to "
                f"{label}: {current}"
            )
        if current == current.parent:
            return
        current = current.parent
        first = False


def validate_runtime_dependencies(
    config: dict,
    targets: Sequence[HermesTarget],
) -> None:
    profile_paths = {target.runtime_profile_path for target in targets}
    for profile_path in profile_paths:
        resolved_profile = _validate_trusted_runtime_profile(profile_path)
        try:
            load_runtime_profile(resolved_profile)
        except Exception as exc:
            raise UnitRefreshError(
                f"runtime profile is invalid: {profile_path}: {exc}"
            ) from exc

    hermes_cfg = config.get("hermes") or {}
    executable = Path(str(hermes_cfg.get("executable") or "/usr/local/bin/hermes"))
    if not executable.is_absolute():
        raise UnitRefreshError("Hermes executable must be an absolute path")
    try:
        executable_stat = executable.lstat()
        resolved_executable = executable.resolve(strict=True)
    except FileNotFoundError as exc:
        raise UnitRefreshError(f"Hermes executable does not exist: {executable}") from exc
    if stat.S_ISLNK(executable_stat.st_mode):
        if executable_stat.st_uid != 0:
            raise UnitRefreshError(f"Hermes executable symlink must be root-owned: {executable}")
        _validate_trusted_ancestors(executable, label="Hermes executable")
    _validate_trusted_regular_file(resolved_executable, label="Hermes executable target")
    for target in targets:
        try:
            account = pwd.getpwnam(target.linux_user)
            account_group = grp.getgrnam(target.linux_user)
        except KeyError as exc:
            raise UnitRefreshError(
                f"target account/group does not exist: {target.linux_user}"
            ) from exc
        if account.pw_uid == 0 or account_group.gr_gid == 0:
            raise UnitRefreshError(f"refusing root target account: {target.linux_user}")
        if account.pw_gid != account_group.gr_gid:
            raise UnitRefreshError(
                f"target primary group does not match account name: {target.linux_user}"
            )
        account_gids = set(os.getgrouplist(account.pw_name, account.pw_gid))
        _require_account_path_access(
            resolved_executable,
            account=account,
            gids=account_gids,
            required=0o1,
            label="Hermes executable",
        )
        if executable != resolved_executable:
            _require_account_path_access(
                executable,
                account=account,
                gids=account_gids,
                required=0o1,
                label="Hermes executable symlink",
            )
        _require_account_path_access(
            target.runtime_profile_path,
            account=account,
            gids=account_gids,
            required=0o4,
            label="runtime profile",
        )
        account_home = Path(account.pw_dir).resolve()
        target_home = target.home_dir.resolve()
        if account_home != target_home:
            raise UnitRefreshError(
                f"target home does not match passwd entry for {target.linux_user}"
            )
        for field, directory in (
            ("hermes_home", target.hermes_home),
            ("workdir", target.workdir),
        ):
            resolved = directory.resolve()
            try:
                resolved.relative_to(target_home)
            except ValueError as exc:
                raise UnitRefreshError(
                    f"target {field} must remain inside home for {target.linux_user}"
                ) from exc
            if not resolved.is_dir():
                raise UnitRefreshError(
                    f"target {field} directory does not exist for {target.linux_user}: {resolved}"
                )
            _require_account_path_access(
                resolved,
                account=account,
                gids=account_gids,
                required=0o3,
                label=field,
            )


def validate_target_set(
    targets: Sequence[HermesTarget],
    *,
    unit_dir: Path,
    expected_count: int | None,
    require_existing_set: bool,
) -> dict[str, HermesTarget]:
    by_service: dict[str, HermesTarget] = {}
    for target in targets:
        service_name = _validate_unit_name(target.systemd_service)
        if service_name in by_service:
            raise UnitRefreshError(f"duplicate mapped systemd service: {service_name}")
        by_service[service_name] = target
    if expected_count is not None and len(by_service) != expected_count:
        raise UnitRefreshError(
            f"expected {expected_count} mapped services, found {len(by_service)}"
        )
    if require_existing_set:
        existing = {path.name for path in unit_dir.glob("hermes-*.service")}
        mapped = set(by_service)
        if existing != mapped:
            missing = sorted(mapped - existing)
            orphaned = sorted(existing - mapped)
            details: list[str] = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if orphaned:
                details.append("unmapped: " + ", ".join(orphaned))
            raise UnitRefreshError(
                "existing unit set mismatch (" + "; ".join(details) + ")"
            )
    return by_service


def _validate_rendered_unit(content: bytes, target: HermesTarget) -> None:
    try:
        rendered = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnitRefreshError(f"rendered unit is not UTF-8: {target.systemd_service}") from exc
    required_lines = {
        "[Unit]",
        "[Service]",
        "Environment=HERMES_DISABLE_LAZY_INSTALLS=1",
        "Environment=HERMES_SKIP_NODE_BOOTSTRAP=1",
        "Environment=HERMES_DISABLE_GATEWAY_PLATFORMS=1",
        "Environment=HERMES_DISABLE_MCP=1",
        "Environment=HERMES_DISABLE_CRON=1",
        "Environment=HERMES_DISABLE_KANBAN=1",
        "Environment=TERMINAL_ENV=local",
        "Environment=AGENT_BROWSER_ENGINE=chrome",
        "Environment=HERMES_BUNDLED_SKILLS=/opt/potato-hermes-lite/current/share/hermes/skills",
        "Environment=HERMES_OPTIONAL_SKILLS=/opt/potato-hermes-lite/current/share/hermes/optional-skills",
        "Environment=HERMES_AGENT_BROWSER_BIN_DIR=/opt/potato-hermes-lite/current/browser/bin",
        "Environment=AGENT_BROWSER_EXECUTABLE_PATH=/opt/potato-hermes-lite/current/browser/chrome/chrome-linux64/chrome",
        f"Environment=HERMES_RUNTIME_PROFILE_PATH={target.runtime_profile_path}",
    }
    lines = set(rendered.splitlines())
    missing = sorted(required_lines - lines)
    if missing:
        raise UnitRefreshError(
            f"rendered unit lacks runtime policy for {target.systemd_service}: "
            + ", ".join(missing)
        )
    if "Environment=BROWSER_CDP_URL=" not in rendered:
        raise UnitRefreshError(
            f"rendered unit lacks BROWSER_CDP_URL for {target.systemd_service}"
        )
    if " gateway run --replace" not in rendered:
        raise UnitRefreshError(
            f"rendered unit lacks Hermes gateway ExecStart: {target.systemd_service}"
        )


def prepare_candidates(
    config: dict,
    targets: Iterable[HermesTarget],
    *,
    unit_dir: Path,
    expected_uid: int = 0,
    expected_gid: int | None = None,
    expected_mode: int | None = None,
) -> list[UnitCandidate]:
    candidates: list[UnitCandidate] = []
    for target in targets:
        service_name = _validate_unit_name(target.systemd_service)
        desired = build_systemd_unit(config, target).encode("utf-8")
        _validate_rendered_unit(desired, target)
        path = unit_dir / service_name
        original = _read_unit_snapshot(
            path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=expected_mode,
        )
        candidates.append(
            UnitCandidate(target=target, path=path, desired=desired, original=original)
        )
    return candidates


def render_diff(candidate: UnitCandidate) -> str:
    original = b"" if candidate.original is None else candidate.original.content
    return "".join(
        difflib.unified_diff(
            original.decode("utf-8", errors="replace").splitlines(keepends=True),
            candidate.desired.decode("utf-8").splitlines(keepends=True),
            fromfile=str(candidate.path),
            tofile=f"{candidate.path} (rendered)",
        )
    )


def _assert_snapshot_current(
    candidate: UnitCandidate,
    *,
    expected_uid: int,
    expected_gid: int | None,
    expected_mode: int | None,
) -> None:
    current = _read_unit_snapshot(
        candidate.path,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        expected_mode=expected_mode,
    )
    if candidate.original is None:
        if current is not None:
            raise UnitRefreshError(f"unit appeared before commit: {candidate.path}")
        return
    if current is None or current.version != candidate.original.version:
        raise UnitRefreshError(f"unit changed before commit: {candidate.path}")
    if not secrets.compare_digest(current.content, candidate.original.content):
        raise UnitRefreshError(f"unit content changed before commit: {candidate.path}")


def _snapshot_matches_after_rename(
    actual: FileSnapshot,
    expected: FileSnapshot,
) -> bool:
    # A rename may update ctime, but it must preserve inode identity, metadata,
    # mtime, and bytes. Excluding only ctime lets an exchange act as an atomic
    # compare-and-swap without discarding a concurrent administrator update.
    return (
        actual.version[:-1] == expected.version[:-1]
        and secrets.compare_digest(actual.content, expected.content)
    )


def _read_untrusted_exchange_snapshot(path: Path) -> FileSnapshot | None:
    """Read a displaced exchange entry without trusting its metadata."""
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        return None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if _file_version(os.fstat(fd)) != _file_version(file_stat):
            raise UnitRefreshError(f"exchanged entry changed while opening: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        if _file_version(os.fstat(fd)) != _file_version(file_stat):
            raise UnitRefreshError(f"exchanged entry changed while reading: {path}")
    finally:
        os.close(fd)
    return FileSnapshot(content=b"".join(chunks), version=_file_version(file_stat))


def _rename_exchange(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise UnitRefreshError("renameat2(RENAME_EXCHANGE) is required")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(destination))


@contextlib.contextmanager
def _refresh_lock(
    path: Path = DEFAULT_LOCK_PATH,
    *,
    expected_uid: int = 0,
    expected_gid: int = 0,
    validate_ancestors: bool = True,
):
    lock_dir = path.parent
    try:
        lock_dir.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    directory_stat = lock_dir.lstat()
    if (
        stat.S_ISLNK(directory_stat.st_mode)
        or not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != expected_uid
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise UnitRefreshError(
            f"unit refresh lock directory must be root-owned mode 0700: {lock_dir}"
        )
    if validate_ancestors:
        _validate_trusted_ancestors(path, label="unit refresh lock")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(lock_dir, directory_flags)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = -1
    try:
        fd = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
        lock_stat = os.fstat(fd)
        path_stat = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_nlink != 1
            or lock_stat.st_uid != expected_uid
            or lock_stat.st_gid != expected_gid
            or stat.S_IMODE(lock_stat.st_mode) != 0o600
            or (path_stat.st_dev, path_stat.st_ino)
            != (lock_stat.st_dev, lock_stat.st_ino)
        ):
            raise UnitRefreshError(f"invalid unit refresh lock file: {path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UnitRefreshError("another Hermes unit refresh is already running") from exc
        yield
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(directory_fd)


def _write_file(
    path: Path,
    content: bytes,
    *,
    mode: int,
    owner_uid: int,
    owner_gid: int,
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(path, flags, mode)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fchmod(fd, mode)
        os.fchown(fd, owner_uid, owner_gid)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)


def _stage_path(unit_dir: Path, service_name: str) -> Path:
    return unit_dir / f".{service_name}.runtime-profile.{os.getpid()}.{secrets.token_hex(6)}"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _create_backup_dir(root: Path, *, owner_uid: int) -> Path:
    root_created = False
    try:
        root.mkdir(parents=True, mode=0o700)
        root_created = True
    except FileExistsError:
        pass
    root_stat = root.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise UnitRefreshError(f"backup root must be a non-symlink directory: {root}")
    if root_stat.st_uid != owner_uid:
        raise UnitRefreshError(
            f"backup root owner must be uid {owner_uid}, got {root_stat.st_uid}: {root}"
        )
    if stat.S_IMODE(root_stat.st_mode) & 0o077:
        raise UnitRefreshError(f"backup root must have mode 0700: {root}")
    if root_created:
        _fsync_directory(root.parent)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(20):
        candidate = root / f"{timestamp}-{os.getpid()}-{secrets.token_hex(3)}"
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        _fsync_directory(root)
        return candidate
    raise UnitRefreshError(f"unable to allocate backup directory under {root}")


def _assert_no_incomplete_transactions(
    root: Path,
    *,
    expected_uid: int = 0,
    validate_ancestors: bool = True,
) -> None:
    if not root.exists():
        return
    root_stat = root.lstat()
    if (
        stat.S_ISLNK(root_stat.st_mode)
        or not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != expected_uid
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        raise UnitRefreshError(
            f"backup root must be a root-owned mode 0700 directory: {root}"
        )
    incomplete: list[str] = []
    for transaction_dir in sorted(root.iterdir()):
        if not transaction_dir.is_dir() or transaction_dir.is_symlink():
            continue
        manifest_path = transaction_dir / "transaction.json"
        if not manifest_path.exists():
            incomplete.append(f"{transaction_dir} (missing transaction.json)")
            continue
        _validate_trusted_regular_file(
            manifest_path,
            label="transaction manifest",
            expected_uid=expected_uid,
            validate_ancestors=validate_ancestors,
        )
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UnitRefreshError(
                f"cannot read transaction manifest {manifest_path}: {exc}"
            ) from exc
        status = str(payload.get("status") or "") if isinstance(payload, dict) else ""
        if status not in {"complete", "rolled_back"}:
            incomplete.append(f"{transaction_dir} ({status or 'unknown status'})")
    if incomplete:
        raise UnitRefreshError(
            "incomplete Hermes unit transaction requires manual recovery: "
            + ", ".join(incomplete)
        )


def _write_transaction_manifest(
    backup_dir: Path,
    *,
    status: str,
    candidates: Sequence[UnitCandidate],
    stage_paths: dict[Path, Path],
    owner_uid: int,
    owner_gid: int,
    errors: Sequence[str] = (),
) -> None:
    payload = {
        "schema_version": 1,
        "status": status,
        "units": [
            {
                "path": str(candidate.path),
                "stage_path": str(stage_paths[candidate.path]),
                "original_sha256": (
                    hashlib.sha256(candidate.original.content).hexdigest()
                    if candidate.original is not None
                    else None
                ),
                "desired_sha256": hashlib.sha256(candidate.desired).hexdigest(),
            }
            for candidate in candidates
        ],
        "errors": list(errors),
    }
    destination = backup_dir / "transaction.json"
    temporary = backup_dir / f".transaction.{os.getpid()}.{secrets.token_hex(5)}"
    _write_file(
        temporary,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o600,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    os.replace(temporary, destination)
    _fsync_directory(backup_dir)


def verify_with_systemd_analyze(candidates: Sequence[UnitCandidate]) -> None:
    binary = shutil.which("systemd-analyze")
    if binary is None:
        raise UnitRefreshError("systemd-analyze is required before applying units")
    with tempfile.TemporaryDirectory(prefix="potato-hermes-unit-verify-") as raw_dir:
        directory = Path(raw_dir)
        paths: list[str] = []
        for candidate in candidates:
            path = directory / candidate.path.name
            path.write_bytes(candidate.desired)
            paths.append(str(path))
        result = subprocess.run(
            [binary, "verify", *paths],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise UnitRefreshError(f"systemd-analyze verify failed: {detail}")


def apply_candidates(
    candidates: Sequence[UnitCandidate],
    *,
    backup_root: Path,
    expected_uid: int = 0,
    expected_gid: int | None = 0,
    expected_mode: int | None = 0o644,
    owner_uid: int = 0,
    owner_gid: int = 0,
    reload_fn: Callable[[], None] | None = None,
    authority_check: Callable[[], None] | None = None,
) -> Path | None:
    changed = [candidate for candidate in candidates if candidate.changed]
    if not changed:
        return None
    if any(candidate.original is None for candidate in changed):
        raise UnitRefreshError("apply only refreshes an existing complete unit set")
    unit_directories = {candidate.path.parent for candidate in changed}
    if len(unit_directories) != 1:
        raise UnitRefreshError("all unit candidates must share one systemd directory")

    for candidate in changed:
        _assert_snapshot_current(
            candidate,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=expected_mode,
        )

    backup_dir = _create_backup_dir(backup_root, owner_uid=owner_uid)
    for candidate in changed:
        if candidate.original is None:
            raise UnitRefreshError(f"unit disappeared before backup: {candidate.path}")
        _write_file(
            backup_dir / candidate.path.name,
            candidate.original.content,
            mode=0o600,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )
    _fsync_directory(backup_dir)

    stage_paths = {
        candidate.path: _stage_path(candidate.path.parent, candidate.path.name)
        for candidate in changed
    }
    _write_transaction_manifest(
        backup_dir,
        status="preparing",
        candidates=changed,
        stage_paths=stage_paths,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    installed_snapshots: dict[Path, FileSnapshot] = {}
    applied: list[AppliedSwap] = []
    preserve_stages: set[Path] = set()
    reload_attempted = False
    try:
        for candidate in changed:
            stage = stage_paths[candidate.path]
            _write_file(
                stage,
                candidate.desired,
                mode=0o644,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
            installed = _read_unit_snapshot(
                stage,
                expected_uid=owner_uid,
                expected_gid=owner_gid,
                expected_mode=0o644,
            )
            if installed is None:
                raise UnitRefreshError(f"staged unit disappeared: {stage}")
            installed_snapshots[candidate.path] = installed

        _write_transaction_manifest(
            backup_dir,
            status="prepared",
            candidates=changed,
            stage_paths=stage_paths,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )

        for candidate in changed:
            _assert_snapshot_current(
                candidate,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
                expected_mode=expected_mode,
            )
        if authority_check is not None:
            authority_check()

        try:
            for candidate in changed:
                _assert_snapshot_current(
                    candidate,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                    expected_mode=expected_mode,
                )
                stage = stage_paths[candidate.path]
                installed = installed_snapshots[candidate.path]
                _rename_exchange(stage, candidate.path)
                try:
                    displaced = _read_untrusted_exchange_snapshot(stage)
                    if displaced is None or candidate.original is None:
                        raise UnitRefreshError(
                            f"atomic unit CAS lost its displaced file: {candidate.path}"
                        )
                    if not _snapshot_matches_after_rename(
                        displaced, candidate.original
                    ):
                        raise UnitRefreshError(
                            f"atomic unit CAS detected a concurrent change: {candidate.path}"
                        )
                except BaseException as cas_exc:
                    try:
                        _rename_exchange(stage, candidate.path)
                    except BaseException as restore_exc:
                        preserve_stages.add(stage)
                        raise UnitRefreshError(
                            "atomic unit CAS could not restore the displaced entry; "
                            f"preserved at {stage}: {restore_exc}"
                        ) from cas_exc
                    try:
                        displaced_current = _read_untrusted_exchange_snapshot(stage)
                        restored_cleanly = (
                            displaced_current is not None
                            and _snapshot_matches_after_rename(
                                displaced_current, installed
                            )
                        )
                    except BaseException as inspect_exc:
                        try:
                            _rename_exchange(stage, candidate.path)
                        except BaseException as second_restore_exc:
                            preserve_stages.add(stage)
                            raise UnitRefreshError(
                                "atomic unit CAS could not preserve an uninspectable "
                                f"concurrent update at {stage}: {second_restore_exc}"
                            ) from inspect_exc
                        preserve_stages.add(stage)
                        raise UnitRefreshError(
                            "atomic unit CAS preserved an uninspectable concurrent update"
                        ) from inspect_exc
                    if not restored_cleanly:
                        try:
                            _rename_exchange(stage, candidate.path)
                        except BaseException as second_restore_exc:
                            preserve_stages.add(stage)
                            raise UnitRefreshError(
                                "atomic unit CAS could not preserve a second concurrent "
                                f"update at {stage}: {second_restore_exc}"
                            ) from cas_exc
                        preserve_stages.add(stage)
                        raise UnitRefreshError(
                            "atomic unit CAS preserved a second concurrent administrator update"
                        ) from cas_exc
                    raise
                applied.append(
                    AppliedSwap(
                        candidate=candidate,
                        displaced_path=stage,
                        installed=installed,
                    )
                )
            _fsync_directory(changed[0].path.parent)
            if authority_check is not None:
                authority_check()
            for swap in applied:
                current = _read_unit_snapshot(
                    swap.candidate.path,
                    expected_uid=owner_uid,
                    expected_gid=owner_gid,
                    expected_mode=0o644,
                )
                if current is None:
                    raise UnitRefreshError(
                        f"installed unit disappeared before reload: {swap.candidate.path}"
                    )
                if not _snapshot_matches_after_rename(current, swap.installed):
                    raise UnitRefreshError(
                        f"installed unit changed before daemon reload: {swap.candidate.path}"
                    )
            if reload_fn is not None:
                reload_attempted = True
                reload_fn()
            _write_transaction_manifest(
                backup_dir,
                status="committed",
                candidates=changed,
                stage_paths=stage_paths,
                owner_uid=owner_uid,
                owner_gid=owner_gid,
            )
        except BaseException as exc:
            rollback_errors: list[str] = []
            for swap in reversed(applied):
                candidate = swap.candidate
                stage = swap.displaced_path
                try:
                    _rename_exchange(stage, candidate.path)
                    try:
                        displaced_current = _read_untrusted_exchange_snapshot(stage)
                        current_matches = (
                            displaced_current is not None
                            and _snapshot_matches_after_rename(
                                displaced_current, swap.installed
                            )
                        )
                    except BaseException as inspect_exc:
                        try:
                            _rename_exchange(stage, candidate.path)
                        except BaseException as restore_exc:
                            preserve_stages.add(stage)
                            raise UnitRefreshError(
                                "rollback could not restore an uninspectable concurrent "
                                f"entry; preserved at {stage}: {restore_exc}"
                            ) from inspect_exc
                        preserve_stages.add(stage)
                        raise UnitRefreshError(
                            "rollback preserved an uninspectable concurrent administrator update"
                        ) from inspect_exc
                    if not current_matches:
                        try:
                            _rename_exchange(stage, candidate.path)
                        except BaseException as restore_exc:
                            preserve_stages.add(stage)
                            raise UnitRefreshError(
                                "rollback could not restore a concurrent entry; "
                                f"preserved at {stage}: {restore_exc}"
                            ) from restore_exc
                        preserve_stages.add(stage)
                        raise UnitRefreshError(
                            "rollback preserved a concurrent administrator update"
                        )
                    stage.unlink()
                except BaseException as rollback_exc:
                    rollback_errors.append(f"{candidate.path}: {rollback_exc}")
            _fsync_directory(changed[0].path.parent)
            if reload_attempted and applied and reload_fn is not None:
                try:
                    reload_fn()
                except BaseException as rollback_reload_exc:
                    rollback_errors.append(
                        f"daemon reload after rollback: {rollback_reload_exc}"
                    )
            for stage in stage_paths.values():
                if stage in preserve_stages:
                    continue
                try:
                    stage.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    preserve_stages.add(stage)
                    rollback_errors.append(f"rollback stage cleanup {stage}: {cleanup_exc}")
            _fsync_directory(changed[0].path.parent)
            try:
                _write_transaction_manifest(
                    backup_dir,
                    status=(
                        "rollback_conflict"
                        if rollback_errors or preserve_stages
                        else "rolled_back"
                    ),
                    candidates=changed,
                    stage_paths=stage_paths,
                    owner_uid=owner_uid,
                    owner_gid=owner_gid,
                    errors=rollback_errors,
                )
            except BaseException as manifest_exc:
                rollback_errors.append(f"transaction manifest: {manifest_exc}")
            detail = f"unit refresh failed and was rolled back: {exc}"
            if rollback_errors:
                detail += "; rollback errors: " + "; ".join(rollback_errors)
            if preserve_stages:
                detail += "; preserved exchange entries: " + ", ".join(
                    str(path) for path in sorted(preserve_stages)
                )
            raise UnitRefreshError(detail) from exc

        cleanup_errors: list[str] = []
        for swap in applied:
            try:
                swap.displaced_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                preserve_stages.add(swap.displaced_path)
                cleanup_errors.append(f"{swap.displaced_path}: {cleanup_exc}")
        _fsync_directory(changed[0].path.parent)
        _write_transaction_manifest(
            backup_dir,
            status="cleanup_required" if cleanup_errors else "complete",
            candidates=changed,
            stage_paths=stage_paths,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
            errors=cleanup_errors,
        )
        if cleanup_errors:
            raise UnitRefreshError(
                "units were committed but displaced files require cleanup: "
                + "; ".join(cleanup_errors)
            )
    finally:
        for stage in stage_paths.values():
            if stage not in preserve_stages:
                stage.unlink(missing_ok=True)

    return backup_dir


def _mapping_digest(path: Path) -> tuple[tuple[int, ...], str]:
    file_stat = _validate_trusted_regular_file(path, label="mapping")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if _file_version(os.fstat(fd)) != _file_version(file_stat):
            raise UnitRefreshError(f"mapping changed while opening: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if _file_version(os.fstat(fd)) != _file_version(file_stat):
            raise UnitRefreshError(f"mapping changed while reading: {path}")
    finally:
        os.close(fd)
    return _file_version(file_stat), digest.hexdigest()


def _daemon_reload() -> None:
    result = subprocess.run(
        ["systemctl", "daemon-reload"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise UnitRefreshError(
            (result.stderr or result.stdout).strip() or "systemctl daemon-reload failed"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.expect_count is not None and args.expect_count < 1:
        raise UnitRefreshError("--expect-count must be positive")
    if args.apply:
        if os.geteuid() != 0:
            raise UnitRefreshError("--apply must run as root")
        if args.expect_count is None:
            raise UnitRefreshError("--apply requires --expect-count")
        if not args.require_existing_set:
            raise UnitRefreshError("--apply requires --require-existing-set")

    if args.apply:
        with _refresh_lock():
            return _run_refresh(args)
    return _run_refresh(args)


def _run_refresh(args: argparse.Namespace) -> int:

    mapping_path = Path(os.path.abspath(args.mapping.expanduser()))
    unit_dir = Path(os.path.abspath(args.unit_dir.expanduser()))
    _validate_unit_directory(unit_dir, expected_uid=0)
    mapping_before = _mapping_digest(mapping_path)
    store = MappingStore(mapping_path)
    config = store.load_config(resolve_env=True)
    all_targets = store.load_targets()
    by_service = validate_target_set(
        all_targets,
        unit_dir=unit_dir,
        expected_count=args.expect_count,
        require_existing_set=args.require_existing_set,
    )
    del by_service
    validate_runtime_dependencies(config, all_targets)
    if args.user:
        selected = [target for target in all_targets if target.username == args.user]
        if len(selected) != 1:
            raise UnitRefreshError(f"unknown mapping username: {args.user}")
    else:
        selected = all_targets

    candidates = prepare_candidates(
        config,
        selected,
        unit_dir=unit_dir,
        expected_uid=0,
        expected_gid=0,
        expected_mode=0o644,
    )

    def authority_check() -> None:
        if _mapping_digest(mapping_path) != mapping_before:
            raise UnitRefreshError(
                f"mapping changed during unit rendering or commit: {mapping_path}"
            )

    authority_check()

    changed = [candidate for candidate in candidates if candidate.changed]
    if not args.apply:
        for candidate in changed:
            sys.stdout.write(render_diff(candidate))
        print(
            f"Checked {len(candidates)} Hermes unit(s): "
            f"{len(changed)} drifted, {len(candidates) - len(changed)} current."
        )
        return 1 if changed else 0

    backup_root = Path(os.path.abspath(args.backup_root.expanduser()))
    _assert_no_incomplete_transactions(backup_root)
    verify_with_systemd_analyze(candidates)
    backup_dir = apply_candidates(
        candidates,
        backup_root=backup_root,
        reload_fn=_daemon_reload,
        authority_check=authority_check,
    )
    if backup_dir is None:
        print(f"All {len(candidates)} Hermes unit(s) are current; no files changed.")
    else:
        print(f"Updated {len(changed)} Hermes unit(s); backups: {backup_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

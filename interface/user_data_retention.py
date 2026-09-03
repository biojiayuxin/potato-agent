from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import errno
import grp
import hashlib
import json
import os
import platform
import pwd
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence
from zoneinfo import ZoneInfo

import yaml

from interface.background_jobs import has_active_background_processes
from interface.mapping import (
    DEFAULT_MAPPING_PATH,
    HermesTarget,
    build_targets_from_config,
    resolve_env_placeholders,
)
from interface.user_lifecycle_lock import (
    DEFAULT_LIFECYCLE_LOCK_DIR,
    acquire_mapping_lock,
    acquire_retention_lock,
    acquire_user_lock,
)


DEFAULT_CONFIG_PATH = Path("/etc/potato-agent/user-data-retention.yaml")
DEFAULT_STATE_DIR = Path("/var/lib/potato-agent/retention")
DEFAULT_STATUS_PATH = DEFAULT_STATE_DIR / "status.json"
DEFAULT_AUTHORIZATION_PATH = DEFAULT_STATE_DIR / "authorization.json"
STATUS_GROUP = "potato-interface"
SCHEMA_VERSION = 1
POLICY_VERSION = "potato-user-data-retention-v1"
UPLOAD_DIR_NAME = ".potato-interface-uploads"
BATCH_SIZE = 256
MAX_STATUS_BYTES = 1024 * 1024
MAX_MAPPING_BYTES = 16 * 1024 * 1024
DAY_SECONDS = 24 * 60 * 60
SHANGHAI = ZoneInfo("Asia/Shanghai")

PROTECTED_DIRECTORY_NAMES = frozenset(
    {
        "bin",
        "sbin",
        "lib",
        "lib64",
        "include",
        "share",
        "opt",
        "apps",
        "software",
        "tools",
        "scripts",
        "src",
        "source",
        "code",
        "env",
        "envs",
        "venv",
        "venvs",
        "conda",
        "miniconda",
        "miniconda3",
        "anaconda",
        "anaconda3",
        "micromamba",
    }
)
PROTECTED_SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".pyx",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".r",
        ".rmd",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".cxx",
        ".hpp",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".rb",
        ".php",
        ".swift",
        ".lua",
        ".pl",
        ".pm",
        ".sql",
        ".ipynb",
        ".jl",
        ".m",
        ".mm",
        ".cs",
        ".fs",
        ".fsx",
        ".vb",
        ".dart",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".clj",
        ".cljs",
        ".hs",
        ".lhs",
        ".sol",
        ".vue",
        ".svelte",
        ".asm",
        ".s",
    }
)
PROTECTED_SECRET_SUFFIXES = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".csr", ".der", ".gpg", ".asc"}
)
PROTECTED_MANIFEST_NAMES = frozenset(
    {
        "pyproject.toml",
        "environment.yml",
        "environment.yaml",
        "requirements.txt",
        "requirements.in",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "uv.lock",
        "poetry.lock",
        "pipfile",
        "pipfile.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "deno.lock",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "gemfile",
        "gemfile.lock",
        "composer.json",
        "composer.lock",
        "makefile",
        "gnumakefile",
        "cmakelists.txt",
        "meson.build",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "pom.xml",
        "dockerfile",
        "compose.yaml",
        "compose.yml",
        "justfile",
    }
)
ALLOWED_RUN_STATUSES = frozenset({"running", "ok", "partial", "failed"})
ALLOWED_USER_STATUSES = frozenset({"scanned", "skipped", "partial", "error"})
ALLOWED_REASONS = frozenset(
    {
        "none",
        "runtime_active",
        "background_jobs",
        "login_session",
        "uid_processes",
        "activity_check_failed",
        "lock_busy",
        "invalid_identity",
        "unsafe_filesystem",
        "atime_unsupported",
        "limit_reached",
        "mapping_changed",
        "new_origin_preview",
        "authorization_required",
        "policy_changed",
        "journal_error",
        "other",
    }
)


class RetentionError(RuntimeError):
    pass


class UnsafeFilesystemError(RetentionError):
    pass


class ActivityCheckError(RetentionError):
    pass


class UserBecameActive(RetentionError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class FilesystemConfig:
    uuid: str
    mountpoint: Path
    home_bases: tuple[Path, ...]
    history_root: Path


@dataclass(frozen=True)
class RetentionConfig:
    mode: str
    inactive_days: int
    history_days: int
    filesystems: tuple[FilesystemConfig, ...]
    max_files_per_user: int = 1_000_000
    max_files_total: int = 5_000_000
    max_runtime_seconds: int = 4 * 3600
    min_uid: int = 1000


@dataclass(frozen=True)
class MountIdentity:
    mount_id: int
    major_minor: str
    mountpoint: Path
    uuid: str
    options: frozenset[str]
    st_dev: int


@dataclass(frozen=True)
class UserIdentity:
    target: HermesTarget
    uid: int
    gid: int
    home_dev: int
    home_ino: int
    filesystem: FilesystemConfig
    mount: MountIdentity
    hermes_relative: bytes
    identity_nonce: str = ""


@dataclass(frozen=True)
class FileCandidate:
    relpath: bytes
    dev: int
    ino: int
    mode: int
    uid: int
    gid: int
    size: int
    blocks: int
    atime_ns: int
    mtime_ns: int
    ctime_ns: int

    @property
    def allocated_bytes(self) -> int:
        return self.blocks * 512


@dataclass
class UserResult:
    mapping_username: str
    status: str = "scanned"
    reason: str = "none"
    candidate_files: int = 0
    candidate_bytes: int = 0
    staged_files: int = 0
    staged_bytes: int = 0
    due_files: int = 0
    due_bytes: int = 0
    purged_files: int = 0
    purged_bytes: int = 0
    errors: int = 0

    def public_dict(self) -> dict[str, Any]:
        reason = self.reason if self.reason in ALLOWED_REASONS else "other"
        status = self.status if self.status in ALLOWED_USER_STATUSES else "error"
        return {
            "mapping_username": self.mapping_username,
            "status": status,
            "reason": reason,
            "candidate_files": self.candidate_files,
            "candidate_bytes": self.candidate_bytes,
            "staged_files": self.staged_files,
            "staged_bytes": self.staged_bytes,
            "due_files": self.due_files,
            "due_bytes": self.due_bytes,
            "purged_files": self.purged_files,
            "purged_bytes": self.purged_bytes,
            "errors": self.errors,
        }


@dataclass
class RunResult:
    run_id: str
    mode: str
    started_at: float
    policy_sha256: str
    status: str = "running"
    finished_at: float | None = None
    users: list[UserResult] = field(default_factory=list)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_exact_keys(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise RetentionError(f"{context} contains unsupported fields")


def load_retention_config(path: Path = DEFAULT_CONFIG_PATH) -> RetentionConfig:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise RetentionError("retention configuration is missing") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RetentionError("retention configuration must be a regular file")
        if (
            file_stat.st_uid != 0
            or file_stat.st_gid != 0
            or stat.S_IMODE(file_stat.st_mode) != 0o600
        ):
            raise RetentionError("retention configuration must be root:root mode 0600")
        if file_stat.st_size > MAX_STATUS_BYTES:
            raise RetentionError("retention configuration is too large")
        body = bytearray()
        while len(body) <= MAX_STATUS_BYTES:
            chunk = os.read(fd, min(65536, MAX_STATUS_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > MAX_STATUS_BYTES:
            raise RetentionError("retention configuration is too large")
        raw = yaml.safe_load(body.decode("utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RetentionError("retention configuration is invalid") from exc
    finally:
        os.close(fd)
    if not isinstance(raw, dict):
        raise RetentionError("retention configuration must be an object")
    _require_exact_keys(
        raw,
        {
            "mode",
            "inactive_days",
            "history_days",
            "filesystems",
            "max_files_per_user",
            "max_files_total",
            "max_runtime_seconds",
            "min_uid",
        },
        "retention configuration",
    )
    mode = raw.get("mode", "preview")
    if mode not in {"preview", "enforce"}:
        raise RetentionError("retention mode must be preview or enforce")
    inactive_days = raw.get("inactive_days", 30)
    history_days = raw.get("history_days", 30)
    if inactive_days != 30 or history_days != 30:
        raise RetentionError("both retention periods must currently be exactly 30 days")
    raw_filesystems = raw.get("filesystems")
    if not isinstance(raw_filesystems, list) or not raw_filesystems:
        raise RetentionError("at least one filesystem is required")
    filesystems: list[FilesystemConfig] = []
    for item in raw_filesystems:
        if not isinstance(item, dict):
            raise RetentionError("filesystem entries must be objects")
        _require_exact_keys(
            item, {"uuid", "mountpoint", "home_bases", "history_root"}, "filesystem"
        )
        raw_bases = item.get("home_bases")
        if not isinstance(raw_bases, list) or not raw_bases:
            raise RetentionError("filesystem home_bases must be a non-empty list")
        values = [
            item.get("uuid"),
            item.get("mountpoint"),
            item.get("history_root"),
            *raw_bases,
        ]
        if not all(
            isinstance(value, str) and value.startswith("/") for value in values[1:]
        ):
            raise RetentionError("filesystem paths must be absolute")
        if not isinstance(values[0], str) or not values[0].strip():
            raise RetentionError("filesystem UUID is required")
        filesystems.append(
            FilesystemConfig(
                uuid=str(item["uuid"]).strip(),
                mountpoint=Path(str(item["mountpoint"])),
                home_bases=tuple(Path(str(value)) for value in raw_bases),
                history_root=Path(str(item["history_root"])),
            )
        )
    if len({fs.uuid.casefold() for fs in filesystems}) != len(filesystems):
        raise RetentionError("filesystem UUIDs must be unique")
    if len({fs.mountpoint for fs in filesystems}) != len(filesystems):
        raise RetentionError("filesystem mountpoints must be unique")
    if len({fs.history_root for fs in filesystems}) != len(filesystems):
        raise RetentionError("filesystem history roots must be unique")
    integer_defaults = {
        "max_files_per_user": 1_000_000,
        "max_files_total": 5_000_000,
        "max_runtime_seconds": 4 * 3600,
        "min_uid": 1000,
    }
    integers: dict[str, int] = {}
    for key, default in integer_defaults.items():
        value = raw.get(key, default)
        if not _is_int(value) or int(value) <= 0:
            raise RetentionError(f"{key} must be a positive integer")
        integers[key] = int(value)
    return RetentionConfig(
        mode=str(mode),
        inactive_days=int(inactive_days),
        history_days=int(history_days),
        filesystems=tuple(filesystems),
        **integers,
    )


def policy_document(config: RetentionConfig) -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "mode": config.mode,
        "inactive_days": config.inactive_days,
        "history_days": config.history_days,
        "max_files_per_user": config.max_files_per_user,
        "max_files_total": config.max_files_total,
        "max_runtime_seconds": config.max_runtime_seconds,
        "min_uid": config.min_uid,
        "protected_directory_names": sorted(PROTECTED_DIRECTORY_NAMES),
        "protected_source_suffixes": sorted(PROTECTED_SOURCE_SUFFIXES),
        "protected_secret_suffixes": sorted(PROTECTED_SECRET_SUFFIXES),
        "protected_manifest_names": sorted(PROTECTED_MANIFEST_NAMES),
        "filesystems": [
            {
                "uuid": fs.uuid,
                "mountpoint": str(fs.mountpoint),
                "home_bases": [str(path) for path in fs.home_bases],
                "history_root": str(fs.history_root),
            }
            for fs in config.filesystems
        ],
    }


def policy_sha256(config: RetentionConfig) -> str:
    encoded = json.dumps(
        policy_document(config), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _unescape_mountinfo(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _mountinfo_for(path: Path) -> tuple[int, str, Path, frozenset[str]]:
    resolved = path.resolve(strict=True)
    candidates: list[tuple[int, int, str, Path, frozenset[str]]] = []
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise UnsafeFilesystemError("mount table is unavailable") from exc
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            mountpoint = Path(_unescape_mountinfo(fields[4])).resolve()
            options = set(fields[5].split(","))
            options.update(fields[separator + 3].split(","))
            if resolved == mountpoint or _within(resolved, mountpoint):
                candidates.append(
                    (
                        len(mountpoint.parts),
                        int(fields[0]),
                        fields[2],
                        mountpoint,
                        frozenset(options),
                    )
                )
        except (IndexError, ValueError, OSError):
            continue
    if not candidates:
        raise UnsafeFilesystemError("filesystem mount is not identifiable")
    _depth, mount_id, major_minor, mountpoint, options = max(
        candidates, key=lambda item: item[0]
    )
    return mount_id, major_minor, mountpoint, options


def _findmnt_identity(path: Path) -> tuple[str, frozenset[str]]:
    try:
        result = subprocess.run(
            ["findmnt", "--json", "--target", str(path), "--output", "UUID,OPTIONS"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnsafeFilesystemError("filesystem UUID check is unavailable") from exc
    if result.returncode != 0:
        raise UnsafeFilesystemError("filesystem UUID check failed")
    try:
        payload = json.loads(result.stdout)
        filesystems = payload["filesystems"]
        item = filesystems[0]
        fs_uuid = str(item.get("uuid") or "")
        options = frozenset(str(item.get("options") or "").split(","))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise UnsafeFilesystemError("filesystem UUID response is invalid") from exc
    if not fs_uuid:
        raise UnsafeFilesystemError("filesystem has no stable UUID")
    return fs_uuid, options


def validate_filesystem(fs: FilesystemConfig) -> MountIdentity:
    mountpoint = fs.mountpoint.resolve(strict=True)
    if mountpoint == Path("/"):
        raise UnsafeFilesystemError("the root filesystem is never eligible")
    if fs.mountpoint.is_symlink() or mountpoint != fs.mountpoint:
        raise UnsafeFilesystemError(
            "mountpoint must be canonical and must not be a symlink"
        )
    mount_stat = mountpoint.stat()
    mount_id, major_minor, observed_mountpoint, mount_options = _mountinfo_for(
        mountpoint
    )
    found_uuid, findmnt_options = _findmnt_identity(mountpoint)
    if observed_mountpoint != mountpoint or found_uuid.casefold() != fs.uuid.casefold():
        raise UnsafeFilesystemError("filesystem identity does not match configuration")
    options = mount_options | findmnt_options
    if "ro" in options or "rw" not in options:
        raise UnsafeFilesystemError("filesystem is not writable")
    if "noatime" in options or not ({"relatime", "strictatime"} & options):
        raise UnsafeFilesystemError("filesystem atime semantics are unsupported")
    history = fs.history_root.resolve(strict=True)
    history_stat = history.stat()
    if fs.history_root.is_symlink() or not history.is_dir():
        raise UnsafeFilesystemError("history root must be a real directory")
    if (
        history != fs.history_root
        or history_stat.st_uid != 0
        or history_stat.st_gid != 0
        or stat.S_IMODE(history_stat.st_mode) != 0o700
    ):
        raise UnsafeFilesystemError("history root must be root:root mode 0700")
    if history_stat.st_dev != mount_stat.st_dev:
        raise UnsafeFilesystemError("history root must be on the configured filesystem")
    for base in fs.home_bases:
        resolved_base = base.resolve(strict=True)
        if (
            base.is_symlink()
            or resolved_base != base
            or not resolved_base.is_dir()
            or resolved_base.stat().st_dev != mount_stat.st_dev
        ):
            raise UnsafeFilesystemError(
                "home base does not match the configured filesystem"
            )
        # Flat deployments may place each user home directly below the mount root.
        # The history root may be a protected sibling in that base, but the base
        # itself must never sit inside the history tree.
        if history == resolved_base or _within(resolved_base, history):
            raise UnsafeFilesystemError("history root contains a home base")
    return MountIdentity(
        mount_id=mount_id,
        major_minor=major_minor,
        mountpoint=mountpoint,
        uuid=found_uuid,
        options=options,
        st_dev=mount_stat.st_dev,
    )


def _next_daily_run(timestamp: float) -> float:
    current = datetime.fromtimestamp(timestamp, tz=SHANGHAI)
    candidate = current.replace(hour=5, minute=0, second=0, microsecond=0)
    if candidate.timestamp() <= timestamp:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def _path_parts(relpath: bytes) -> tuple[bytes, ...]:
    if relpath.startswith(b"/") or relpath.endswith(b"/"):
        raise RetentionError("unsafe relative path")
    parts = tuple(relpath.split(b"/"))
    if not parts or any(
        not part or part in {b".", b".."} or b"/" in part or b"\x00" in part
        for part in parts
    ):
        raise RetentionError("unsafe relative path")
    return parts


def path_is_protected(relpath: bytes, *, hermes_relative: bytes) -> bool:
    parts = _path_parts(relpath)
    folded = tuple(os.fsdecode(part).casefold() for part in parts)
    hermes_parts = _path_parts(hermes_relative)
    if parts[: len(hermes_parts)] == hermes_parts:
        return True
    if folded[0] == "public_data":
        return True
    in_uploads = folded[0] == UPLOAD_DIR_NAME.casefold()
    if not in_uploads and any(part.startswith(".") for part in folded):
        return True
    if not in_uploads and any(
        part in PROTECTED_DIRECTORY_NAMES for part in folded[:-1]
    ):
        return True
    basename = folded[-1]
    if basename.startswith("id_") or basename in PROTECTED_MANIFEST_NAMES:
        return True
    suffix = Path(basename).suffix.casefold()
    return suffix in PROTECTED_SOURCE_SUFFIXES or suffix in PROTECTED_SECRET_SUFFIXES


def directory_should_prune(relpath: bytes, *, hermes_relative: bytes) -> bool:
    parts = _path_parts(relpath)
    folded = tuple(os.fsdecode(part).casefold() for part in parts)
    hermes_parts = _path_parts(hermes_relative)
    if parts[: len(hermes_parts)] == hermes_parts or folded[0] == "public_data":
        return True
    in_uploads = folded[0] == UPLOAD_DIR_NAME.casefold()
    if not in_uploads and any(part.startswith(".") for part in folded):
        return True
    return not in_uploads and any(part in PROTECTED_DIRECTORY_NAMES for part in folded)


_LIBC = ctypes.CDLL(None, use_errno=True)
_AT_FDCWD = -100
_AT_EMPTY_PATH = 0x1000
_STATX_BASIC_STATS = 0x07FF | 0x1000  # Include STATX_MNT_ID.
_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
_RENAME_NOREPLACE = 1


class _OpenHow(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


class _StatxTimestamp(ctypes.Structure):
    _fields_ = [
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    _fields_ = [
        ("mask", ctypes.c_uint32),
        ("blksize", ctypes.c_uint32),
        ("attributes", ctypes.c_uint64),
        ("nlink", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("ino", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("blocks", ctypes.c_uint64),
        ("attributes_mask", ctypes.c_uint64),
        ("atime", _StatxTimestamp),
        ("btime", _StatxTimestamp),
        ("ctime", _StatxTimestamp),
        ("mtime", _StatxTimestamp),
        ("rdev_major", ctypes.c_uint32),
        ("rdev_minor", ctypes.c_uint32),
        ("dev_major", ctypes.c_uint32),
        ("dev_minor", ctypes.c_uint32),
        ("mnt_id", ctypes.c_uint64),
        ("dio_mem_align", ctypes.c_uint32),
        ("dio_offset_align", ctypes.c_uint32),
        ("spare3", ctypes.c_uint64 * 12),
    ]


def _syscall_number(name: str) -> int:
    machine = platform.machine().casefold()
    numbers = {
        "x86_64": {"openat2": 437, "statx": 332},
        "amd64": {"openat2": 437, "statx": 332},
        "aarch64": {"openat2": 437, "statx": 291},
        "arm64": {"openat2": 437, "statx": 291},
    }
    try:
        return numbers[machine][name]
    except KeyError as exc:
        raise UnsafeFilesystemError(
            f"{name} is unsupported on this architecture"
        ) from exc


def openat2_beneath(dir_fd: int, relpath: bytes, flags: int, mode: int = 0) -> int:
    _path_parts(relpath)
    how = _OpenHow(
        flags=flags,
        mode=mode,
        resolve=_RESOLVE_BENEATH | _RESOLVE_NO_SYMLINKS | _RESOLVE_NO_XDEV,
    )
    result = _LIBC.syscall(
        _syscall_number("openat2"),
        dir_fd,
        ctypes.c_char_p(relpath),
        ctypes.byref(how),
        ctypes.sizeof(how),
    )
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), os.fsdecode(relpath))
    return int(result)


def statx_fd(fd: int) -> _Statx:
    result = _Statx()
    rc = _LIBC.syscall(
        _syscall_number("statx"),
        fd,
        ctypes.c_char_p(b""),
        _AT_EMPTY_PATH,
        _STATX_BASIC_STATS,
        ctypes.byref(result),
    )
    if rc != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def rename_noreplace(
    source_dir_fd: int, source: bytes, target_dir_fd: int, target: bytes
) -> None:
    renameat2 = getattr(_LIBC, "renameat2", None)
    if renameat2 is None:
        raise UnsafeFilesystemError("renameat2 is unavailable")
    rc = renameat2(
        source_dir_fd,
        ctypes.c_char_p(source),
        target_dir_fd,
        ctypes.c_char_p(target),
        _RENAME_NOREPLACE,
    )
    if rc != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _statx_matches(candidate: FileCandidate, current: _Statx) -> bool:
    return (
        stat.S_ISREG(current.mode)
        and current.nlink == 1
        and os.makedev(current.dev_major, current.dev_minor) == candidate.dev
        and current.ino == candidate.ino
        and current.uid == candidate.uid
        and current.gid == candidate.gid
        and current.mode == candidate.mode
        and current.size == candidate.size
        and current.blocks == candidate.blocks
        and current.atime.tv_sec * 1_000_000_000 + current.atime.tv_nsec
        == candidate.atime_ns
        and current.mtime.tv_sec * 1_000_000_000 + current.mtime.tv_nsec
        == candidate.mtime_ns
        and current.ctime.tv_sec * 1_000_000_000 + current.ctime.tv_nsec
        == candidate.ctime_ns
    )


def _sqlite_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma journal_mode=WAL")
    connection.execute("pragma synchronous=FULL")
    connection.execute("pragma foreign_keys=ON")
    return connection


def initialize_journal(history_root: Path) -> Path:
    path = history_root / "journal.sqlite3"
    existed = path.exists()
    if existed:
        value = path.lstat()
        expected_uid = 0 if os.geteuid() == 0 else os.geteuid()
        expected_gid = 0 if os.geteuid() == 0 else os.getegid()
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != expected_uid
            or value.st_gid != expected_gid
            or stat.S_IMODE(value.st_mode) != 0o600
        ):
            raise RetentionError("history journal must be root:root mode 0600")
    connection = _sqlite_connect(path)
    try:
        connection.executescript(
            """
            create table if not exists origins (
                origin_id text primary key,
                mapping_username text not null,
                linux_user text not null,
                uid integer not null,
                gid integer not null,
                home_path blob not null,
                home_dev integer not null,
                home_ino integer not null,
                identity_nonce text not null default '',
                active integer not null default 1,
                created_at real not null,
                preview_completed_at real,
                enforce_eligible_at real
            );
            create table if not exists runs (
                run_id text primary key,
                origin_id text not null references origins(origin_id),
                mode text not null,
                status text not null,
                policy_sha256 text not null,
                started_at real not null,
                finished_at real
            );
            create table if not exists entries (
                entry_id text primary key,
                origin_id text not null references origins(origin_id),
                run_id text not null references runs(run_id),
                relpath blob not null,
                archive_relpath blob not null unique,
                dev integer not null,
                ino integer not null,
                mode integer not null,
                uid integer not null,
                gid integer not null,
                size integer not null,
                blocks integer not null,
                atime_ns integer not null,
                mtime_ns integer not null,
                ctime_ns integer not null,
                state text not null,
                prepared_at real not null,
                staged_at real,
                archive_atime_ns integer,
                archive_ctime_ns integer,
                purge_prepared_at real,
                purged_at real,
                restore_prepared_at real,
                restored_at real,
                hold_reason text
            );
            create index if not exists entries_origin_state on entries(origin_id, state, staged_at);
            create table if not exists directories (
                origin_id text not null,
                relpath blob not null,
                mode integer not null,
                uid integer not null,
                gid integer not null,
                atime_ns integer not null,
                mtime_ns integer not null,
                xattrs_json text not null,
                removed integer not null default 0,
                primary key(origin_id, relpath)
            );
            create table if not exists cooldowns (
                origin_id text not null,
                relpath blob not null,
                until_at real not null,
                primary key(origin_id, relpath)
            );
            """
        )
        columns = {
            str(row["name"]) for row in connection.execute("pragma table_info(entries)")
        }
        for column in ("archive_atime_ns", "archive_ctime_ns"):
            if column not in columns:
                connection.execute(f"alter table entries add column {column} integer")
        origin_columns = {
            str(row["name"]) for row in connection.execute("pragma table_info(origins)")
        }
        if "identity_nonce" not in origin_columns:
            connection.execute(
                "alter table origins add column identity_nonce text not null default ''"
            )
        connection.commit()
    finally:
        connection.close()
    if not existed:
        os.chmod(path, 0o600)
        if os.geteuid() == 0:
            os.chown(path, 0, 0)
    return path


class Journal:
    def __init__(self, history_root: Path):
        self.history_root = history_root
        self.path = initialize_journal(history_root)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = _sqlite_connect(self.path)
        try:
            yield connection
        finally:
            connection.close()

    def origin_for(
        self, identity: UserIdentity, now: float
    ) -> tuple[str, bool, float | None]:
        home_path = os.fsencode(identity.target.home_dir)
        with self.connect() as connection:
            row = connection.execute(
                """
                select origin_id, enforce_eligible_at from origins
                 where active = 1 and mapping_username = ? and linux_user = ? and uid = ?
                   and gid = ? and home_path = ? and home_dev = ? and home_ino = ?
                   and identity_nonce = ?
                """,
                (
                    identity.target.username,
                    identity.target.linux_user,
                    identity.uid,
                    identity.gid,
                    home_path,
                    identity.home_dev,
                    identity.home_ino,
                    identity.identity_nonce,
                ),
            ).fetchone()
            if row is not None:
                return str(row["origin_id"]), False, row["enforce_eligible_at"]
            connection.execute(
                "update origins set active = 0 where mapping_username = ? and active = 1",
                (identity.target.username,),
            )
            connection.execute(
                """
                update entries set state = 'HOLD', hold_reason = 'identity_changed'
                 where origin_id in (
                    select origin_id from origins where mapping_username = ? and active = 0
                 ) and state in ('PREPARED', 'STAGED', 'PURGE_PREPARED', 'RESTORE_PREPARED')
                """,
                (identity.target.username,),
            )
            origin_id = str(uuid.uuid4())
            connection.execute(
                """
                insert into origins(
                    origin_id, mapping_username, linux_user, uid, gid, home_path,
                    home_dev, home_ino, identity_nonce, active, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    origin_id,
                    identity.target.username,
                    identity.target.linux_user,
                    identity.uid,
                    identity.gid,
                    home_path,
                    identity.home_dev,
                    identity.home_ino,
                    identity.identity_nonce,
                    now,
                ),
            )
            connection.commit()
            return origin_id, True, None

    def create_run(
        self,
        *,
        run_id: str,
        origin_id: str,
        mode: str,
        fingerprint: str,
        started_at: float,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "insert into runs values (?, ?, ?, 'running', ?, ?, null)",
                (run_id, origin_id, mode, fingerprint, started_at),
            )
            connection.commit()

    def finish_run(
        self,
        *,
        run_id: str,
        origin_id: str,
        mode: str,
        status_value: str,
        finished_at: float,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "update runs set status = ?, finished_at = ? where run_id = ?",
                (status_value, finished_at, run_id),
            )
            if mode == "preview" and status_value == "ok":
                connection.execute(
                    """
                    update origins set preview_completed_at = ?, enforce_eligible_at = ?
                     where origin_id = ?
                    """,
                    (finished_at, _next_daily_run(finished_at), origin_id),
                )
            connection.commit()

    def cooldown_active(self, origin_id: str, relpath: bytes, now: float) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "select until_at from cooldowns where origin_id = ? and relpath = ?",
                (origin_id, relpath),
            ).fetchone()
        return row is not None and float(row["until_at"]) > now

    def prepare_entries(
        self,
        *,
        origin_id: str,
        run_id: str,
        candidates: Sequence[FileCandidate],
        prepared_at: float,
    ) -> list[tuple[str, FileCandidate, bytes]]:
        prepared: list[tuple[str, FileCandidate, bytes]] = []
        with self.connect() as connection:
            for candidate in candidates:
                entry_id = str(uuid.uuid4())
                archive_relpath = (
                    b"origins/"
                    + origin_id.encode("ascii")
                    + b"/runs/"
                    + run_id.encode("ascii")
                    + b"/payload/"
                    + candidate.relpath
                )
                connection.execute(
                    """
                    insert into entries(
                        entry_id, origin_id, run_id, relpath, archive_relpath, dev, ino,
                        mode, uid, gid, size, blocks, atime_ns, mtime_ns, ctime_ns,
                        state, prepared_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?)
                    """,
                    (
                        entry_id,
                        origin_id,
                        run_id,
                        candidate.relpath,
                        archive_relpath,
                        candidate.dev,
                        candidate.ino,
                        candidate.mode,
                        candidate.uid,
                        candidate.gid,
                        candidate.size,
                        candidate.blocks,
                        candidate.atime_ns,
                        candidate.mtime_ns,
                        candidate.ctime_ns,
                        prepared_at,
                    ),
                )
                prepared.append((entry_id, candidate, archive_relpath))
            connection.commit()
        return prepared

    def mark_staged(
        self,
        entry_id: str,
        staged_at: float,
        *,
        archive_atime_ns: int | None = None,
        archive_ctime_ns: int | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                update entries set state = 'STAGED', staged_at = ?, archive_atime_ns = ?,
                       archive_ctime_ns = ?
                 where entry_id = ? and state = 'PREPARED'
                """,
                (staged_at, archive_atime_ns, archive_ctime_ns, entry_id),
            )
            connection.commit()

    def hold(self, entry_id: str, reason: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "update entries set state = 'HOLD', hold_reason = ? where entry_id = ?",
                (reason, entry_id),
            )
            connection.commit()

    def delete_unmoved_prepared(self, entry_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "delete from entries where entry_id = ? and state = 'PREPARED'",
                (entry_id,),
            )
            connection.commit()

    def staged_due(self, cutoff: float) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    select e.*, o.mapping_username, o.linux_user, o.uid as origin_uid,
                           o.gid as origin_gid, o.home_path, o.home_dev, o.home_ino, o.active
                           , o.identity_nonce
                      from entries e join origins o on o.origin_id = e.origin_id
                     where e.state = 'STAGED' and e.staged_at < ?
                     order by e.staged_at, e.entry_id
                    """,
                    (cutoff,),
                )
            )

    def staged_due_batches(
        self, cutoff: float, *, batch_size: int = BATCH_SIZE
    ) -> Iterator[list[sqlite3.Row]]:
        last_staged_at = -1.0
        last_entry_id = ""
        while True:
            with self.connect() as connection:
                rows = list(
                    connection.execute(
                        """
                        select e.*, o.mapping_username, o.linux_user,
                               o.uid as origin_uid, o.gid as origin_gid, o.home_path,
                               o.home_dev, o.home_ino, o.active, o.identity_nonce
                          from entries e join origins o on o.origin_id = e.origin_id
                         where e.state = 'STAGED' and e.staged_at < ?
                           and (e.staged_at > ? or (e.staged_at = ? and e.entry_id > ?))
                         order by e.staged_at, e.entry_id limit ?
                        """,
                        (
                            cutoff,
                            last_staged_at,
                            last_staged_at,
                            last_entry_id,
                            batch_size,
                        ),
                    )
                )
            if not rows:
                return
            yield rows
            last_staged_at = float(rows[-1]["staged_at"])
            last_entry_id = str(rows[-1]["entry_id"])

    def staged_totals(self) -> tuple[int, int]:
        with self.connect() as connection:
            row = connection.execute(
                "select count(*) as count, coalesce(sum(blocks), 0) * 512 as bytes from entries where state = 'STAGED'"
            ).fetchone()
        return int(row["count"]), int(row["bytes"])

    def prepare_purge(self, entry_id: str, now: float) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                update entries set state = 'PURGE_PREPARED', purge_prepared_at = ?
                 where entry_id = ? and state = 'STAGED'
                """,
                (now, entry_id),
            )
            connection.commit()
            return cursor.rowcount == 1

    def mark_purged(self, entry_id: str, now: float) -> None:
        with self.connect() as connection:
            connection.execute(
                "update entries set state = 'PURGED', purged_at = ? where entry_id = ? and state = 'PURGE_PREPARED'",
                (now, entry_id),
            )
            connection.commit()

    def restore_rows(
        self, *, origin_id: str, run_id: str, relpath: bytes | None
    ) -> list[sqlite3.Row]:
        query = """
            select e.*, o.mapping_username, o.linux_user, o.uid as origin_uid,
                   o.gid as origin_gid, o.home_path, o.home_dev, o.home_ino, o.active
                   , o.identity_nonce
              from entries e join origins o on o.origin_id = e.origin_id
             where e.origin_id = ? and e.run_id = ? and e.state = 'STAGED'
            """
        params: list[object] = [origin_id, run_id]
        if relpath is not None:
            query += " and e.relpath = ?"
            params.append(relpath)
        query += " order by length(e.relpath), e.relpath"
        with self.connect() as connection:
            return list(connection.execute(query, params))

    def restore_batches(
        self,
        *,
        origin_id: str,
        run_id: str,
        relpath: bytes | None,
        batch_size: int = BATCH_SIZE,
    ) -> Iterator[list[sqlite3.Row]]:
        last_entry_id = ""
        while True:
            query = """
                select e.*, o.mapping_username, o.linux_user,
                       o.uid as origin_uid, o.gid as origin_gid, o.home_path,
                       o.home_dev, o.home_ino, o.active, o.identity_nonce
                  from entries e join origins o on o.origin_id = e.origin_id
                 where e.origin_id = ? and e.run_id = ? and e.state = 'STAGED'
                   and e.entry_id > ?
                """
            params: list[object] = [origin_id, run_id, last_entry_id]
            if relpath is not None:
                query += " and e.relpath = ?"
                params.append(relpath)
            query += " order by e.entry_id limit ?"
            params.append(batch_size)
            with self.connect() as connection:
                rows = list(connection.execute(query, params))
            if not rows:
                return
            yield rows
            last_entry_id = str(rows[-1]["entry_id"])

    def prepare_restore(self, entry_id: str, now: float) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                update entries set state = 'RESTORE_PREPARED', restore_prepared_at = ?
                 where entry_id = ? and state = 'STAGED'
                """,
                (now, entry_id),
            )
            connection.commit()
            return cursor.rowcount == 1

    def mark_restored(
        self, entry_id: str, origin_id: str, relpath: bytes, now: float, days: int
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "update entries set state = 'RESTORED', restored_at = ? where entry_id = ? and state = 'RESTORE_PREPARED'",
                (now, entry_id),
            )
            connection.execute(
                """
                insert into cooldowns(origin_id, relpath, until_at) values (?, ?, ?)
                on conflict(origin_id, relpath) do update set until_at = excluded.until_at
                """,
                (origin_id, relpath, now + days * DAY_SECONDS),
            )
            connection.commit()

    def directory_record(self, origin_id: str, relpath: bytes) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "select * from directories where origin_id = ? and relpath = ?",
                (origin_id, relpath),
            ).fetchone()

    def record_directory(
        self, origin_id: str, relpath: bytes, metadata: dict[str, Any]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                insert into directories(
                    origin_id, relpath, mode, uid, gid, atime_ns, mtime_ns, xattrs_json, removed
                ) values (?, ?, ?, ?, ?, ?, ?, ?, 0)
                on conflict(origin_id, relpath) do update set
                    mode = excluded.mode,
                    uid = excluded.uid,
                    gid = excluded.gid,
                    atime_ns = excluded.atime_ns,
                    mtime_ns = excluded.mtime_ns,
                    xattrs_json = excluded.xattrs_json,
                    removed = 0
                """,
                (
                    origin_id,
                    relpath,
                    metadata["mode"],
                    metadata["uid"],
                    metadata["gid"],
                    metadata["atime_ns"],
                    metadata["mtime_ns"],
                    metadata["xattrs_json"],
                ),
            )
            connection.commit()

    def mark_directory_removed(self, origin_id: str, relpath: bytes) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                update directories set removed = 1
                 where origin_id = ? and relpath = ? and removed = 2
                """,
                (origin_id, relpath),
            )
            connection.commit()

    def prepare_directory_removal(self, origin_id: str, relpath: bytes) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                update directories set removed = 2
                 where origin_id = ? and relpath = ? and removed = 0
                """,
                (origin_id, relpath),
            )
            connection.commit()
            return cursor.rowcount == 1

    def cancel_directory_removal(self, origin_id: str, relpath: bytes) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                update directories set removed = 0
                 where origin_id = ? and relpath = ? and removed = 2
                """,
                (origin_id, relpath),
            )
            connection.commit()

    def reconcile(self) -> None:
        root_fd = _open_root_fd(self.history_root)
        try:
            with self.connect() as connection:
                directory_rows = connection.execute(
                    """
                    select d.*, o.home_path, o.home_dev, o.home_ino, o.active
                      from directories d join origins o on o.origin_id = d.origin_id
                     where d.removed = 2
                     order by d.origin_id, d.relpath
                    """
                )
                for row in directory_rows:
                    home_fd = _open_origin_home(row)
                    try:
                        exists = home_fd is not None and _directory_exists_at(
                            home_fd, bytes(row["relpath"])
                        )
                    finally:
                        if home_fd is not None:
                            os.close(home_fd)
                    connection.execute(
                        """
                        update directories set removed = ?
                         where origin_id = ? and relpath = ? and removed = 2
                        """,
                        (0 if exists else 1, row["origin_id"], row["relpath"]),
                    )
                rows = connection.execute(
                    """
                    select e.*, o.home_path, o.home_dev, o.home_ino, o.active
                      from entries e join origins o on o.origin_id = e.origin_id
                     where e.state in ('PREPARED', 'PURGE_PREPARED', 'RESTORE_PREPARED')
                     order by e.entry_id
                    """
                )
                for row in rows:
                    archive_exists = _entry_matches_at(
                        root_fd, bytes(row["archive_relpath"]), row
                    )
                    home_fd = _open_origin_home(row)
                    try:
                        source_exists = home_fd is not None and _entry_matches_at(
                            home_fd, bytes(row["relpath"]), row
                        )
                    finally:
                        if home_fd is not None:
                            os.close(home_fd)
                    state_value = str(row["state"])
                    if state_value == "PREPARED":
                        if archive_exists and not source_exists:
                            archive_stat = _statx_at(
                                root_fd, bytes(row["archive_relpath"])
                            )
                            connection.execute(
                                """
                                update entries set state = 'STAGED', staged_at = coalesce(staged_at, prepared_at),
                                       archive_atime_ns = ?, archive_ctime_ns = ? where entry_id = ?
                                """,
                                (
                                    _timestamp_ns(archive_stat.atime)
                                    if archive_stat is not None
                                    else None,
                                    _timestamp_ns(archive_stat.ctime)
                                    if archive_stat is not None
                                    else None,
                                    row["entry_id"],
                                ),
                            )
                        elif source_exists and not archive_exists:
                            connection.execute(
                                "delete from entries where entry_id = ?",
                                (row["entry_id"],),
                            )
                        else:
                            connection.execute(
                                "update entries set state = 'HOLD', hold_reason = 'reconcile_ambiguous' where entry_id = ?",
                                (row["entry_id"],),
                            )
                    elif state_value == "PURGE_PREPARED":
                        if not archive_exists and not source_exists:
                            connection.execute(
                                "update entries set state = 'PURGED', purged_at = coalesce(purged_at, purge_prepared_at) where entry_id = ?",
                                (row["entry_id"],),
                            )
                        elif archive_exists and not source_exists:
                            connection.execute(
                                "update entries set state = 'STAGED', purge_prepared_at = null where entry_id = ?",
                                (row["entry_id"],),
                            )
                        else:
                            connection.execute(
                                "update entries set state = 'HOLD', hold_reason = 'reconcile_purge' where entry_id = ?",
                                (row["entry_id"],),
                            )
                    elif source_exists and not archive_exists:
                        connection.execute(
                            "update entries set state = 'RESTORED', restored_at = coalesce(restored_at, restore_prepared_at) where entry_id = ?",
                            (row["entry_id"],),
                        )
                        connection.execute(
                            """
                            insert into cooldowns(origin_id, relpath, until_at) values (?, ?, ?)
                            on conflict(origin_id, relpath) do update set until_at = excluded.until_at
                            """,
                            (
                                row["origin_id"],
                                row["relpath"],
                                time.time() + 30 * DAY_SECONDS,
                            ),
                        )
                    elif archive_exists and not source_exists:
                        connection.execute(
                            "update entries set state = 'STAGED', restore_prepared_at = null where entry_id = ?",
                            (row["entry_id"],),
                        )
                    else:
                        connection.execute(
                            "update entries set state = 'HOLD', hold_reason = 'reconcile_restore' where entry_id = ?",
                            (row["entry_id"],),
                        )
                connection.commit()
        finally:
            os.close(root_fd)


def _open_root_fd(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NOATIME"):
        flags |= os.O_NOATIME
    return os.open(path, flags)


def _open_origin_home(row: sqlite3.Row) -> int | None:
    if not bool(row["active"]):
        return None
    path = os.fsdecode(bytes(row["home_path"]))
    try:
        fd = _open_root_fd(Path(path))
    except OSError:
        return None
    home_stat = os.fstat(fd)
    if home_stat.st_dev != int(row["home_dev"]) or home_stat.st_ino != int(
        row["home_ino"]
    ):
        os.close(fd)
        return None
    return fd


def _entry_matches_at(
    root_fd: int, relpath: bytes, row: sqlite3.Row | dict[str, Any]
) -> bool:
    flags = (
        os.O_PATH | os.O_CLOEXEC
        if hasattr(os, "O_PATH")
        else os.O_RDONLY | os.O_CLOEXEC
    )
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = openat2_beneath(root_fd, relpath, flags)
    except OSError:
        return False
    try:
        current = statx_fd(fd)
        return (
            stat.S_ISREG(current.mode)
            and current.ino == int(row["ino"])
            and current.uid == int(row["uid"])
            and current.gid == int(row["gid"])
            and current.size == int(row["size"])
        )
    finally:
        os.close(fd)


def _directory_exists_at(root_fd: int, relpath: bytes) -> bool:
    try:
        fd = openat2_beneath(
            root_fd,
            relpath,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
            return False
        raise
    os.close(fd)
    return True


def _statx_at(root_fd: int, relpath: bytes) -> _Statx | None:
    try:
        fd = openat2_beneath(
            root_fd,
            relpath,
            (os.O_PATH if hasattr(os, "O_PATH") else os.O_RDONLY)
            | os.O_CLOEXEC
            | os.O_NOFOLLOW,
        )
    except OSError:
        return None
    try:
        return statx_fd(fd)
    finally:
        os.close(fd)


def _timestamp_ns(value: _StatxTimestamp) -> int:
    return value.tv_sec * 1_000_000_000 + value.tv_nsec


def _capture_directory_metadata(dir_fd: int) -> dict[str, Any]:
    value = os.fstat(dir_fd)
    xattrs: dict[str, str] = {}
    names = os.listxattr(dir_fd)
    for name in names:
        encoded_name = os.fsencode(name)
        xattrs[base64.b64encode(encoded_name).decode("ascii")] = base64.b64encode(
            os.getxattr(dir_fd, name)
        ).decode("ascii")
    return {
        "mode": stat.S_IMODE(value.st_mode),
        "uid": value.st_uid,
        "gid": value.st_gid,
        "atime_ns": value.st_atime_ns,
        "mtime_ns": value.st_mtime_ns,
        "xattrs_json": json.dumps(xattrs, sort_keys=True, separators=(",", ":")),
    }


def _restore_directory_metadata(dir_fd: int, row: sqlite3.Row) -> None:
    os.fchown(dir_fd, int(row["uid"]), int(row["gid"]))
    os.fchmod(dir_fd, int(row["mode"]))
    xattrs = json.loads(str(row["xattrs_json"]))
    for encoded_name, encoded_value in xattrs.items():
        os.setxattr(
            dir_fd,
            base64.b64decode(encoded_name),
            base64.b64decode(encoded_value),
        )
    os.utime(dir_fd, ns=(int(row["atime_ns"]), int(row["mtime_ns"])))


def _ensure_relative_directories(
    root_fd: int, relpath: bytes, *, mode: int = 0o700
) -> None:
    current_fd = os.dup(root_fd)
    try:
        for part in _path_parts(relpath):
            try:
                os.mkdir(part, mode, dir_fd=current_fd)
            except FileExistsError:
                pass
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
    finally:
        os.close(current_fd)


def _parent_relpath(relpath: bytes) -> bytes:
    return relpath.rpartition(b"/")[0]


def _fsync_parent(root_fd: int, relpath: bytes) -> None:
    parent = _parent_relpath(relpath)
    fd = (
        os.dup(root_fd)
        if not parent
        else openat2_beneath(
            root_fd, parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _configured_filesystem_for_home(
    home: Path,
    filesystems: Sequence[FilesystemConfig],
) -> FilesystemConfig:
    matches = [
        fs
        for fs in filesystems
        if any(home != base and _within(home, base) for base in fs.home_bases)
        and not (
            home == fs.history_root
            or _within(home, fs.history_root)
            or _within(fs.history_root, home)
        )
    ]
    if len(matches) != 1:
        raise UnsafeFilesystemError(
            "mapped home is not in exactly one allowed home base"
        )
    return matches[0]


def validate_user_identity(
    target: HermesTarget,
    *,
    config: RetentionConfig,
    mounts: dict[FilesystemConfig, MountIdentity],
    raw_home_path: Path | None = None,
) -> UserIdentity:
    if raw_home_path is not None:
        try:
            if (
                raw_home_path.is_symlink()
                or raw_home_path.resolve(strict=True) != raw_home_path
            ):
                raise UnsafeFilesystemError(
                    "mapped home must be canonical and must not contain symlinks"
                )
        except OSError as exc:
            raise UnsafeFilesystemError("mapped home is unavailable") from exc
    try:
        password_entry = pwd.getpwnam(target.linux_user)
    except KeyError as exc:
        raise UnsafeFilesystemError("mapped Linux user is missing") from exc
    if password_entry.pw_uid < config.min_uid or password_entry.pw_uid == 0:
        raise UnsafeFilesystemError("system UIDs are not eligible")
    home = target.home_dir
    if Path(password_entry.pw_dir).resolve() != home:
        raise UnsafeFilesystemError("passwd home does not match mapping")
    home_lstat = home.lstat()
    if stat.S_ISLNK(home_lstat.st_mode) or not stat.S_ISDIR(home_lstat.st_mode):
        raise UnsafeFilesystemError("mapped home must be a real directory")
    if home_lstat.st_uid != password_entry.pw_uid:
        raise UnsafeFilesystemError("mapped home owner does not match Linux user")
    fs = _configured_filesystem_for_home(home, config.filesystems)
    mount = mounts[fs]
    if home_lstat.st_dev != mount.st_dev:
        raise UnsafeFilesystemError("mapped home crosses the configured filesystem")
    observed_mount_id, _major_minor, observed_mountpoint, _options = _mountinfo_for(
        home
    )
    if observed_mount_id != mount.mount_id or observed_mountpoint != mount.mountpoint:
        raise UnsafeFilesystemError("mapped home mount identity changed")
    if not _within(target.hermes_home, home) or target.hermes_home == home:
        raise UnsafeFilesystemError("Hermes home must be inside mapped home")
    hermes_relative = os.fsencode(target.hermes_home.relative_to(home))
    home_fd = _open_root_fd(home)
    try:
        statx_fd(home_fd)
    finally:
        os.close(home_fd)
    return UserIdentity(
        target=target,
        uid=password_entry.pw_uid,
        gid=password_entry.pw_gid,
        home_dev=home_lstat.st_dev,
        home_ino=home_lstat.st_ino,
        filesystem=fs,
        mount=mount,
        hermes_relative=hermes_relative,
        identity_nonce=(
            target.retention_identity_nonce
            or "legacy-"
            + hashlib.sha256(
                f"{target.username}\0{target.linux_user}\0{home}".encode("utf-8")
            ).hexdigest()
        ),
    )


def _raw_home_paths(mapping: dict[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    users = mapping.get("users")
    if not isinstance(users, list):
        return result
    for raw in users:
        if not isinstance(raw, dict):
            continue
        username = str(raw.get("username") or "").strip()
        home = raw.get("home_dir")
        if username and isinstance(home, str) and home.startswith("/"):
            result[username] = Path(home)
    return result


def _ensure_unique_uids(identities: Sequence[UserIdentity]) -> None:
    seen: dict[int, str] = {}
    for identity in identities:
        previous = seen.get(identity.uid)
        if previous is not None and previous != identity.target.username:
            raise RetentionError("mapped users must not share a Linux UID")
        seen[identity.uid] = identity.target.username


def _candidate_from_stat(relpath: bytes, value: os.stat_result) -> FileCandidate:
    return FileCandidate(
        relpath=relpath,
        dev=value.st_dev,
        ino=value.st_ino,
        mode=value.st_mode,
        uid=value.st_uid,
        gid=value.st_gid,
        size=value.st_size,
        blocks=value.st_blocks,
        atime_ns=value.st_atime_ns,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
    )


def scan_candidates(
    identity: UserIdentity,
    *,
    cutoff_ns: int,
    origin_id: str,
    journal: Journal,
    now: float,
    max_files: int,
    deadline: float,
    global_progress: dict[str, int] | None = None,
    max_files_total: int | None = None,
    activity_check: Callable[[], str | None] | None = None,
) -> Iterator[FileCandidate]:
    home_fd = _open_root_fd(identity.target.home_dir)
    visited = 0
    stack: list[tuple[int, bytes, os.ScandirIterator[str]]] = [
        (home_fd, b"", os.scandir(home_fd))
    ]
    try:
        while stack:
            dir_fd, relative_dir, iterator = stack[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                iterator.close()
                os.close(dir_fd)
                stack.pop()
                continue
            except OSError as exc:
                iterator.close()
                os.close(dir_fd)
                stack.pop()
                raise RetentionError("home traversal failed") from exc
            if time.monotonic() >= deadline:
                raise TimeoutError("retention runtime limit reached")
            name = os.fsencode(entry.name)
            relpath = name if not relative_dir else relative_dir + b"/" + name
            try:
                value = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RetentionError("home traversal failed") from exc
            if value.st_dev != identity.home_dev:
                continue
            if stat.S_ISDIR(value.st_mode):
                if directory_should_prune(
                    relpath, hermes_relative=identity.hermes_relative
                ):
                    continue
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
                if hasattr(os, "O_NOATIME"):
                    flags |= os.O_NOATIME
                try:
                    child_fd = os.open(name, flags, dir_fd=dir_fd)
                except OSError as exc:
                    if exc.errno in {
                        errno.ENOENT,
                        errno.ENOTDIR,
                        errno.ELOOP,
                        errno.EXDEV,
                    }:
                        continue
                    raise RetentionError("home traversal failed") from exc
                try:
                    current = os.fstat(child_fd)
                    current_statx = statx_fd(child_fd)
                except OSError as exc:
                    os.close(child_fd)
                    raise RetentionError("home traversal failed") from exc
                if (
                    current.st_dev != value.st_dev
                    or current.st_ino != value.st_ino
                    or current_statx.mnt_id != identity.mount.mount_id
                ):
                    os.close(child_fd)
                    continue
                try:
                    child_iterator = os.scandir(child_fd)
                except OSError as exc:
                    os.close(child_fd)
                    raise RetentionError("home traversal failed") from exc
                stack.append((child_fd, relpath, child_iterator))
                continue
            if not stat.S_ISREG(value.st_mode):
                continue
            visited += 1
            if visited > max_files:
                raise OverflowError("retention file limit reached")
            if global_progress is not None:
                global_progress["visited"] = global_progress.get("visited", 0) + 1
                if (
                    max_files_total is not None
                    and global_progress["visited"] > max_files_total
                ):
                    raise OverflowError("global retention file limit reached")
            if activity_check is not None and visited % BATCH_SIZE == 0:
                if reason := activity_check():
                    raise UserBecameActive(reason)
            if value.st_uid != identity.uid or value.st_nlink != 1:
                continue
            if value.st_mode & 0o111:
                continue
            if path_is_protected(relpath, hermes_relative=identity.hermes_relative):
                continue
            if (
                max(value.st_atime_ns, value.st_mtime_ns, value.st_ctime_ns)
                >= cutoff_ns
            ):
                continue
            if journal.cooldown_active(origin_id, relpath, now):
                continue
            probe_fd = -1
            try:
                probe_fd = openat2_beneath(
                    home_fd,
                    relpath,
                    (os.O_PATH if hasattr(os, "O_PATH") else os.O_RDONLY)
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                )
                probe = statx_fd(probe_fd)
                if probe.mnt_id != identity.mount.mount_id:
                    continue
            except OSError as exc:
                if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV}:
                    continue
                raise RetentionError("file revalidation failed") from exc
            finally:
                if probe_fd >= 0:
                    os.close(probe_fd)
            yield _candidate_from_stat(relpath, value)
    finally:
        for dir_fd, _relative, iterator in stack:
            iterator.close()
            with contextlib.suppress(OSError):
                os.close(dir_fd)


def _run_checked(
    command: list[str], *, timeout: int = 10
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActivityCheckError("activity command failed") from exc


def _system_clock_synchronized() -> bool:
    result = _run_checked(
        ["timedatectl", "show", "--property=NTPSynchronized", "--value"]
    )
    return result.returncode == 0 and result.stdout.strip().casefold() == "yes"


def user_activity_reason(identity: UserIdentity) -> str | None:
    service = _run_checked(["systemctl", "is-active", identity.target.systemd_service])
    service_state = service.stdout.strip()
    if service.returncode == 0 and service_state == "active":
        return "runtime_active"
    if service.returncode != 3 or service_state not in {"inactive", "failed"}:
        raise ActivityCheckError("runtime activity check failed")
    try:
        if has_active_background_processes(identity.target):
            return "background_jobs"
    except Exception as exc:
        raise ActivityCheckError("background activity check failed") from exc
    sessions = _run_checked(["loginctl", "list-sessions", "--no-legend", "--no-pager"])
    if sessions.returncode != 0:
        raise ActivityCheckError("login activity check failed")
    for line in sessions.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit() and int(fields[1]) == identity.uid:
            return "login_session"
    try:
        proc_entries = os.scandir("/proc")
    except OSError as exc:
        raise ActivityCheckError("process activity check failed") from exc
    with proc_entries:
        for entry in proc_entries:
            if not entry.name.isdigit():
                continue
            try:
                status_text = (Path("/proc") / entry.name / "status").read_text(
                    encoding="ascii", errors="strict"
                )
                uid_line = next(
                    (
                        line
                        for line in status_text.splitlines()
                        if line.startswith("Uid:")
                    ),
                    "",
                )
                process_uids = [int(value) for value in uid_line.split()[1:]]
                if identity.uid in process_uids:
                    return "uid_processes"
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError, ValueError) as exc:
                raise ActivityCheckError("process activity check failed") from exc
    return None


def _activity_reason_or_failure(identity: UserIdentity) -> str | None:
    try:
        return user_activity_reason(identity)
    except ActivityCheckError:
        return "activity_check_failed"


def _ensure_archive_parents(history_fd: int, archive_relpath: bytes) -> None:
    parent = _parent_relpath(archive_relpath)
    if parent:
        _ensure_relative_directories(history_fd, parent)


def _record_candidate_directories(
    home_fd: int,
    journal: Journal,
    origin_id: str,
    candidate: FileCandidate,
    recorded_dirs: set[bytes],
) -> bool:
    parent = _parent_relpath(candidate.relpath)
    parts = list(_path_parts(parent)) if parent else []
    for index in range(1, len(parts) + 1):
        relpath = b"/".join(parts[:index])
        if relpath in recorded_dirs:
            continue
        try:
            fd = openat2_beneath(
                home_fd,
                relpath,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            try:
                metadata = _capture_directory_metadata(fd)
            finally:
                os.close(fd)
            journal.record_directory(origin_id, relpath, metadata)
            recorded_dirs.add(relpath)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
    return True


def stage_batch(
    identity: UserIdentity,
    *,
    origin_id: str,
    run_id: str,
    candidates: Sequence[FileCandidate],
    journal: Journal,
    now: float,
) -> tuple[int, int, set[bytes]]:
    if not candidates:
        return 0, 0, set()
    prepared = journal.prepare_entries(
        origin_id=origin_id,
        run_id=run_id,
        candidates=candidates,
        prepared_at=now,
    )
    home_fd = _open_root_fd(identity.target.home_dir)
    history_fd = _open_root_fd(identity.filesystem.history_root)
    staged_files = 0
    staged_bytes = 0
    possible_empty_dirs: set[bytes] = set()
    recorded_dirs: set[bytes] = set()
    try:
        for entry_id, candidate, archive_relpath in prepared:
            source_fd = -1
            try:
                flags = (
                    (os.O_PATH if hasattr(os, "O_PATH") else os.O_RDONLY)
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW
                )
                if not hasattr(os, "O_PATH") and hasattr(os, "O_NOATIME"):
                    flags |= os.O_NOATIME
                source_fd = openat2_beneath(home_fd, candidate.relpath, flags)
                current = statx_fd(source_fd)
                if not _statx_matches(candidate, current):
                    journal.delete_unmoved_prepared(entry_id)
                    continue
                metadata_complete = _record_candidate_directories(
                    home_fd, journal, origin_id, candidate, recorded_dirs
                )
                _ensure_archive_parents(history_fd, archive_relpath)
                rename_noreplace(
                    home_fd, candidate.relpath, history_fd, archive_relpath
                )
                _fsync_parent(home_fd, candidate.relpath)
                _fsync_parent(history_fd, archive_relpath)
                staged_at = time.time()
                archive_stat = _statx_at(history_fd, archive_relpath)
                if archive_stat is None:
                    raise OSError(errno.EIO, "archived file cannot be revalidated")
                journal.mark_staged(
                    entry_id,
                    staged_at,
                    archive_atime_ns=_timestamp_ns(archive_stat.atime),
                    archive_ctime_ns=_timestamp_ns(archive_stat.ctime),
                )
                staged_files += 1
                staged_bytes += candidate.allocated_bytes
                if metadata_complete:
                    parent = _parent_relpath(candidate.relpath)
                    while parent:
                        possible_empty_dirs.add(parent)
                        parent = _parent_relpath(parent)
            except FileExistsError:
                journal.hold(entry_id, "archive_collision")
            except OSError:
                if _entry_matches_at(
                    history_fd,
                    archive_relpath,
                    {
                        "ino": candidate.ino,
                        "uid": candidate.uid,
                        "gid": candidate.gid,
                        "size": candidate.size,
                    },
                ):
                    try:
                        _fsync_parent(home_fd, candidate.relpath)
                        _fsync_parent(history_fd, archive_relpath)
                        archive_stat = _statx_at(history_fd, archive_relpath)
                        if archive_stat is not None:
                            journal.mark_staged(
                                entry_id,
                                time.time(),
                                archive_atime_ns=_timestamp_ns(archive_stat.atime),
                                archive_ctime_ns=_timestamp_ns(archive_stat.ctime),
                            )
                            staged_files += 1
                            staged_bytes += candidate.allocated_bytes
                    except OSError:
                        # Leave PREPARED for startup reconciliation.
                        pass
                else:
                    journal.hold(entry_id, "stage_failed")
            finally:
                if source_fd >= 0:
                    os.close(source_fd)
    finally:
        os.close(history_fd)
        os.close(home_fd)
    return staged_files, staged_bytes, possible_empty_dirs


def remove_batch_empty_directories(
    identity: UserIdentity,
    *,
    origin_id: str,
    journal: Journal,
    relative_dirs: Iterable[bytes],
) -> None:
    home_fd = _open_root_fd(identity.target.home_dir)
    try:
        for relpath in sorted(
            set(relative_dirs), key=lambda value: value.count(b"/"), reverse=True
        ):
            if directory_should_prune(
                relpath, hermes_relative=identity.hermes_relative
            ):
                continue
            record = journal.directory_record(origin_id, relpath)
            if record is None:
                continue
            if not journal.prepare_directory_removal(origin_id, relpath):
                continue
            removed = False
            try:
                os.rmdir(relpath, dir_fd=home_fd)
                removed = True
                _fsync_parent(home_fd, relpath)
                journal.mark_directory_removed(origin_id, relpath)
            except OSError as exc:
                if removed or exc.errno == errno.ENOENT:
                    journal.mark_directory_removed(origin_id, relpath)
                else:
                    journal.cancel_directory_removal(origin_id, relpath)
    finally:
        os.close(home_fd)


def _origin_matches_identity(row: sqlite3.Row, identity: UserIdentity) -> bool:
    return (
        bool(row["active"])
        and str(row["mapping_username"]) == identity.target.username
        and str(row["linux_user"]) == identity.target.linux_user
        and int(row["origin_uid"]) == identity.uid
        and int(row["origin_gid"]) == identity.gid
        and bytes(row["home_path"]) == os.fsencode(identity.target.home_dir)
        and int(row["home_dev"]) == identity.home_dev
        and int(row["home_ino"]) == identity.home_ino
        and str(row["identity_nonce"]) == identity.identity_nonce
    )


def _archive_entry_matches(history_fd: int, row: sqlite3.Row) -> bool:
    try:
        fd = openat2_beneath(
            history_fd,
            bytes(row["archive_relpath"]),
            (os.O_PATH if hasattr(os, "O_PATH") else os.O_RDONLY)
            | os.O_CLOEXEC
            | os.O_NOFOLLOW,
        )
    except OSError:
        return False
    try:
        current = statx_fd(fd)
        archive_atime_ns = row["archive_atime_ns"]
        archive_ctime_ns = row["archive_ctime_ns"]
        return (
            stat.S_ISREG(current.mode)
            and current.nlink == 1
            and os.makedev(current.dev_major, current.dev_minor) == int(row["dev"])
            and current.ino == int(row["ino"])
            and current.uid == int(row["uid"])
            and current.gid == int(row["gid"])
            and current.mode == int(row["mode"])
            and current.size == int(row["size"])
            and current.blocks == int(row["blocks"])
            and current.mtime.tv_sec * 1_000_000_000 + current.mtime.tv_nsec
            == int(row["mtime_ns"])
            and archive_atime_ns is not None
            and _timestamp_ns(current.atime) == int(archive_atime_ns)
            and archive_ctime_ns is not None
            and _timestamp_ns(current.ctime) == int(archive_ctime_ns)
        )
    finally:
        os.close(fd)


def purge_due_entries(
    journal: Journal,
    *,
    cutoff: float,
    identities: dict[str, UserIdentity],
    mode: str,
    now: float,
    result_by_username: dict[str, UserResult],
    lock_dir: Path,
) -> None:
    history_fd = _open_root_fd(journal.history_root)
    try:
        for rows in journal.staged_due_batches(cutoff):
            for row in rows:
                _purge_due_entry(
                    row,
                    journal=journal,
                    history_fd=history_fd,
                    identities=identities,
                    mode=mode,
                    now=now,
                    result_by_username=result_by_username,
                    lock_dir=lock_dir,
                )
    finally:
        os.close(history_fd)


def _purge_due_entry(
    row: sqlite3.Row,
    *,
    journal: Journal,
    history_fd: int,
    identities: dict[str, UserIdentity],
    mode: str,
    now: float,
    result_by_username: dict[str, UserResult],
    lock_dir: Path,
) -> None:
    username = str(row["mapping_username"])
    user_result = result_by_username.get(username)
    if user_result is not None:
        user_result.due_files += 1
        user_result.due_bytes += int(row["blocks"]) * 512
    if mode != "enforce":
        return
    identity = identities.get(username)
    if identity is None or not _origin_matches_identity(row, identity):
        journal.hold(str(row["entry_id"]), "identity_changed")
        return
    if path_is_protected(
        bytes(row["relpath"]), hermes_relative=identity.hermes_relative
    ):
        journal.hold(str(row["entry_id"]), "policy_protected")
        return
    try:
        user_lock = acquire_user_lock(
            username,
            exclusive=True,
            blocking=False,
            publish_marker=True,
            run_id=str(row["run_id"]),
            lock_dir=lock_dir,
        )
    except BlockingIOError:
        if user_result is not None:
            user_result.status = "partial"
            user_result.reason = "lock_busy"
        return
    with user_lock:
        reason = _activity_reason_or_failure(identity)
        if reason is not None:
            if user_result is not None:
                user_result.status = "partial"
                user_result.reason = reason
            return
        if not _archive_entry_matches(history_fd, row):
            journal.hold(str(row["entry_id"]), "archive_metadata_changed")
            return
        if not journal.prepare_purge(str(row["entry_id"]), now):
            return
        try:
            os.unlink(bytes(row["archive_relpath"]), dir_fd=history_fd)
            _fsync_parent(history_fd, bytes(row["archive_relpath"]))
        except OSError:
            journal.hold(str(row["entry_id"]), "purge_failed")
            return
        journal.mark_purged(str(row["entry_id"]), time.time())
        if user_result is not None:
            user_result.purged_files += 1
            user_result.purged_bytes += int(row["blocks"]) * 512


def _ensure_restore_parents(
    home_fd: int,
    *,
    journal: Journal,
    origin_id: str,
    relpath: bytes,
    created_directories: set[bytes],
) -> None:
    parent = _parent_relpath(relpath)
    if not parent:
        return
    parts = _path_parts(parent)
    current_fd = os.dup(home_fd)
    try:
        for index, part in enumerate(parts, start=1):
            current_relpath = b"/".join(parts[:index])
            created = False
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                record = journal.directory_record(origin_id, current_relpath)
                if record is None or not bool(record["removed"]):
                    raise RetentionError("restore parent metadata is unavailable")
                os.mkdir(part, 0o700, dir_fd=current_fd)
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                created = True
                created_directories.add(current_relpath)
                _restore_directory_metadata(next_fd, record)
                os.fsync(current_fd)
            if created:
                os.fsync(next_fd)
            os.close(current_fd)
            current_fd = next_fd
    finally:
        os.close(current_fd)


def _restore_created_directory_metadata(
    home_fd: int,
    *,
    journal: Journal,
    origin_id: str,
    relpath: bytes,
    created_directories: set[bytes],
) -> None:
    parent = _parent_relpath(relpath)
    while parent:
        if parent in created_directories:
            record = journal.directory_record(origin_id, parent)
            if record is None or not bool(record["removed"]):
                raise RetentionError("restore parent metadata is unavailable")
            fd = openat2_beneath(
                home_fd,
                parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            try:
                _restore_directory_metadata(fd, record)
                os.fsync(fd)
            finally:
                os.close(fd)
        parent = _parent_relpath(parent)


def restore_entries(
    *,
    config: RetentionConfig,
    origin_id: str,
    run_id: str,
    relpath: bytes | None,
    mapping_path: Path = DEFAULT_MAPPING_PATH,
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
    now: float | None = None,
) -> dict[str, int]:
    require_root()
    timestamp = time.time() if now is None else now
    mounts = {fs: validate_filesystem(fs) for fs in config.filesystems}
    restored = 0
    held = 0
    with acquire_mapping_lock(exclusive=False, lock_dir=lock_dir):
        _targets, identities, _failures = _prepare_identities(
            config=config,
            mounts=mounts,
            mapping_path=mapping_path,
        )
        for fs in config.filesystems:
            journal = Journal(fs.history_root)
            journal.reconcile()
            batches = journal.restore_batches(
                origin_id=origin_id, run_id=run_id, relpath=relpath
            )
            pending_batch = next(batches, None)
            if not pending_batch:
                continue
            username = str(pending_batch[0]["mapping_username"])
            identity = identities.get(username)
            if identity is None:
                while pending_batch:
                    for row in pending_batch:
                        journal.hold(str(row["entry_id"]), "identity_changed")
                        held += 1
                    pending_batch = next(batches, None)
                continue
            with acquire_user_lock(
                username,
                exclusive=True,
                publish_marker=True,
                run_id=run_id,
                lock_dir=lock_dir,
            ):
                if (reason := user_activity_reason(identity)) is not None:
                    raise RetentionError(
                        f"restore refused while user is active: {reason}"
                    )
                home_fd = _open_root_fd(identity.target.home_dir)
                history_fd = _open_root_fd(fs.history_root)
                created_directories: set[bytes] = set()
                try:
                    while pending_batch:
                        if (reason := user_activity_reason(identity)) is not None:
                            raise RetentionError(
                                f"restore interrupted while user is active: {reason}"
                            )
                        for row in pending_batch:
                            if not _origin_matches_identity(
                                row, identity
                            ) or not _archive_entry_matches(history_fd, row):
                                journal.hold(
                                    str(row["entry_id"]),
                                    "restore_identity_mismatch",
                                )
                                held += 1
                                continue
                            item_relpath = bytes(row["relpath"])
                            if not journal.prepare_restore(
                                str(row["entry_id"]), timestamp
                            ):
                                continue
                            _ensure_restore_parents(
                                home_fd,
                                journal=journal,
                                origin_id=origin_id,
                                relpath=item_relpath,
                                created_directories=created_directories,
                            )
                            try:
                                rename_noreplace(
                                    history_fd,
                                    bytes(row["archive_relpath"]),
                                    home_fd,
                                    item_relpath,
                                )
                                _fsync_parent(history_fd, bytes(row["archive_relpath"]))
                                _fsync_parent(home_fd, item_relpath)
                            except OSError as exc:
                                journal.hold(
                                    str(row["entry_id"]),
                                    "restore_collision"
                                    if exc.errno == errno.EEXIST
                                    else "restore_failed",
                                )
                                held += 1
                                continue
                            journal.mark_restored(
                                str(row["entry_id"]),
                                origin_id,
                                item_relpath,
                                time.time(),
                                config.inactive_days,
                            )
                            try:
                                _restore_created_directory_metadata(
                                    home_fd,
                                    journal=journal,
                                    origin_id=origin_id,
                                    relpath=item_relpath,
                                    created_directories=created_directories,
                                )
                            except (OSError, ValueError, json.JSONDecodeError) as exc:
                                raise RetentionError(
                                    "restored directory metadata could not be reapplied"
                                ) from exc
                            restored += 1
                        pending_batch = next(batches, None)
                finally:
                    os.close(history_fd)
                    os.close(home_fd)
    return {"restored_files": restored, "held_files": held}


def _authorization_payload(path: Path) -> dict[str, Any] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        value = os.fstat(fd)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != 0
            or value.st_gid != 0
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_size > 65536
        ):
            return None
        body = bytearray()
        while len(body) <= 65536:
            chunk = os.read(fd, min(8192, 65537 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > 65536:
            return None
        payload = json.loads(body.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    finally:
        os.close(fd)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "policy_sha256",
        "preview_run_id",
        "preview_finished_at",
        "authorized_at",
    }:
        return None
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or not isinstance(payload.get("policy_sha256"), str)
        or len(payload["policy_sha256"]) != 64
        or not isinstance(payload.get("preview_run_id"), str)
        or not isinstance(payload.get("preview_finished_at"), (int, float))
        or isinstance(payload.get("preview_finished_at"), bool)
        or not isinstance(payload.get("authorized_at"), (int, float))
        or isinstance(payload.get("authorized_at"), bool)
    ):
        return None
    return payload


def _write_root_json(
    path: Path, payload: dict[str, Any], *, mode: int, gid: int = 0
) -> None:
    body = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    fd, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, 0, gid)
        os.write(fd, body)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        directory_fd = _open_root_fd(path.parent)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _validate_status_directory(path: Path, group_name: str = STATUS_GROUP) -> int:
    try:
        expected_gid = grp.getgrnam(group_name).gr_gid
        value = path.lstat()
    except (KeyError, OSError) as exc:
        raise RetentionError("retention status directory is unavailable") from exc
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or value.st_uid != 0
        or value.st_gid != expected_gid
        or stat.S_IMODE(value.st_mode) != 0o750
    ):
        raise RetentionError(
            "retention status directory must be root:potato-interface mode 0750"
        )
    return expected_gid


def authorize_enforce(
    config: RetentionConfig,
    *,
    confirm: bool,
    status_path: Path = DEFAULT_STATUS_PATH,
    authorization_path: Path = DEFAULT_AUTHORIZATION_PATH,
    now: float | None = None,
) -> dict[str, Any]:
    require_root()
    timestamp = time.time() if now is None else now
    if not confirm:
        raise RetentionError("authorize-enforce requires --confirm")
    if config.mode != "enforce":
        raise RetentionError("configuration mode must be enforce before authorization")
    _validate_status_directory(authorization_path.parent)
    status_payload = read_cleanup_status(status_path, require_secure_owner=True)
    latest = status_payload.get("latest_run")
    fingerprint = policy_sha256(config)
    if (
        not isinstance(latest, dict)
        or latest.get("mode") != "preview"
        or latest.get("status") != "ok"
        or latest.get("policy_sha256") != fingerprint
        or not isinstance(latest.get("finished_at_epoch"), (int, float))
        or timestamp - float(latest["finished_at_epoch"]) > DAY_SECONDS
        or float(latest["finished_at_epoch"]) > timestamp
    ):
        raise RetentionError(
            "a complete matching preview from the last 24 hours is required"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_sha256": fingerprint,
        "preview_run_id": str(latest.get("id") or ""),
        "preview_finished_at": float(latest["finished_at_epoch"]),
        "authorized_at": timestamp,
    }
    _write_root_json(authorization_path, payload, mode=0o600)
    return payload


def revoke_enforce(authorization_path: Path = DEFAULT_AUTHORIZATION_PATH) -> bool:
    require_root()
    try:
        authorization_path.unlink()
    except FileNotFoundError:
        return False
    directory_fd = _open_root_fd(authorization_path.parent)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return True


def enforce_is_authorized(
    config: RetentionConfig,
    *,
    authorization_path: Path,
    now: float,
) -> bool:
    payload = _authorization_payload(authorization_path)
    return bool(
        payload
        and payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("policy_sha256") == policy_sha256(config)
        and isinstance(payload.get("authorized_at"), (int, float))
        and float(payload["authorized_at"]) <= now
    )


def _iso_timestamp(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def _status_payload(
    result: RunResult,
    *,
    config: RetentionConfig,
    history_files: int,
    history_bytes: int,
) -> dict[str, Any]:
    users = [item.public_dict() for item in result.users]
    count_fields = (
        "candidate_files",
        "candidate_bytes",
        "staged_files",
        "staged_bytes",
        "due_files",
        "due_bytes",
        "purged_files",
        "purged_bytes",
        "errors",
    )
    totals = {
        field_name: sum(int(item[field_name]) for item in users)
        for field_name in count_fields
    }
    totals["history_files"] = history_files
    totals["history_bytes"] = history_bytes
    now = time.time()
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _iso_timestamp(now),
        "updated_at_epoch": now,
        "heartbeat_at": _iso_timestamp(now) if result.status == "running" else None,
        "policy": {
            "mode": config.mode,
            "inactive_days": config.inactive_days,
            "history_days": config.history_days,
            "policy_sha256": result.policy_sha256,
        },
        "schedule": {"time": "05:00", "timezone": "Asia/Shanghai", "persistent": True},
        "latest_run": {
            "id": result.run_id,
            "mode": result.mode,
            "status": result.status,
            "started_at": _iso_timestamp(result.started_at),
            "started_at_epoch": result.started_at,
            "finished_at": _iso_timestamp(result.finished_at),
            "finished_at_epoch": result.finished_at,
            "policy_sha256": result.policy_sha256,
        },
        "totals": totals,
        "users": users,
    }


def write_cleanup_status(
    payload: dict[str, Any],
    *,
    path: Path = DEFAULT_STATUS_PATH,
    group_name: str = STATUS_GROUP,
) -> None:
    gid = _validate_status_directory(path.parent, group_name)
    _write_root_json(path, payload, mode=0o640, gid=gid)


def _nonnegative_int(value: object) -> bool:
    return _is_int(value) and int(value) >= 0


def read_cleanup_status(
    path: Path = DEFAULT_STATUS_PATH,
    *,
    require_secure_owner: bool = True,
) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RetentionError("file cleanup status is unavailable") from exc
    try:
        value = os.fstat(fd)
        if not stat.S_ISREG(value.st_mode) or value.st_size > MAX_STATUS_BYTES:
            raise RetentionError("file cleanup status is invalid")
        if require_secure_owner:
            try:
                status_gid = grp.getgrnam(STATUS_GROUP).gr_gid
            except KeyError as exc:
                raise RetentionError(
                    "file cleanup status group is unavailable"
                ) from exc
            if (
                value.st_uid != 0
                or value.st_gid != status_gid
                or stat.S_IMODE(value.st_mode) != 0o640
            ):
                raise RetentionError("file cleanup status permissions are invalid")
        body = bytearray()
        while len(body) <= MAX_STATUS_BYTES:
            chunk = os.read(fd, min(65536, MAX_STATUS_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > MAX_STATUS_BYTES:
            raise RetentionError("file cleanup status is too large")
    finally:
        os.close(fd)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RetentionError("file cleanup status is invalid") from exc
    _validate_status_schema(payload)
    return payload


def _validate_status_schema(payload: object) -> None:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "updated_at",
        "updated_at_epoch",
        "heartbeat_at",
        "policy",
        "schedule",
        "latest_run",
        "totals",
        "users",
    }:
        raise RetentionError("file cleanup status schema is invalid")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise RetentionError("file cleanup status version is unsupported")
    if (
        not isinstance(payload["updated_at"], str)
        or not isinstance(payload["updated_at_epoch"], (int, float))
        or isinstance(payload["updated_at_epoch"], bool)
        or payload["updated_at_epoch"] < 0
        or (
            payload["heartbeat_at"] is not None
            and not isinstance(payload["heartbeat_at"], str)
        )
    ):
        raise RetentionError("file cleanup status timestamp is invalid")
    policy = payload["policy"]
    schedule = payload["schedule"]
    latest = payload["latest_run"]
    totals = payload["totals"]
    users = payload["users"]
    if not isinstance(policy, dict) or set(policy) != {
        "mode",
        "inactive_days",
        "history_days",
        "policy_sha256",
    }:
        raise RetentionError("file cleanup policy status is invalid")
    if (
        policy["mode"] not in {"preview", "enforce"}
        or policy["inactive_days"] != 30
        or policy["history_days"] != 30
    ):
        raise RetentionError("file cleanup policy status is invalid")
    if (
        not isinstance(policy["policy_sha256"], str)
        or len(policy["policy_sha256"]) != 64
    ):
        raise RetentionError("file cleanup policy fingerprint is invalid")
    try:
        int(policy["policy_sha256"], 16)
    except ValueError as exc:
        raise RetentionError("file cleanup policy fingerprint is invalid") from exc
    if not isinstance(schedule, dict) or set(schedule) != {
        "time",
        "timezone",
        "persistent",
    }:
        raise RetentionError("file cleanup schedule is invalid")
    if schedule != {"time": "05:00", "timezone": "Asia/Shanghai", "persistent": True}:
        raise RetentionError("file cleanup schedule is invalid")
    required_latest = {
        "id",
        "mode",
        "status",
        "started_at",
        "started_at_epoch",
        "finished_at",
        "finished_at_epoch",
        "policy_sha256",
    }
    if not isinstance(latest, dict) or set(latest) != required_latest:
        raise RetentionError("file cleanup run status is invalid")
    if (
        latest["mode"] not in {"preview", "enforce"}
        or latest["status"] not in ALLOWED_RUN_STATUSES
    ):
        raise RetentionError("file cleanup run status is invalid")
    if (
        not isinstance(latest["id"], str)
        or len(latest["id"]) > 128
        or not isinstance(latest["started_at"], str)
        or not isinstance(latest["started_at_epoch"], (int, float))
        or isinstance(latest["started_at_epoch"], bool)
        or (
            latest["finished_at"] is not None
            and not isinstance(latest["finished_at"], str)
        )
        or (
            latest["finished_at_epoch"] is not None
            and (
                not isinstance(latest["finished_at_epoch"], (int, float))
                or isinstance(latest["finished_at_epoch"], bool)
            )
        )
        or latest["policy_sha256"] != policy["policy_sha256"]
    ):
        raise RetentionError("file cleanup run status is invalid")
    total_fields = {
        "candidate_files",
        "candidate_bytes",
        "staged_files",
        "staged_bytes",
        "due_files",
        "due_bytes",
        "purged_files",
        "purged_bytes",
        "errors",
        "history_files",
        "history_bytes",
    }
    if (
        not isinstance(totals, dict)
        or set(totals) != total_fields
        or not all(_nonnegative_int(value) for value in totals.values())
    ):
        raise RetentionError("file cleanup totals are invalid")
    if not isinstance(users, list) or len(users) > 100_000:
        raise RetentionError("file cleanup users are invalid")
    user_fields = {
        "mapping_username",
        "status",
        "reason",
        "candidate_files",
        "candidate_bytes",
        "staged_files",
        "staged_bytes",
        "due_files",
        "due_bytes",
        "purged_files",
        "purged_bytes",
        "errors",
    }
    for item in users:
        if not isinstance(item, dict) or set(item) != user_fields:
            raise RetentionError("file cleanup user status is invalid")
        if (
            not isinstance(item["mapping_username"], str)
            or not item["mapping_username"]
            or len(item["mapping_username"]) > 256
            or any(ord(character) < 32 for character in item["mapping_username"])
        ):
            raise RetentionError("file cleanup username is invalid")
        if item["status"] not in ALLOWED_USER_STATUSES:
            raise RetentionError("file cleanup user status is invalid")
        if not isinstance(item["reason"], str):
            raise RetentionError("file cleanup reason is invalid")
        for key in user_fields - {"mapping_username", "status", "reason"}:
            if not _nonnegative_int(item[key]):
                raise RetentionError("file cleanup user counters are invalid")


def _history_totals(journals: Iterable[Journal]) -> tuple[int, int]:
    files = 0
    allocated_bytes = 0
    for journal in journals:
        journal_files, journal_bytes = journal.staged_totals()
        files += journal_files
        allocated_bytes += journal_bytes
    return files, allocated_bytes


def _publish_run_status(
    result: RunResult,
    *,
    config: RetentionConfig,
    journals: Iterable[Journal],
    status_path: Path,
    status_group: str,
) -> None:
    history_files, history_bytes = _history_totals(journals)
    write_cleanup_status(
        _status_payload(
            result,
            config=config,
            history_files=history_files,
            history_bytes=history_bytes,
        ),
        path=status_path,
        group_name=status_group,
    )


def _raw_mapping_home_is_symlink(raw_path: Path | None) -> bool:
    if raw_path is None:
        return False
    try:
        return stat.S_ISLNK(raw_path.lstat().st_mode)
    except OSError:
        return False


def _load_secure_mapping(path: Path) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise RetentionError("user mapping is unavailable") from exc
    try:
        value = os.fstat(fd)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != 0
            or value.st_mode & 0o027
            or value.st_size > MAX_MAPPING_BYTES
        ):
            raise RetentionError("user mapping ownership or permissions are unsafe")
        body = bytearray()
        while len(body) <= MAX_MAPPING_BYTES:
            chunk = os.read(fd, min(65536, MAX_MAPPING_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > MAX_MAPPING_BYTES:
            raise RetentionError("user mapping is too large")
        mapping = yaml.safe_load(body.decode("utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RetentionError("user mapping is invalid") from exc
    finally:
        os.close(fd)
    if not isinstance(mapping, dict):
        raise RetentionError("user mapping must be an object")
    return mapping


def _validate_mapping_file(path: Path) -> None:
    _load_secure_mapping(path)


def _prepare_identities(
    *,
    config: RetentionConfig,
    mounts: dict[FilesystemConfig, MountIdentity],
    mapping_path: Path,
) -> tuple[list[HermesTarget], dict[str, UserIdentity], dict[str, str]]:
    raw_mapping = _load_secure_mapping(mapping_path)
    mapping = resolve_env_placeholders(raw_mapping, str(mapping_path))
    targets = build_targets_from_config(mapping, resolve_env=False)
    raw_homes = _raw_home_paths(raw_mapping)
    identities: dict[str, UserIdentity] = {}
    failures: dict[str, str] = {}
    for target in targets:
        raw_home = raw_homes.get(target.username)
        if _raw_mapping_home_is_symlink(raw_home):
            failures[target.username] = "invalid_identity"
            continue
        try:
            identities[target.username] = validate_user_identity(
                target,
                config=config,
                mounts=mounts,
                raw_home_path=raw_home,
            )
        except UnsafeFilesystemError as exc:
            failures[target.username] = (
                "atime_unsupported"
                if "atime" in str(exc).casefold()
                else "invalid_identity"
            )
        except OSError:
            failures[target.username] = "invalid_identity"
    try:
        _ensure_unique_uids(list(identities.values()))
    except RetentionError:
        uid_counts: dict[int, int] = {}
        for identity in identities.values():
            uid_counts[identity.uid] = uid_counts.get(identity.uid, 0) + 1
        for username, identity in list(identities.items()):
            if uid_counts[identity.uid] > 1:
                failures[username] = "invalid_identity"
                identities.pop(username)
    return targets, identities, failures


def run_retention(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    mapping_path: Path = DEFAULT_MAPPING_PATH,
    status_path: Path = DEFAULT_STATUS_PATH,
    authorization_path: Path = DEFAULT_AUTHORIZATION_PATH,
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
    force_preview: bool = False,
    now: float | None = None,
    status_group: str = STATUS_GROUP,
) -> dict[str, Any]:
    require_root()
    started_at = time.time() if now is None else now
    monotonic_deadline = time.monotonic()
    config = load_retention_config(config_path)
    mode = "preview" if force_preview else config.mode
    fingerprint = policy_sha256(config)
    result = RunResult(
        run_id=str(uuid.uuid4()),
        mode=mode,
        started_at=started_at,
        policy_sha256=fingerprint,
    )
    journals: list[Journal] = []
    try:
        if not hasattr(os, "O_NOATIME") or not hasattr(os, "O_NOFOLLOW"):
            raise UnsafeFilesystemError("O_NOATIME and O_NOFOLLOW are required")
        mounts = {fs: validate_filesystem(fs) for fs in config.filesystems}
        if not _system_clock_synchronized():
            raise RetentionError("system clock is not synchronized")
        journals = [Journal(fs.history_root) for fs in config.filesystems]
        for journal in journals:
            journal.reconcile()
        existing_authorization = _authorization_payload(authorization_path)
        if (
            existing_authorization is not None
            and existing_authorization.get("policy_sha256") != fingerprint
        ):
            revoke_enforce(authorization_path)
        if mode == "enforce" and not enforce_is_authorized(
            config,
            authorization_path=authorization_path,
            now=started_at,
        ):
            raise RetentionError("enforce authorization is missing, stale, or invalid")
        monotonic_deadline += config.max_runtime_seconds
        _publish_run_status(
            result,
            config=config,
            journals=journals,
            status_path=status_path,
            status_group=status_group,
        )

        with acquire_mapping_lock(exclusive=False, lock_dir=lock_dir):
            targets, identities, failures = _prepare_identities(
                config=config,
                mounts=mounts,
                mapping_path=mapping_path,
            )
            journal_by_fs = {
                fs: journal
                for fs, journal in zip(config.filesystems, journals, strict=True)
            }
            global_scan_progress = {"visited": 0}
            result_by_username: dict[str, UserResult] = {}
            for target in targets:
                user_result = UserResult(mapping_username=target.username)
                result.users.append(user_result)
                result_by_username[target.username] = user_result
                if target.username in failures:
                    user_result.status = "error"
                    user_result.reason = failures[target.username]
                    user_result.errors = 1
                    _publish_run_status(
                        result,
                        config=config,
                        journals=journals,
                        status_path=status_path,
                        status_group=status_group,
                    )
                    continue
                identity = identities[target.username]
                journal = journal_by_fs[identity.filesystem]
                origin_id, new_origin, eligible_at = journal.origin_for(
                    identity, started_at
                )
                origin_run_id = str(uuid.uuid4())
                auto_preview = mode == "enforce" and (new_origin or eligible_at is None)
                user_mode = "preview" if auto_preview else mode
                journal.create_run(
                    run_id=origin_run_id,
                    origin_id=origin_id,
                    mode=user_mode,
                    fingerprint=fingerprint,
                    started_at=started_at,
                )
                if (
                    mode == "enforce"
                    and not auto_preview
                    and eligible_at is not None
                    and started_at < float(eligible_at)
                ):
                    user_result.status = "skipped"
                    user_result.reason = "new_origin_preview"
                    journal.finish_run(
                        run_id=origin_run_id,
                        origin_id=origin_id,
                        mode=user_mode,
                        status_value="partial",
                        finished_at=time.time(),
                    )
                    continue
                lock = None
                try:
                    lock = acquire_user_lock(
                        target.username,
                        exclusive=user_mode == "enforce",
                        blocking=False,
                        publish_marker=user_mode == "enforce",
                        run_id=result.run_id,
                        lock_dir=lock_dir,
                    )
                except BlockingIOError:
                    user_result.status = "skipped"
                    user_result.reason = "lock_busy"
                    journal.finish_run(
                        run_id=origin_run_id,
                        origin_id=origin_id,
                        mode=mode,
                        status_value="partial",
                        finished_at=time.time(),
                    )
                    continue
                user_run_status = "ok"
                with lock:
                    if user_mode == "enforce":
                        initial_activity = _activity_reason_or_failure(identity)
                        if initial_activity is not None:
                            user_result.status = "skipped"
                            user_result.reason = initial_activity
                            journal.finish_run(
                                run_id=origin_run_id,
                                origin_id=origin_id,
                                mode=user_mode,
                                status_value="partial",
                                finished_at=time.time(),
                            )
                            continue
                    cutoff_ns = int(
                        (started_at - config.inactive_days * DAY_SECONDS)
                        * 1_000_000_000
                    )
                    batch: list[FileCandidate] = []
                    try:
                        for candidate in scan_candidates(
                            identity,
                            cutoff_ns=cutoff_ns,
                            origin_id=origin_id,
                            journal=journal,
                            now=started_at,
                            max_files=config.max_files_per_user,
                            deadline=monotonic_deadline,
                            global_progress=global_scan_progress,
                            max_files_total=config.max_files_total,
                            activity_check=(
                                lambda: _activity_reason_or_failure(identity)
                            )
                            if user_mode == "enforce"
                            else None,
                        ):
                            user_result.candidate_files += 1
                            user_result.candidate_bytes += candidate.allocated_bytes
                            if user_mode == "enforce":
                                batch.append(candidate)
                                if len(batch) >= BATCH_SIZE:
                                    activity = _activity_reason_or_failure(identity)
                                    if activity is not None:
                                        user_result.status = "partial"
                                        user_result.reason = activity
                                        user_run_status = "partial"
                                        break
                                    staged_files, staged_bytes, empty_dirs = (
                                        stage_batch(
                                            identity,
                                            origin_id=origin_id,
                                            run_id=origin_run_id,
                                            candidates=batch,
                                            journal=journal,
                                            now=time.time(),
                                        )
                                    )
                                    user_result.staged_files += staged_files
                                    user_result.staged_bytes += staged_bytes
                                    remove_batch_empty_directories(
                                        identity,
                                        origin_id=origin_id,
                                        journal=journal,
                                        relative_dirs=empty_dirs,
                                    )
                                    batch.clear()
                                    _publish_run_status(
                                        result,
                                        config=config,
                                        journals=journals,
                                        status_path=status_path,
                                        status_group=status_group,
                                    )
                        if user_mode == "enforce" and batch and user_run_status == "ok":
                            activity = _activity_reason_or_failure(identity)
                            if activity is None:
                                staged_files, staged_bytes, empty_dirs = stage_batch(
                                    identity,
                                    origin_id=origin_id,
                                    run_id=origin_run_id,
                                    candidates=batch,
                                    journal=journal,
                                    now=time.time(),
                                )
                                user_result.staged_files += staged_files
                                user_result.staged_bytes += staged_bytes
                                remove_batch_empty_directories(
                                    identity,
                                    origin_id=origin_id,
                                    journal=journal,
                                    relative_dirs=empty_dirs,
                                )
                            else:
                                user_result.status = "partial"
                                user_result.reason = activity
                                user_run_status = "partial"
                    except UserBecameActive as exc:
                        user_result.status = "partial"
                        user_result.reason = exc.reason
                        user_run_status = "partial"
                    except (OverflowError, TimeoutError):
                        user_result.status = "partial"
                        user_result.reason = "limit_reached"
                        user_run_status = "partial"
                    except Exception:
                        user_result.status = "error"
                        user_result.reason = "journal_error"
                        user_result.errors += 1
                        user_run_status = "failed"
                if auto_preview and user_run_status == "ok":
                    user_result.status = "skipped"
                    user_result.reason = "new_origin_preview"
                journal.finish_run(
                    run_id=origin_run_id,
                    origin_id=origin_id,
                    mode=user_mode,
                    status_value=user_run_status,
                    finished_at=time.time(),
                )
                _publish_run_status(
                    result,
                    config=config,
                    journals=journals,
                    status_path=status_path,
                    status_group=status_group,
                )

            purge_cutoff = started_at - config.history_days * DAY_SECONDS
            for journal in journals:
                purge_due_entries(
                    journal,
                    cutoff=purge_cutoff,
                    identities=identities,
                    mode=mode,
                    now=started_at,
                    result_by_username=result_by_username,
                    lock_dir=lock_dir,
                )

        if any(item.status == "error" for item in result.users):
            result.status = "partial"
        elif any(item.status in {"partial", "skipped"} for item in result.users):
            result.status = "partial"
        else:
            result.status = "ok"
        result.finished_at = time.time()
    except Exception:
        result.status = "failed"
        result.finished_at = time.time()
        if mode == "enforce":
            with contextlib.suppress(OSError):
                revoke_enforce(authorization_path)
        if journals:
            with contextlib.suppress(Exception):
                _publish_run_status(
                    result,
                    config=config,
                    journals=journals,
                    status_path=status_path,
                    status_group=status_group,
                )
        raise
    _publish_run_status(
        result,
        config=config,
        journals=journals,
        status_path=status_path,
        status_group=status_group,
    )
    return read_cleanup_status(status_path, require_secure_owner=False)


def validate_installation(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    mapping_path: Path = DEFAULT_MAPPING_PATH,
) -> dict[str, Any]:
    require_root()
    config = load_retention_config(config_path)
    if not hasattr(os, "O_NOATIME") or getattr(_LIBC, "renameat2", None) is None:
        raise UnsafeFilesystemError(
            "required kernel retention capabilities are unavailable"
        )
    if not _system_clock_synchronized():
        raise RetentionError("system clock is not synchronized")
    _validate_status_directory(DEFAULT_STATUS_PATH.parent)
    mounts = {fs: validate_filesystem(fs) for fs in config.filesystems}
    with acquire_mapping_lock(exclusive=False):
        targets, identities, failures = _prepare_identities(
            config=config,
            mounts=mounts,
            mapping_path=mapping_path,
        )
    return {
        "ok": not failures and len(identities) == len(targets),
        "mode": config.mode,
        "policy_sha256": policy_sha256(config),
        "mapped_users": len(targets),
        "validated_users": len(identities),
        "invalid_users": len(failures),
        "filesystems": len(mounts),
    }


def require_root() -> None:
    if os.geteuid() != 0:
        raise RetentionError("this command must run as root")


def _restore_relpath(value: str | None) -> bytes | None:
    if value is None:
        return None
    if value.startswith("/"):
        raise RetentionError("restore path must be home-relative")
    encoded = os.fsencode(value)
    _path_parts(encoded)
    return encoded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Potato Agent two-stage user data retention"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("preview")
    subparsers.add_parser("run")
    authorize = subparsers.add_parser("authorize-enforce")
    authorize.add_argument("--confirm", action="store_true")
    subparsers.add_parser("revoke-enforce")
    restore = subparsers.add_parser("restore")
    restore.add_argument("--origin", required=True)
    restore.add_argument("--run", required=True)
    restore.add_argument("--path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    require_root()
    try:
        operation_lock = acquire_retention_lock(blocking=False)
    except BlockingIOError as exc:
        raise RetentionError("another retention operation is already running") from exc
    with operation_lock:
        if args.command == "validate":
            payload = validate_installation()
        elif args.command == "preview":
            payload = run_retention(force_preview=True)
        elif args.command == "run":
            payload = run_retention()
        elif args.command == "authorize-enforce":
            payload = authorize_enforce(
                load_retention_config(), confirm=bool(args.confirm)
            )
        elif args.command == "revoke-enforce":
            payload = {"revoked": revoke_enforce()}
        elif args.command == "restore":
            payload = restore_entries(
                config=load_retention_config(),
                origin_id=str(args.origin),
                run_id=str(args.run),
                relpath=_restore_relpath(args.path),
            )
        else:
            raise RetentionError("unsupported command")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

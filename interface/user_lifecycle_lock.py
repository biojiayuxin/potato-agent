from __future__ import annotations

import contextlib
import contextvars
import fcntl
import grp
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


DEFAULT_LIFECYCLE_LOCK_DIR = Path(
    os.getenv("POTATO_AGENT_LIFECYCLE_LOCK_DIR") or "/run/potato-agent/user-lifecycle"
)
LIFECYCLE_GROUP = "potato-interface"
MAINTENANCE_ERROR_CODE = "maintenance"
MAINTENANCE_ERROR_MARKER = "POTATO_USER_MAINTENANCE"
MAINTENANCE_ERROR_MESSAGE = (
    "User files are temporarily unavailable during maintenance; retry later."
)
_MAPPING_LOCK_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "potato_mapping_lock_depth", default=0
)


class UserMaintenanceError(RuntimeError):
    """A controlled user entry point raced with retention maintenance."""


@dataclass
class LifecycleLock:
    handle: object
    marker_path: Path | None = None

    @property
    def fd(self) -> int:
        return self.handle.fileno()  # type: ignore[no-any-return, union-attr]

    def make_inheritable(self) -> None:
        os.set_inheritable(self.fd, True)

    def close(self) -> None:
        marker_path = self.marker_path
        self.marker_path = None
        if marker_path is not None:
            with contextlib.suppress(FileNotFoundError):
                marker_path.unlink()
        handle = self.handle
        self.handle = None
        if handle is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def __enter__(self) -> LifecycleLock:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


def _lock_key(mapping_username: str) -> str:
    normalized = str(mapping_username or "").strip()
    if not normalized:
        raise ValueError("mapping username is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ensure_lock_dir(lock_dir: Path) -> None:
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    try:
        os.chmod(lock_dir, 0o750)
        if os.geteuid() == 0:
            os.chown(lock_dir, 0, grp.getgrnam(LIFECYCLE_GROUP).gr_gid)
    except PermissionError:
        # An unprivileged Interface process may use an already provisioned dir.
        pass


def _open_lock(path: Path) -> object:
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o640)
    if os.geteuid() == 0:
        try:
            os.fchown(fd, 0, grp.getgrnam(LIFECYCLE_GROUP).gr_gid)
            os.fchmod(fd, 0o640)
        except KeyError:
            os.close(fd)
            raise RuntimeError("lifecycle lock group is unavailable")
    return os.fdopen(fd, "r+", encoding="ascii")


def acquire_mapping_lock(
    *,
    exclusive: bool,
    blocking: bool = True,
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
) -> LifecycleLock:
    _ensure_lock_dir(lock_dir)
    handle = _open_lock(lock_dir / "mapping.lock")
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), operation)
    except BaseException:
        handle.close()
        raise
    return LifecycleLock(handle=handle)


def acquire_retention_lock(
    *,
    blocking: bool = False,
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
) -> LifecycleLock:
    _ensure_lock_dir(lock_dir)
    handle = _open_lock(lock_dir / "retention.lock")
    operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(handle.fileno(), operation)
    except BaseException:
        handle.close()
        raise
    return LifecycleLock(handle=handle)


def acquire_user_lock(
    mapping_username: str,
    *,
    exclusive: bool,
    blocking: bool = True,
    publish_marker: bool = False,
    run_id: str = "",
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
) -> LifecycleLock:
    _ensure_lock_dir(lock_dir)
    key = _lock_key(mapping_username)
    handle = _open_lock(lock_dir / f"user-{key}.lock")
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), operation)
    except BaseException:
        handle.close()
        raise

    marker_path: Path | None = None
    if exclusive and publish_marker:
        marker_path = lock_dir / f"user-{key}.maintenance"
        temporary_path = lock_dir / f".{marker_path.name}.{os.getpid()}.tmp"
        payload = json.dumps(
            {"schema_version": 1, "run_id": run_id, "started_at": int(time.time())},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        fd = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o640,
        )
        try:
            os.write(fd, payload.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary_path, marker_path)
    return LifecycleLock(handle=handle, marker_path=marker_path)


@contextlib.contextmanager
def mapping_lifecycle_lock(
    *,
    exclusive: bool,
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
) -> Iterator[None]:
    with acquire_mapping_lock(exclusive=exclusive, lock_dir=lock_dir):
        token = _MAPPING_LOCK_DEPTH.set(_MAPPING_LOCK_DEPTH.get() + 1)
        try:
            yield
        finally:
            _MAPPING_LOCK_DEPTH.reset(token)


def mapping_lock_held() -> bool:
    return _MAPPING_LOCK_DEPTH.get() > 0


@contextlib.contextmanager
def user_lifecycle_lock(
    mapping_username: str,
    *,
    exclusive: bool,
    publish_marker: bool = False,
    run_id: str = "",
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
) -> Iterator[None]:
    with acquire_user_lock(
        mapping_username,
        exclusive=exclusive,
        publish_marker=publish_marker,
        run_id=run_id,
        lock_dir=lock_dir,
    ):
        yield


def acquire_user_entry_lock(
    mapping_username: str,
    *,
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
) -> LifecycleLock:
    try:
        return acquire_user_lock(
            mapping_username,
            exclusive=False,
            blocking=False,
            lock_dir=lock_dir,
        )
    except BlockingIOError as exc:
        raise UserMaintenanceError(MAINTENANCE_ERROR_MESSAGE) from exc


def user_maintenance_active(
    mapping_username: str,
    *,
    lock_dir: Path = DEFAULT_LIFECYCLE_LOCK_DIR,
) -> bool:
    try:
        lock = acquire_user_entry_lock(mapping_username, lock_dir=lock_dir)
    except UserMaintenanceError:
        return True
    lock.close()
    return False

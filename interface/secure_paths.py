from __future__ import annotations

import os
from pathlib import Path


DEFAULT_STATE_DIR = Path(os.getenv("POTATO_AGENT_STATE_DIR") or "/var/lib/potato-agent")
DEFAULT_PRIVATE_DIR_MODE = 0o750
DEFAULT_PRIVATE_WRITABLE_DIR_MODE = 0o700
DEFAULT_PRIVATE_FILE_MODE = 0o600
DEFAULT_MAPPING_FILE_MODE = 0o640


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except PermissionError:
        # A non-root service may be able to use an already-provisioned path but
        # not chmod a root-owned parent. Deployment should set the mode.
        return


def ensure_private_directory(
    path: Path, *, mode: int = DEFAULT_PRIVATE_DIR_MODE
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(path, mode)
    return path


def _chown_to_parent_owner_if_root(path: Path) -> None:
    if os.geteuid() != 0 or not path.exists():
        return
    try:
        parent_stat = path.parent.stat()
        os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
    except PermissionError:
        return


def ensure_private_file(path: Path, *, mode: int = DEFAULT_PRIVATE_FILE_MODE) -> Path:
    if path.exists():
        _chown_to_parent_owner_if_root(path)
        _chmod_best_effort(path, mode)
    return path


def ensure_sqlite_sidecar_modes(
    db_path: Path, *, mode: int = DEFAULT_PRIVATE_FILE_MODE
) -> None:
    for candidate in (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    ):
        ensure_private_file(candidate, mode=mode)

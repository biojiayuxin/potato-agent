#!/usr/bin/env python3
"""Create a private, consistent backup of deployment data used by cutover."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml


SCHEMA_VERSION = 1
MIN_FREE_SPACE_MARGIN = 16 * 1024 * 1024
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_STABLE_STAT_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_gid",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


class BackupError(RuntimeError):
    pass


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _stable_signature(info: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(info, field) for field in _STABLE_STAT_FIELDS)


def _source_metadata(path: Path, info: os.stat_result) -> dict[str, Any]:
    return {
        "path": str(path),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def _assert_real_components(path: Path, *, label: str) -> None:
    path = _absolute(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise BackupError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BackupError(f"{label} contains a symbolic link")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise BackupError(f"{label} has a non-directory parent")


def _regular_source(path: Path, *, label: str) -> os.stat_result:
    path = _absolute(path)
    _assert_real_components(path, label=label)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BackupError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode):
        raise BackupError(f"{label} is not a regular file")
    return info


def _real_directory(path: Path, *, label: str) -> os.stat_result:
    path = _absolute(path)
    _assert_real_components(path, label=label)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BackupError(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise BackupError(f"{label} is not a directory")
    return info


def _read_regular_stable(
    path: Path, *, label: str
) -> tuple[bytes, os.stat_result]:
    path = _absolute(path)
    expected = _regular_source(path, label=label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupError(f"{label} cannot be opened safely") from exc
    chunks: list[bytes] = []
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BackupError(f"{label} is not a regular file")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise BackupError(f"{label} cannot be read") from exc
    finally:
        os.close(descriptor)
    if (
        _stable_signature(expected) != _stable_signature(before)
        or _stable_signature(before) != _stable_signature(after)
    ):
        raise BackupError(f"{label} changed while it was read")
    return b"".join(chunks), after


def _load_mapping(path: Path) -> tuple[dict[str, Any], os.stat_result]:
    content, info = _read_regular_stable(path, label="mapping")
    try:
        value = yaml.safe_load(content.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BackupError("mapping YAML is invalid") from exc
    if not isinstance(value, dict):
        raise BackupError("mapping YAML must contain an object")
    return value, info


def _mapped_state_sources(
    mapping: Mapping[str, Any],
) -> list[tuple[int, Path, os.stat_result]]:
    raw_users = mapping.get("users") or []
    if not isinstance(raw_users, list):
        raise BackupError("mapping users must be a list")
    if not raw_users:
        raise BackupError("mapping does not contain any users")

    sources: list[tuple[int, Path, os.stat_result]] = []
    identities: set[tuple[int, int]] = set()
    lexical_paths: set[Path] = set()
    for index, raw_user in enumerate(raw_users):
        if not isinstance(raw_user, dict):
            raise BackupError(f"mapping user {index} must be an object")
        linux_user = str(
            raw_user.get("linux_user") or f"hmx_{raw_user.get('username') or index}"
        ).strip()
        home_dir = str(raw_user.get("home_dir") or f"/home/{linux_user}").strip()
        raw_hermes_home = str(
            raw_user.get("hermes_home") or (Path(home_dir) / ".hermes")
        ).strip()
        hermes_home = Path(raw_hermes_home)
        if not hermes_home.is_absolute():
            raise BackupError(f"mapping user {index} has a non-absolute Hermes home")
        source = _absolute(hermes_home / "state.db")
        info = _regular_source(source, label=f"mapped state database {index}")
        identity = (info.st_dev, info.st_ino)
        if source in lexical_paths or identity in identities:
            raise BackupError("mapped users share a state database")
        lexical_paths.add(source)
        identities.add(identity)
        sources.append((index, source, info))
    return sources


def _database_estimated_size(
    path: Path, info: os.stat_result, *, label: str
) -> int:
    total = info.st_size
    for suffix in SQLITE_SIDECAR_SUFFIXES:
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar_info = os.lstat(sidecar)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupError(f"{label} sidecar is unavailable") from exc
        _assert_real_components(sidecar, label=f"{label} sidecar")
        if not stat.S_ISREG(sidecar_info.st_mode):
            raise BackupError(f"{label} sidecar is not a regular file")
        total += sidecar_info.st_size
    return total


def _fsync_file(path: Path, *, label: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise BackupError(f"{label} could not be synchronized") from exc


def _fsync_directory(path: Path, *, label: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise BackupError(f"{label} could not be synchronized") from exc


def _mkdir_private(path: Path, *, label: str) -> None:
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700, follow_symlinks=False)
    except OSError as exc:
        raise BackupError(f"{label} could not be created") from exc
    _fsync_directory(path.parent, label=f"{label} parent")


def _write_private(path: Path, content: bytes, *, label: str) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise BackupError(f"{label} could not be written") from exc
    _fsync_directory(path.parent, label=f"{label} parent")


def _backup_database(
    source: Path,
    destination: Path,
    *,
    label: str,
    expected_info: os.stat_result,
) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise BackupError(f"{label} destination already exists")
    current_info = _regular_source(source, label=label)
    if _stable_signature(current_info) != _stable_signature(expected_info):
        raise BackupError(f"{label} changed before backup")

    try:
        source_uri = f"{source.as_uri()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as source_conn:
            source_conn.execute("PRAGMA query_only = ON")
            with sqlite3.connect(str(destination)) as destination_conn:
                source_conn.backup(destination_conn)
                destination_conn.execute("PRAGMA journal_mode = DELETE")
                rows = destination_conn.execute("PRAGMA integrity_check").fetchall()
                if rows != [("ok",)]:
                    raise BackupError(f"{label} backup failed integrity_check")
    except BackupError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"{label} SQLite backup or validation failed") from exc

    try:
        after_info = os.lstat(source)
    except OSError as exc:
        raise BackupError(f"{label} disappeared during backup") from exc
    if _stable_signature(after_info) != _stable_signature(expected_info):
        raise BackupError(f"{label} changed during backup")
    try:
        os.chmod(destination, 0o600, follow_symlinks=False)
        backup_info = os.lstat(destination)
    except OSError as exc:
        raise BackupError(f"{label} backup metadata could not be secured") from exc
    if not stat.S_ISREG(backup_info.st_mode):
        raise BackupError(f"{label} backup is not a regular file")
    _fsync_file(destination, label=f"{label} backup")
    _fsync_directory(destination.parent, label=f"{label} backup directory")
    return {
        "size": backup_info.st_size,
        "mode": "0600",
        "integrity_check": "ok",
    }


def create_backup(
    *,
    mapping_path: Path,
    destination: Path,
    interface_db: Path,
    archive_db: Path,
    chat_shares_db: Path,
    model_proxy_config: Path,
    usage_db: Path,
) -> dict[str, Any]:
    destination = _absolute(destination)
    marker = destination.parent / f"{destination.name}.complete"
    _real_directory(destination.parent, label="backup parent")
    if destination.is_symlink():
        raise BackupError("backup destination is a symbolic link")
    if destination.exists():
        raise BackupError("backup destination already exists")
    if marker.is_symlink():
        raise BackupError("backup completion marker is a symbolic link")
    if marker.exists():
        raise BackupError("backup completion marker already exists")

    mapping_path = _absolute(mapping_path)
    mapping, mapping_info = _load_mapping(mapping_path)
    state_sources = _mapped_state_sources(mapping)
    fixed_sources = [
        ("interface", _absolute(interface_db), Path("interface/interface.db")),
        ("archive", _absolute(archive_db), Path("interface/archive.db")),
        ("chat_shares", _absolute(chat_shares_db), Path("interface/chat_shares.db")),
        ("model_proxy_usage", _absolute(usage_db), Path("model-proxy/usage.db")),
    ]
    database_sources: list[tuple[str, int | None, Path, Path, os.stat_result]] = []
    for role, source, relative in fixed_sources:
        info = _regular_source(source, label=f"{role} database")
        database_sources.append((role, None, source, relative, info))
    for mapping_index, source, info in state_sources:
        database_sources.append(
            (
                "user_state",
                mapping_index,
                source,
                Path(f"users/{mapping_index:04d}/state.db"),
                info,
            )
        )

    config_path = _absolute(model_proxy_config)
    config_content, config_info = _read_regular_stable(
        config_path, label="model proxy configuration"
    )
    required_bytes = len(config_content)
    for role, mapping_index, source, _relative, info in database_sources:
        label = (
            f"mapped state database {mapping_index}"
            if mapping_index is not None
            else f"{role} database"
        )
        required_bytes += _database_estimated_size(source, info, label=label)
    try:
        free_bytes = shutil.disk_usage(destination.parent).free
    except OSError as exc:
        raise BackupError("backup free space cannot be determined") from exc
    margin = max(required_bytes // 10, MIN_FREE_SPACE_MARGIN)
    if free_bytes < required_bytes + margin:
        raise BackupError("insufficient free space for sensitive-state backup")

    _mkdir_private(destination, label="backup destination")
    interface_dir = destination / "interface"
    users_dir = destination / "users"
    model_proxy_dir = destination / "model-proxy"
    for path, label in (
        (interface_dir, "Interface backup directory"),
        (users_dir, "user backup directory"),
        (model_proxy_dir, "model proxy backup directory"),
    ):
        _mkdir_private(path, label=label)
    for mapping_index, _source, _info in state_sources:
        _mkdir_private(
            users_dir / f"{mapping_index:04d}",
            label=f"mapped state backup directory {mapping_index}",
        )

    manifest_databases: list[dict[str, Any]] = []
    for role, mapping_index, source, relative, source_info in database_sources:
        label = (
            f"mapped state database {mapping_index}"
            if mapping_index is not None
            else f"{role} database"
        )
        backup_record = _backup_database(
            source,
            destination / relative,
            label=label,
            expected_info=source_info,
        )
        record: dict[str, Any] = {
            "role": role,
            "source": _source_metadata(source, source_info),
            "backup": {
                **backup_record,
                "path": str(relative),
            },
        }
        if mapping_index is not None:
            record["mapping_index"] = mapping_index
        manifest_databases.append(record)

    config_relative = Path("model-proxy/model_proxy.yaml")
    _write_private(
        destination / config_relative,
        config_content,
        label="model proxy configuration backup",
    )
    config_sha256 = hashlib.sha256(config_content).hexdigest()
    try:
        current_mapping_info = os.lstat(mapping_path)
    except OSError as exc:
        raise BackupError("mapping disappeared during backup") from exc
    if _stable_signature(current_mapping_info) != _stable_signature(mapping_info):
        raise BackupError("mapping changed during backup")
    current_config, current_config_info = _read_regular_stable(
        config_path, label="model proxy configuration"
    )
    if (
        _stable_signature(current_config_info) != _stable_signature(config_info)
        or current_config != config_content
    ):
        raise BackupError("model proxy configuration changed during backup")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mapping": _source_metadata(mapping_path, mapping_info),
        "databases": manifest_databases,
        "files": [
            {
                "role": "model_proxy_config",
                "source": _source_metadata(config_path, config_info),
                "backup": {
                    "path": str(config_relative),
                    "size": len(config_content),
                    "mode": "0600",
                    "sha256": config_sha256,
                },
            }
        ],
        "totals": {
            "sqlite_databases": len(database_sources),
            "mapped_user_databases": len(state_sources),
            "configuration_files": 1,
        },
    }
    manifest_content = (
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    _write_private(
        destination / "manifest.json", manifest_content, label="backup manifest"
    )
    for directory in (interface_dir, users_dir, model_proxy_dir, destination):
        _fsync_directory(directory, label="backup directory")
    _write_private(marker, b"complete\n", label="backup completion marker")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--interface-db", type=Path, required=True)
    parser.add_argument("--archive-db", type=Path, required=True)
    parser.add_argument("--chat-shares-db", type=Path, required=True)
    parser.add_argument("--model-proxy-config", type=Path, required=True)
    parser.add_argument("--usage-db", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.geteuid() != 0:
        print("error: sensitive-state backup must run as root", file=sys.stderr)
        return 2
    try:
        manifest = create_backup(
            mapping_path=args.mapping,
            destination=args.destination,
            interface_db=args.interface_db,
            archive_db=args.archive_db,
            chat_shares_db=args.chat_shares_db,
            model_proxy_config=args.model_proxy_config,
            usage_db=args.usage_db,
        )
    except BackupError as exc:
        print(f"error: sensitive-state backup failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "error: sensitive-state backup failed: unexpected internal error",
            file=sys.stderr,
        )
        return 1
    totals = manifest["totals"]
    print(
        "sensitive-state backup complete: "
        f"{totals['sqlite_databases']} databases "
        f"({totals['mapped_user_databases']} mapped user databases), "
        f"{totals['configuration_files']} configuration file"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

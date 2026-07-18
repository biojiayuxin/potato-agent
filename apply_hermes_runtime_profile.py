#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import os
import pwd
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from interface.hermes_profile import (
    HermesRuntimeProfile,
    HermesRuntimeProfileError,
    apply_runtime_profile,
    load_runtime_profile,
    runtime_profile_path,
)
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore


@dataclass(frozen=True)
class UserProfileResult:
    username: str
    config_path: Path
    changed: bool
    applied: bool = False
    backup_path: Path | None = None
    error: str = ""


@dataclass(frozen=True)
class ConfigSnapshot:
    path: Path
    directory_path: Path
    directory_fd: int
    config_fd: int
    directory_identity: tuple[int, int]
    original: bytes
    original_stat: os.stat_result
    data: dict[str, Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check or apply the sealed Potato Hermes runtime profile to existing "
            "user config.yaml files. The default mode is read-only --check."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check for profile drift (default).")
    mode.add_argument("--apply", action="store_true", help="Back up and atomically patch config.yaml.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--user", help="Limit the operation to one mapping username.")
    scope.add_argument("--all", action="store_true", help="Operate on every mapped user.")
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING_PATH,
        help=f"Path to users_mapping.yaml (default: {DEFAULT_MAPPING_PATH})",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        help=(
            "Runtime profile source. Defaults to HERMES_RUNTIME_PROFILE_PATH when "
            "set, otherwise packaging/hermes/runtime-profile.yaml."
        ),
    )
    return parser


def _parse_config(content: bytes, path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(content.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RuntimeError(f"unable to read config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"config must contain a top-level mapping: {path}")
    return data


def _yaml_bytes(data: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=False,
    ).encode("utf-8")


def _write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _absolute_path(path: Path, field: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise RuntimeError(f"{field} must be an absolute path: {path}")
    return Path(os.path.abspath(expanded))


def _open_absolute_directory_no_symlinks(path: Path) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    current_fd = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _file_version(file_stat: os.stat_result) -> tuple[int, ...]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_uid,
        file_stat.st_gid,
        file_stat.st_nlink,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _validate_config_stat(
    file_stat: os.stat_result,
    *,
    path: Path,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"config must be a regular file: {path}")
    if (file_stat.st_uid, file_stat.st_gid) != (expected_uid, expected_gid):
        raise RuntimeError(
            f"config owner must be {expected_uid}:{expected_gid}, got "
            f"{file_stat.st_uid}:{file_stat.st_gid}: {path}"
        )
    if file_stat.st_nlink != 1:
        raise RuntimeError(
            f"config must have exactly one hard link, got {file_stat.st_nlink}: {path}"
        )


@contextlib.contextmanager
def _open_config_snapshot(target: HermesTarget) -> Iterator[ConfigSnapshot]:
    home_path = _absolute_path(target.home_dir, "target.home_dir")
    directory_path = _absolute_path(target.hermes_home, "target.hermes_home")
    config_path = directory_path / "config.yaml"
    try:
        directory_path.relative_to(home_path)
    except ValueError as exc:
        raise RuntimeError(
            f"config path must remain inside target.home_dir {home_path}: {config_path}"
        ) from exc

    try:
        account = pwd.getpwnam(target.linux_user)
    except KeyError as exc:
        raise RuntimeError(f"Linux user {target.linux_user!r} does not exist") from exc

    try:
        directory_fd = _open_absolute_directory_no_symlinks(directory_path)
    except OSError as exc:
        raise RuntimeError(
            f"refusing config path with missing or symlinked ancestor: {config_path}: {exc}"
        ) from exc

    config_fd = -1
    try:
        directory_stat = os.fstat(directory_fd)
        config_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            config_fd = os.open("config.yaml", config_flags, dir_fd=directory_fd)
        except FileNotFoundError as exc:
            raise RuntimeError(f"config file not found: {config_path}") from exc
        except OSError as exc:
            raise RuntimeError(
                f"refusing symlinked or unreadable config: {config_path}: {exc}"
            ) from exc

        original_stat = os.fstat(config_fd)
        _validate_config_stat(
            original_stat,
            path=config_path,
            expected_uid=account.pw_uid,
            expected_gid=account.pw_gid,
        )
        original = _read_all(config_fd)
        after_read_stat = os.fstat(config_fd)
        if _file_version(after_read_stat) != _file_version(original_stat):
            raise RuntimeError(f"config changed while it was being read: {config_path}")
        data = _parse_config(original, config_path)
        yield ConfigSnapshot(
            path=config_path,
            directory_path=directory_path,
            directory_fd=directory_fd,
            config_fd=config_fd,
            directory_identity=(directory_stat.st_dev, directory_stat.st_ino),
            original=original,
            original_stat=original_stat,
            data=data,
        )
    finally:
        if config_fd >= 0:
            os.close(config_fd)
        os.close(directory_fd)


def _assert_directory_still_bound(snapshot: ConfigSnapshot) -> None:
    try:
        current_fd = _open_absolute_directory_no_symlinks(snapshot.directory_path)
    except OSError as exc:
        raise RuntimeError(
            f"config directory path changed before commit: {snapshot.directory_path}"
        ) from exc
    try:
        current = os.fstat(current_fd)
    finally:
        os.close(current_fd)
    if (current.st_dev, current.st_ino) != snapshot.directory_identity:
        raise RuntimeError(
            f"config directory path changed before commit: {snapshot.directory_path}"
        )


def _assert_snapshot_current(snapshot: ConfigSnapshot) -> None:
    try:
        current = os.stat(
            "config.yaml",
            dir_fd=snapshot.directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"config was removed before commit: {snapshot.path}") from exc
    if _file_version(current) != _file_version(snapshot.original_stat):
        raise RuntimeError(f"config changed before commit: {snapshot.path}")
    opened_before = os.fstat(snapshot.config_fd)
    if _file_version(opened_before) != _file_version(snapshot.original_stat):
        raise RuntimeError(f"config changed before commit: {snapshot.path}")
    os.lseek(snapshot.config_fd, 0, os.SEEK_SET)
    current_content = _read_all(snapshot.config_fd)
    opened_after = os.fstat(snapshot.config_fd)
    if (
        current_content != snapshot.original
        or _file_version(opened_after) != _file_version(snapshot.original_stat)
    ):
        raise RuntimeError(f"config changed before commit: {snapshot.path}")


def _create_exclusive_file(
    directory_fd: int,
    name: str,
    *,
    mode: int,
) -> int:
    return os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=directory_fd,
    )


def _create_backup_file(snapshot: ConfigSnapshot, timestamp: str) -> tuple[int, str]:
    base_name = f"config.yaml.bak.runtime-profile.{timestamp}"
    file_mode = stat.S_IMODE(snapshot.original_stat.st_mode)
    suffix = 0
    while True:
        name = base_name if suffix == 0 else f"{base_name}.{suffix}"
        try:
            return (
                _create_exclusive_file(
                    snapshot.directory_fd,
                    name,
                    mode=file_mode,
                ),
                name,
            )
        except FileExistsError:
            suffix += 1


def atomic_backup_and_write(
    snapshot: ConfigSnapshot,
    content: bytes,
    *,
    timestamp: str | None = None,
) -> Path:
    _assert_directory_still_bound(snapshot)
    _assert_snapshot_current(snapshot)
    original_stat = snapshot.original_stat
    file_mode = stat.S_IMODE(original_stat.st_mode)
    timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if re.fullmatch(r"\d{8}T\d{6}Z", timestamp) is None:
        raise RuntimeError(f"invalid backup timestamp: {timestamp!r}")
    backup_fd, backup_name = _create_backup_file(snapshot, timestamp)
    backup_created = True
    temp_fd = -1
    temp_name = f".config.yaml.runtime-profile.{secrets.token_hex(16)}"
    replaced = False
    try:
        os.fchown(backup_fd, original_stat.st_uid, original_stat.st_gid)
        os.fchmod(backup_fd, file_mode)
        _write_all(backup_fd, snapshot.original)
        os.fsync(backup_fd)
        os.close(backup_fd)
        backup_fd = -1

        temp_fd = _create_exclusive_file(
            snapshot.directory_fd,
            temp_name,
            mode=file_mode,
        )
        os.fchown(temp_fd, original_stat.st_uid, original_stat.st_gid)
        os.fchmod(temp_fd, file_mode)
        _write_all(temp_fd, content)
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1

        _assert_directory_still_bound(snapshot)
        _assert_snapshot_current(snapshot)
        os.replace(
            temp_name,
            "config.yaml",
            src_dir_fd=snapshot.directory_fd,
            dst_dir_fd=snapshot.directory_fd,
        )
        replaced = True
        os.fsync(snapshot.directory_fd)
    except Exception:
        if backup_fd >= 0:
            os.close(backup_fd)
        if temp_fd >= 0:
            os.close(temp_fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name, dir_fd=snapshot.directory_fd)
        if backup_created and not replaced:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(backup_name, dir_fd=snapshot.directory_fd)
        raise
    return snapshot.path.with_name(backup_name)


def process_target(
    target: HermesTarget,
    profile: HermesRuntimeProfile,
    *,
    apply: bool,
) -> UserProfileResult:
    config_path = target.hermes_home / "config.yaml"
    try:
        with _open_config_snapshot(target) as snapshot:
            existing = snapshot.data
            desired = apply_runtime_profile(existing, profile=profile)
            changed = desired != existing
            if not apply or not changed:
                return UserProfileResult(
                    username=target.username,
                    config_path=snapshot.path,
                    changed=changed,
                )
            backup_path = atomic_backup_and_write(
                snapshot,
                _yaml_bytes(desired),
            )
        return UserProfileResult(
            username=target.username,
            config_path=snapshot.path,
            changed=True,
            applied=True,
            backup_path=backup_path,
        )
    except (HermesRuntimeProfileError, OSError, RuntimeError) as exc:
        return UserProfileResult(
            username=target.username,
            config_path=config_path,
            changed=False,
            error=str(exc),
        )


def _select_targets(mapping_path: Path, username: str | None) -> list[HermesTarget]:
    store = MappingStore(mapping_path)
    config = store.load_config(resolve_env=True)
    raw_users = config.get("users") or []
    if not isinstance(raw_users, list):
        raise RuntimeError("mapping users must be a list")
    mapped_names: list[str] = []
    for index, raw_user in enumerate(raw_users):
        if not isinstance(raw_user, dict):
            raise RuntimeError(f"mapping users[{index}] must be a mapping")
        mapped_name = str(raw_user.get("username") or "").strip()
        if not mapped_name:
            raise RuntimeError(f"mapping users[{index}] is missing username")
        if mapped_name in mapped_names:
            raise RuntimeError(f"mapping contains duplicate username: {mapped_name}")
        mapped_names.append(mapped_name)

    targets = store.load_targets()
    loaded_names = {target.username for target in targets}
    skipped = [name for name in mapped_names if name not in loaded_names]
    if skipped:
        raise RuntimeError(
            "mapping users could not be loaded for migration (check api_port "
            "and api_key): " + ", ".join(skipped)
        )
    if username is None:
        return targets
    selected = [target for target in targets if target.username == username]
    if not selected:
        raise RuntimeError(f"mapping user not found: {username}")
    return selected


def _print_results(results: Iterable[UserProfileResult], *, apply: bool) -> bool:
    failed = False
    for result in results:
        if result.error:
            failed = True
            print(f"ERROR {result.username}: {result.error}")
        elif result.applied:
            print(
                f"APPLIED {result.username}: {result.config_path} "
                f"(backup: {result.backup_path})"
            )
        elif result.changed:
            failed = True
            print(f"DRIFT {result.username}: {result.config_path}")
        else:
            verb = "UNCHANGED" if apply else "OK"
            print(f"{verb} {result.username}: {result.config_path}")
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    apply = bool(args.apply)
    if apply and not (args.user or args.all):
        parser.error("--apply requires exactly one of --user USER or --all")
    if apply and os.geteuid() != 0:
        raise RuntimeError("--apply must run as root")

    profile = load_runtime_profile(args.profile or runtime_profile_path())
    targets = _select_targets(args.mapping, args.user)
    results = [process_target(target, profile, apply=apply) for target in targets]
    failed = _print_results(results, apply=apply)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

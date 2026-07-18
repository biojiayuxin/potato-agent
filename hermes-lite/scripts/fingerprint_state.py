#!/usr/bin/env python3
"""Capture and compare deployment-state fingerprints without following links."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml


SCHEMA_VERSION = 2
DEFAULT_MAPPING = Path("/var/lib/potato-agent/config/users_mapping.yaml")
DEFAULT_DATA_DIR = Path("/var/lib/potato-agent/data")
METADATA_ONLY_HERMES_SUBTREES = ("home",)
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


class FingerprintError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _absolute_no_follow(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _metadata(path: Path, info: os.stat_result, kind: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "type": kind,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "uid": info.st_uid,
        "gid": info.st_gid,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def _stable_signature(info: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(info, field) for field in _STABLE_STAT_FIELDS)


def _stable_regular_digest(
    path: Path, *, collect: bool = False
) -> tuple[str, bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FingerprintError(f"cannot open regular file without following links: {path}: {exc}") from exc
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FingerprintError(f"expected a regular file: {path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if collect:
                chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _stable_signature(before) != _stable_signature(after):
        raise FingerprintError(f"file changed while it was fingerprinted: {path}")
    return digest.hexdigest(), b"".join(chunks), after


def _entry(path: Path) -> dict[str, Any]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"path": str(path), "type": "missing"}
    mode = info.st_mode
    if stat.S_ISREG(mode):
        digest, _unused, stable_info = _stable_regular_digest(path)
        if (info.st_dev, info.st_ino) != (stable_info.st_dev, stable_info.st_ino):
            raise FingerprintError(f"file was replaced while it was fingerprinted: {path}")
        result = _metadata(path, stable_info, "regular")
        result["sha256"] = digest
        return result
    if stat.S_ISLNK(mode):
        result = _metadata(path, info, "symlink")
        try:
            result["target"] = os.readlink(path)
        except OSError as exc:
            raise FingerprintError(f"cannot read symlink metadata: {path}: {exc}") from exc
        if _stable_signature(os.lstat(path)) != _stable_signature(info):
            raise FingerprintError(f"symlink changed while it was fingerprinted: {path}")
        return result
    if stat.S_ISDIR(mode):
        return _metadata(path, info, "directory")
    special = (
        "fifo" if stat.S_ISFIFO(mode) else
        "socket" if stat.S_ISSOCK(mode) else
        "block" if stat.S_ISBLK(mode) else
        "character" if stat.S_ISCHR(mode) else
        "other"
    )
    return _metadata(path, info, special)


def _metadata_tree_record(root: Path) -> dict[str, Any]:
    try:
        root_info = os.lstat(root)
    except FileNotFoundError:
        return {"path": str(root), "type": "missing"}
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise FingerprintError(f"metadata-only root must be a real directory: {root}")

    digest = hashlib.sha256()
    counts = {
        "entries": 0,
        "directories": 0,
        "regular_files": 0,
        "symlinks": 0,
        "special_files": 0,
        "regular_bytes": 0,
    }

    def visit(path: Path, relative: str) -> None:
        try:
            before = os.lstat(path)
        except OSError as exc:
            raise FingerprintError(f"cannot stat metadata-only path: {path}: {exc}") from exc
        mode = before.st_mode
        if stat.S_ISDIR(mode):
            kind = "directory"
        elif stat.S_ISREG(mode):
            kind = "regular"
        elif stat.S_ISLNK(mode):
            kind = "symlink"
        elif stat.S_ISFIFO(mode):
            kind = "fifo"
        elif stat.S_ISSOCK(mode):
            kind = "socket"
        elif stat.S_ISBLK(mode):
            kind = "block"
        elif stat.S_ISCHR(mode):
            kind = "character"
        else:
            kind = "other"
        item = {
            "path": relative,
            "type": kind,
            "mode": f"{stat.S_IMODE(mode):04o}",
            "uid": before.st_uid,
            "gid": before.st_gid,
            "size": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "ctime_ns": before.st_ctime_ns,
        }
        if kind == "symlink":
            try:
                item["target"] = os.readlink(path)
            except OSError as exc:
                raise FingerprintError(
                    f"cannot read metadata-only symlink: {path}: {exc}"
                ) from exc

        counts["entries"] += 1
        if kind == "directory":
            counts["directories"] += 1
        elif kind == "regular":
            counts["regular_files"] += 1
            counts["regular_bytes"] += before.st_size
        elif kind == "symlink":
            counts["symlinks"] += 1
        else:
            counts["special_files"] += 1
        digest.update(_canonical_json(item))

        if kind == "directory":
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise FingerprintError(
                    f"cannot enumerate metadata-only directory: {path}: {exc}"
                ) from exc
            try:
                with os.scandir(descriptor) as iterator:
                    names = sorted(entry.name for entry in iterator)
            finally:
                os.close(descriptor)
            for name in names:
                child_relative = name if relative == "." else f"{relative}/{name}"
                visit(path / name, child_relative)

        try:
            after = os.lstat(path)
        except OSError as exc:
            raise FingerprintError(
                f"metadata-only path disappeared during fingerprint: {path}: {exc}"
            ) from exc
        if _stable_signature(before) != _stable_signature(after):
            raise FingerprintError(
                f"metadata-only path changed while it was fingerprinted: {path}"
            )

    visit(root, ".")
    record = _metadata(root, os.lstat(root), "metadata_tree")
    record.update(counts)
    record["tree_sha256"] = digest.hexdigest()
    record["verification"] = "metadata_only"
    return record


def _fingerprint_tree(
    root: Path, *, metadata_only_roots: tuple[Path, ...] = ()
) -> list[dict[str, Any]]:
    root = _absolute_no_follow(root)
    metadata_only_roots = frozenset(
        _absolute_no_follow(path) for path in metadata_only_roots
    )
    entries: list[dict[str, Any]] = []

    def visit(path: Path) -> None:
        if path in metadata_only_roots:
            entries.append(_metadata_tree_record(path))
            return
        record = _entry(path)
        entries.append(record)
        if record["type"] != "directory":
            return
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise FingerprintError(f"cannot enumerate directory without following links: {path}: {exc}") from exc
        try:
            with os.scandir(descriptor) as iterator:
                names = sorted(item.name for item in iterator)
        finally:
            os.close(descriptor)
        for name in names:
            visit(path / name)
        if _entry(path) != record:
            raise FingerprintError(f"directory changed while it was fingerprinted: {path}")

    visit(root)
    return entries


def _mapping_bytes(path: Path) -> tuple[dict[str, Any], bytes, str]:
    path = _absolute_no_follow(path)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise FingerprintError(f"cannot stat mapping file: {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise FingerprintError(f"mapping must be a regular file, not a symlink: {path}")
    digest, content, _stable_info = _stable_regular_digest(path, collect=True)
    try:
        value = yaml.safe_load(content.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise FingerprintError(f"cannot parse mapping YAML: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FingerprintError("mapping YAML must contain an object at the top level")
    return value, content, digest


def _target_hermes_homes(mapping: Mapping[str, Any]) -> list[Path]:
    raw_users = mapping.get("users") or []
    if not isinstance(raw_users, list):
        raise FingerprintError("mapping users must be a list")
    homes: set[Path] = set()
    for index, raw in enumerate(raw_users):
        if not isinstance(raw, dict):
            continue
        linux_user = str(raw.get("linux_user") or f"hmx_{raw.get('username') or index}").strip()
        home_dir = str(raw.get("home_dir") or f"/home/{linux_user}").strip()
        hermes_home = str(raw.get("hermes_home") or (Path(home_dir) / ".hermes")).strip()
        candidate = Path(os.path.expanduser(hermes_home))
        if not candidate.is_absolute():
            raise FingerprintError(f"users[{index}].hermes_home must be absolute")
        homes.add(_absolute_no_follow(candidate))
    return sorted(homes, key=str)


def capture_state(*, mapping_path: Path, data_dir: Path) -> dict[str, Any]:
    mapping_path = _absolute_no_follow(mapping_path)
    data_dir = _absolute_no_follow(data_dir)
    mapping, _content, mapping_sha256 = _mapping_bytes(mapping_path)
    hermes_homes = _target_hermes_homes(mapping)
    records: dict[str, dict[str, Any]] = {}
    for root in (data_dir, *hermes_homes):
        metadata_roots = (
            tuple(root / name for name in METADATA_ONLY_HERMES_SUBTREES)
            if root in hermes_homes
            else ()
        )
        for record in _fingerprint_tree(
            root,
            metadata_only_roots=metadata_roots,
        ):
            previous = records.setdefault(record["path"], record)
            if previous != record:
                raise FingerprintError(f"overlapping roots produced inconsistent metadata: {record['path']}")
    mapping_record = _entry(mapping_path)
    if mapping_record.get("sha256") != mapping_sha256:
        raise FingerprintError("mapping changed between parse and manifest assembly")
    records[mapping_record["path"]] = mapping_record
    return {
        "schema_version": SCHEMA_VERSION,
        "mapping_sha256": mapping_sha256,
        "roots": {
            "mapping": str(mapping_path),
            "data": str(data_dir),
            "hermes_homes": [str(path) for path in hermes_homes],
            "metadata_only": [
                str(root / name)
                for root in hermes_homes
                for name in METADATA_ONLY_HERMES_SUBTREES
            ],
        },
        "entries": [records[path] for path in sorted(records)],
    }


def _validate_manifest(value: Any, *, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise FingerprintError(f"{label} has an unsupported fingerprint schema")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise FingerprintError(f"{label} entries must be a list")
    result: dict[str, dict[str, Any]] = {}
    previous: str | None = None
    for index, item in enumerate(entries):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise FingerprintError(f"{label} has an invalid entry at index {index}")
        path = item["path"]
        if not path or previous is not None and path <= previous:
            raise FingerprintError(f"{label} entry paths must be sorted and unique")
        previous = path
        result[path] = item
    return result


def compare_manifests(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, list[str]]:
    left = _validate_manifest(before, label="before manifest")
    right = _validate_manifest(after, label="after manifest")
    return {
        "added": sorted(set(right) - set(left)),
        "removed": sorted(set(left) - set(right)),
        "changed": sorted(path for path in set(left) & set(right) if left[path] != right[path]),
    }


def _read_json(path: Path) -> dict[str, Any]:
    path = _absolute_no_follow(path)
    digest, content, _stable_info = _stable_regular_digest(path, collect=True)
    del digest
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FingerprintError(f"invalid fingerprint JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FingerprintError(f"fingerprint manifest must be an object: {path}")
    return value


def write_manifest_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path = _absolute_no_follow(path)
    parent = path.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as exc:
        raise FingerprintError(f"manifest parent is unavailable: {parent}: {exc}") from exc
    if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
        raise FingerprintError(f"manifest parent must be a real directory: {parent}")
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temp = Path(raw_temp)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(_canonical_json(dict(value)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600, follow_symlinks=False)
        directory_fd = os.open(
            parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp.unlink(missing_ok=True)
        raise


def _is_within(path: Path, root: Path) -> bool:
    path = _absolute_no_follow(path)
    root = _absolute_no_follow(root)
    return path == root or root in path.parents


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    capture.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    capture.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture":
            manifest = capture_state(mapping_path=args.mapping, data_dir=args.data_dir)
            roots = [Path(manifest["roots"]["data"])] + [
                Path(path) for path in manifest["roots"]["hermes_homes"]
            ]
            if any(_is_within(args.output, root) for root in roots):
                raise FingerprintError("fingerprint output must be outside every scanned tree")
            write_manifest_atomic(args.output, manifest)
            print(json.dumps({"entries": len(manifest["entries"]), "output": str(_absolute_no_follow(args.output))}))
            return 0
        result = compare_manifests(_read_json(args.before), _read_json(args.after))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 1 if any(result.values()) else 0
    except (FingerprintError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

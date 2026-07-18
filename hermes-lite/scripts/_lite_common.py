#!/usr/bin/env python3
"""Shared, side-effect-free helpers for Hermes Lite release tooling."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
LITE_ROOT = SCRIPT_DIR.parent
MANIFEST_DIR = LITE_ROOT / "manifests"
REPO_ROOT = LITE_ROOT.parent
DEFAULT_PROFILE = LITE_ROOT / "runtime-profile.yaml"

INFRASTRUCTURE_ROOTS = frozenset(
    {
        "manifests",
        "scripts",
        "tests",
        "tests_e2e",
        "tests_packaging",
    }
)
IGNORED_PARTS = frozenset(
    {
        ".git",
        ".pytest_cache",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
PRODUCTION_ROOTS = (Path("/opt"), Path("/srv"))


class LiteReleaseError(RuntimeError):
    """Raised when a Hermes Lite release invariant is violated."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LiteReleaseError(f"missing manifest: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LiteReleaseError(f"cannot read JSON manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LiteReleaseError(f"JSON manifest must be an object: {path}")
    return value


def write_json_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(dict(value)))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_mode(mode: int) -> str:
    return "0755" if mode & 0o111 else "0644"


def _safe_relative_path(raw: Any, *, label: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise LiteReleaseError(f"{label} must be a non-empty relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != raw:
        raise LiteReleaseError(f"{label} is not a canonical relative path: {raw!r}")
    return raw


def _is_generated_path(relative: PurePosixPath) -> bool:
    if any(part in IGNORED_PARTS or part.endswith(".egg-info") for part in relative.parts):
        return True
    return relative.name.endswith((".pyc", ".pyo"))


def iter_release_source_files(root: Path) -> Iterable[Path]:
    """Yield product source files, excluding release tooling and generated files."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise LiteReleaseError(f"source root does not exist: {root}")
    for path in sorted(root.rglob("*")):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if not relative.parts:
            continue
        if relative.parts[0] in INFRASTRUCTURE_ROOTS or _is_generated_path(relative):
            continue
        if path.is_symlink():
            raise LiteReleaseError(f"source inventory does not allow symlinks: {relative}")
        if path.is_file():
            yield path
        elif not path.is_dir():
            raise LiteReleaseError(f"unsupported source entry type: {relative}")


def source_inventory(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    files = []
    for path in iter_release_source_files(root):
        relative = path.relative_to(root).as_posix()
        info = path.stat()
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size": info.st_size,
                "mode": canonical_mode(stat.S_IMODE(info.st_mode)),
            }
        )
    files.sort(key=lambda item: item["path"])
    return {"schema_version": 1, "files": files}


def _validate_inventory(
    value: Mapping[str, Any], *, label: str
) -> list[dict[str, Any]]:
    if set(value) != {"schema_version", "files"} or value.get("schema_version") != 1:
        raise LiteReleaseError(
            f"{label} inventory must contain schema_version=1 and files"
        )
    raw_files = value.get("files")
    if not isinstance(raw_files, list):
        raise LiteReleaseError(f"{label} inventory files must be a list")
    files: list[dict[str, Any]] = []
    previous: str | None = None
    for index, raw in enumerate(raw_files):
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256", "size", "mode"}:
            raise LiteReleaseError(f"invalid {label} inventory entry at index {index}")
        path = _safe_relative_path(raw["path"], label=f"{label} files[{index}].path")
        digest = raw["sha256"]
        size = raw["size"]
        mode = raw["mode"]
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise LiteReleaseError(f"invalid SHA256 for {label} inventory path {path}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise LiteReleaseError(f"invalid size for {label} inventory path {path}")
        if mode not in {"0644", "0755"}:
            raise LiteReleaseError(f"invalid mode for {label} inventory path {path}")
        if previous is not None and path <= previous:
            raise LiteReleaseError(f"{label} inventory paths must be unique and sorted")
        previous = path
        files.append(dict(raw))
    return files


def compare_inventory(
    expected: Mapping[str, Any], actual: Mapping[str, Any], *, label: str
) -> dict[str, Any]:
    expected_files = _validate_inventory(expected, label=label)
    actual_files = _validate_inventory(actual, label=f"actual {label}")
    expected_map = {item["path"]: item for item in expected_files}
    actual_map = {item["path"]: item for item in actual_files}
    missing = sorted(set(expected_map) - set(actual_map))
    extra = sorted(set(actual_map) - set(expected_map))
    changed = sorted(
        path
        for path in set(expected_map) & set(actual_map)
        if expected_map[path] != actual_map[path]
    )
    if missing or extra or changed:
        raise LiteReleaseError(
            f"{label} inventory mismatch: missing={missing[:20]}, "
            f"extra={extra[:20]}, changed={changed[:20]}"
        )
    return {
        "file_count": len(actual_files),
        "byte_count": sum(item["size"] for item in actual_files),
        "inventory_sha256": sha256_bytes(canonical_json_bytes(actual)),
    }


def copy_inventory_files(
    source: Path, destination: Path, inventory: Mapping[str, Any]
) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve(strict=False)
    if destination.exists():
        raise LiteReleaseError(f"copy destination already exists: {destination}")
    files = _validate_inventory(inventory, label="source")
    destination.mkdir(parents=True, mode=0o755)
    for item in files:
        relative = PurePosixPath(item["path"])
        source_path = source.joinpath(*relative.parts)
        if source_path.is_symlink() or not source_path.is_file():
            raise LiteReleaseError(f"source inventory file disappeared: {relative}")
        if sha256_file(source_path) != item["sha256"]:
            raise LiteReleaseError(f"source inventory file changed during copy: {relative}")
        destination_path = destination.joinpath(*relative.parts)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
        os.chmod(destination_path, int(item["mode"], 8))
    compare_inventory(inventory, source_inventory(destination), label="copied source")


def wheel_inventory(wheel: Path) -> dict[str, Any]:
    wheel = wheel.expanduser().resolve()
    try:
        with zipfile.ZipFile(wheel) as archive:
            seen: set[str] = set()
            files = []
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                normalized = path.as_posix().rstrip("/")
                if path.is_absolute() or not path.parts or ".." in path.parts:
                    raise LiteReleaseError(f"wheel contains unsafe path: {info.filename!r}")
                if normalized in seen:
                    raise LiteReleaseError(f"wheel contains duplicate path: {info.filename!r}")
                seen.add(normalized)
                if info.is_dir():
                    continue
                unix_mode = (info.external_attr >> 16) & 0o177777
                if stat.S_ISLNK(unix_mode):
                    raise LiteReleaseError(f"wheel contains symlink: {info.filename}")
                data = archive.read(info)
                files.append(
                    {
                        "path": normalized,
                        "sha256": sha256_bytes(data),
                        "size": len(data),
                        "mode": canonical_mode(unix_mode),
                    }
                )
    except (OSError, zipfile.BadZipFile) as exc:
        raise LiteReleaseError(f"cannot inspect wheel {wheel}: {exc}") from exc
    files.sort(key=lambda item: item["path"])
    return {"schema_version": 1, "files": files}


def materialize_wheel(wheel: Path, destination: Path) -> Path:
    """Materialize wheel contents without invoking pip or processing .pth files."""
    wheel = wheel.expanduser().resolve()
    destination = destination.expanduser().resolve(strict=False)
    if destination.exists():
        raise LiteReleaseError(f"wheel target already exists: {destination}")
    # Validate every member before writing any output.
    wheel_inventory(wheel)
    destination.mkdir(parents=True, mode=0o755)
    try:
        with zipfile.ZipFile(wheel) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = PurePosixPath(info.filename).parts
                target_parts = parts
                if len(parts) >= 3 and parts[0].endswith(".data"):
                    scheme = parts[1]
                    if scheme not in {"data", "purelib", "platlib"}:
                        continue
                    target_parts = parts[2:]
                if not target_parts:
                    continue
                target = destination.joinpath(*target_parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(info)
                target.write_bytes(data)
                unix_mode = (info.external_attr >> 16) & 0o177777
                os.chmod(target, int(canonical_mode(unix_mode), 8))
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise LiteReleaseError(f"cannot materialize wheel {wheel}: {exc}") from exc
    return destination


def ensure_non_production_path(path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    for root in PRODUCTION_ROOTS:
        if resolved == root or root in resolved.parents:
            raise LiteReleaseError(f"{label} must not be under {root}: {resolved}")
    return resolved


def ensure_new_output_path(path: Path, *, label: str) -> Path:
    resolved = ensure_non_production_path(path, label=label)
    if resolved.exists():
        raise LiteReleaseError(f"{label} already exists: {resolved}")
    return resolved


def temporary_directory(*, prefix: str, parent: Path | None = None):
    if parent is not None:
        parent = ensure_non_production_path(parent, label="work directory")
        parent.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=parent)


def tree_summary(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise LiteReleaseError(f"release asset tree contains symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        info = path.stat()
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size": info.st_size,
                "mode": canonical_mode(stat.S_IMODE(info.st_mode)),
            }
        )
    return {
        "file_count": len(files),
        "byte_count": sum(item["size"] for item in files),
        "tree_sha256": sha256_bytes(canonical_json_bytes(files)),
        "files": files,
    }

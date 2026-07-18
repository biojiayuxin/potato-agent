#!/usr/bin/env python3
"""Shared, side-effect-free helpers for the Potato Hermes release tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGING_DIR = SCRIPT_DIR.parent
REPO_ROOT = PACKAGING_DIR.parents[1]
PRODUCTION_ROOTS = (Path("/opt"), Path("/srv"))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PackagingError(RuntimeError):
    """Raised when a release input or invariant is invalid."""


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackagingError(f"missing YAML file: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise PackagingError(f"cannot read YAML file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackagingError(f"YAML document must be a mapping: {path}")
    return value


def load_json_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackagingError(f"missing JSON file: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagingError(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackagingError(f"JSON document must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_manifest_lines(path: Path) -> list[str]:
    try:
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except FileNotFoundError as exc:
        raise PackagingError(f"missing expected manifest: {path}") from exc
    except OSError as exc:
        raise PackagingError(f"cannot read expected manifest {path}: {exc}") from exc
    if len(lines) != len(set(lines)):
        raise PackagingError(f"expected manifest contains duplicates: {path}")
    return lines


def ensure_non_production_path(path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    for root in PRODUCTION_ROOTS:
        if resolved == root or root in resolved.parents:
            raise PackagingError(
                f"{label} must not be under production root {root}: {resolved}"
            )
    return resolved


def ensure_new_output_path(path: Path, *, label: str = "output") -> Path:
    resolved = ensure_non_production_path(path, label=label)
    if resolved.exists():
        raise PackagingError(f"{label} already exists: {resolved}")
    return resolved


def _relative_manifest_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackagingError(f"{field} must be a non-empty relative path")
    path = Path(value.strip())
    if path.is_absolute() or ".." in path.parts:
        raise PackagingError(f"{field} must be a safe relative path: {value!r}")
    return path.as_posix()


def validate_upstream_manifest(data: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "reference_kind",
        "provenance_status",
        "provenance_note",
        "repository",
        "tag",
        "commit",
        "tree",
        "archive_url",
        "archive_sha256",
        "upstream_version",
        "python",
        "uv_lock_sha256",
        "vendored_baseline_commit",
        "vendored_tree",
        "vendored_uv_lock_sha256",
        "potato_revision",
        "patch_series",
        "baseline_attestation",
        "replay_attestation",
        "fully_patched_release_tree",
    }
    actual = set(data)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise PackagingError("invalid upstream manifest keys: " + "; ".join(details))

    if data["schema_version"] != 2:
        raise PackagingError("upstream schema_version must be 2")
    for field in (
        "reference_kind",
        "provenance_status",
        "provenance_note",
        "repository",
        "tag",
        "archive_url",
        "upstream_version",
        "python",
    ):
        if not isinstance(data[field], str) or not data[field].strip():
            raise PackagingError(f"upstream {field} must be a non-empty string")
    if data["reference_kind"] not in {"candidate", "baseline"}:
        raise PackagingError("upstream reference_kind must be candidate or baseline")
    if data["provenance_status"] not in {"verified", "unverified"}:
        raise PackagingError("upstream provenance_status must be verified or unverified")
    for field in ("commit", "tree", "vendored_baseline_commit", "vendored_tree"):
        value = data[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise PackagingError(f"upstream {field} must be a 40-character lowercase Git hash")
    for field in ("archive_sha256", "uv_lock_sha256", "vendored_uv_lock_sha256"):
        value = data[field]
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            raise PackagingError(f"upstream {field} must be a lowercase SHA256")
    revision = data["potato_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PackagingError("upstream potato_revision must be a non-negative integer")
    raw_series = data["patch_series"]
    if not isinstance(raw_series, list):
        raise PackagingError("upstream patch_series must be a list")
    series = [
        _relative_manifest_path(value, f"patch_series[{index}]")
        for index, value in enumerate(raw_series)
    ]
    if len(series) != len(set(series)):
        raise PackagingError("upstream patch_series must not contain duplicates")

    for field in ("baseline_attestation", "replay_attestation"):
        attestation = data[field]
        if not isinstance(attestation, Mapping) or set(attestation) != {
            "status",
            "statement_sha256",
        }:
            raise PackagingError(
                f"upstream {field} must contain exactly status and statement_sha256"
            )
        status_value = attestation["status"]
        statement_sha256 = attestation["statement_sha256"]
        if status_value not in {"pending", "attested"}:
            raise PackagingError(
                f"upstream {field}.status must be pending or attested"
            )
        if status_value == "pending":
            if statement_sha256 is not None:
                raise PackagingError(
                    f"upstream {field}.statement_sha256 must be null while pending"
                )
        elif not isinstance(statement_sha256, str) or SHA256_RE.fullmatch(
            statement_sha256
        ) is None:
            raise PackagingError(
                f"upstream {field}.statement_sha256 must be a lowercase SHA256 "
                "when attested"
            )

    release_tree = data["fully_patched_release_tree"]
    if release_tree is not None and (
        not isinstance(release_tree, str)
        or re.fullmatch(r"[0-9a-f]{40}", release_tree) is None
    ):
        raise PackagingError(
            "upstream fully_patched_release_tree must be null or a 40-character "
            "lowercase Git tree hash"
        )

    normalized = dict(data)
    normalized["patch_series"] = series
    normalized["baseline_attestation"] = dict(data["baseline_attestation"])
    normalized["replay_attestation"] = dict(data["replay_attestation"])
    return normalized


def load_upstream_manifest(path: Path) -> dict[str, Any]:
    return validate_upstream_manifest(load_yaml_mapping(path))


def validate_patch_series(manifest: Mapping[str, Any], patch_dir: Path) -> list[Path]:
    """Resolve the exact, ordered patch input declared by the release manifest."""
    patch_dir = patch_dir.expanduser().resolve()
    declared = list(manifest["patch_series"])
    series = read_manifest_lines(patch_dir / "series")
    if series != declared:
        raise PackagingError(
            "patch series mismatch between upstream.yaml and patches/series: "
            f"{declared!r} != {series!r}"
        )

    declared_set = set(declared)
    undeclared = sorted(
        path.relative_to(patch_dir).as_posix()
        for path in patch_dir.rglob("*.patch")
        if path.relative_to(patch_dir).as_posix() not in declared_set
    )
    if undeclared:
        raise PackagingError("undeclared patch files: " + ", ".join(undeclared))

    result: list[Path] = []
    for item in declared:
        path = (patch_dir / item).resolve()
        if patch_dir != path.parent and patch_dir not in path.parents:
            raise PackagingError(f"patch escapes patch directory: {item}")
        if not path.is_file():
            raise PackagingError(f"declared patch is missing: {path}")
        result.append(path)
    return result


def tree_inventory(root: Path, *, include_files: bool = False) -> dict[str, Any]:
    """Return a deterministic content/path/mode inventory for a directory."""
    root = root.resolve()
    if not root.is_dir():
        raise PackagingError(f"inventory root is not a directory: {root}")

    directories: list[tuple[str, int]] = []
    files: list[dict[str, Any]] = []
    byte_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise PackagingError(f"skill trees must not contain symlinks: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            directories.append((relative, mode))
            continue
        if not path.is_file():
            raise PackagingError(f"unsupported filesystem entry in skill tree: {path}")
        size = path.stat().st_size
        item = {
            "path": relative,
            "sha256": sha256_file(path),
            "size": size,
            "mode": f"{mode:04o}",
        }
        files.append(item)
        byte_count += size

    digest = hashlib.sha256()
    for relative, mode in directories:
        digest.update(f"D\0{relative}\0{mode:04o}\n".encode("utf-8"))
    for item in files:
        digest.update(
            (
                f"F\0{item['path']}\0{item['sha256']}\0{item['size']}\0"
                f"{item['mode']}\n"
            ).encode("utf-8")
        )

    result: dict[str, Any] = {
        "tree_sha256": digest.hexdigest(),
        "file_count": len(files),
        "directory_count": len(directories),
        "byte_count": byte_count,
    }
    if include_files:
        result["files"] = files
    return result


def skill_tree_summaries(source: Path) -> dict[str, dict[str, Any]]:
    return {
        name: tree_inventory(source / name)
        for name in ("skills", "optional-skills")
    }


def validate_skill_inventory(source: Path, expected_path: Path) -> dict[str, Any]:
    expected = load_json_mapping(expected_path)
    if set(expected) != {"schema_version", "roots"} or expected["schema_version"] != 1:
        raise PackagingError(f"invalid skill inventory schema: {expected_path}")
    roots = expected.get("roots")
    if not isinstance(roots, dict) or set(roots) != {"skills", "optional-skills"}:
        raise PackagingError(f"skill inventory must contain both skill roots: {expected_path}")
    actual = skill_tree_summaries(source)
    if actual != roots:
        raise PackagingError(
            "skill inventory mismatch: expected "
            + json.dumps(roots, sort_keys=True)
            + ", got "
            + json.dumps(actual, sort_keys=True)
        )
    return actual


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def replace_directory_atomically(staging: Path, output: Path) -> None:
    if output.exists():
        raise PackagingError(f"output appeared while building: {output}")
    os.replace(staging, output)

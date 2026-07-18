#!/usr/bin/env python3
"""Fetch and replay a pinned Hermes upstream archive outside production paths."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    PACKAGING_DIR,
    PackagingError,
    ensure_new_output_path,
    ensure_non_production_path,
    load_upstream_manifest,
    sha256_file,
    validate_patch_series,
)


def _patch_series(manifest: dict[str, Any], patch_dir: Path) -> list[Path]:
    return validate_patch_series(manifest, patch_dir)


def _validate_tar_members(archive: tarfile.TarFile) -> str:
    roots: set[str] = set()
    seen: set[str] = set()
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise PackagingError(f"unsafe archive member: {member.name!r}")
        normalized = path.as_posix().rstrip("/")
        if normalized in seen:
            raise PackagingError(f"duplicate archive member: {member.name!r}")
        seen.add(normalized)
        roots.add(path.parts[0])
        if not (member.isdir() or member.isfile()):
            raise PackagingError(
                f"archive contains unsupported link/device entry: {member.name!r}"
            )
    if len(roots) != 1:
        raise PackagingError(f"archive must contain exactly one top-level directory: {roots}")
    return next(iter(roots))


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            root_name = _validate_tar_members(archive)
            archive.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise PackagingError(f"cannot extract archive {archive_path}: {exc}") from exc
    source = destination / root_name
    if not source.is_dir():
        raise PackagingError(f"archive root was not extracted: {source}")
    return source


def _validate_extracted_source(source: Path, manifest: dict[str, Any]) -> None:
    lock_path = source / "uv.lock"
    if not lock_path.is_file():
        raise PackagingError("upstream archive does not contain uv.lock")
    actual_lock = sha256_file(lock_path)
    if actual_lock != manifest["uv_lock_sha256"]:
        raise PackagingError(
            "candidate archive uv.lock SHA256 mismatch: "
            f"expected {manifest['uv_lock_sha256']}, got {actual_lock}"
        )
    pyproject_path = source / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise PackagingError(f"invalid upstream pyproject.toml: {exc}") from exc
    if project.get("version") != manifest["upstream_version"]:
        raise PackagingError(
            "candidate archive version mismatch: expected "
            f"{manifest['upstream_version']!r}, got {project.get('version')!r}"
        )


def _apply_patches(source: Path, patches: list[Path]) -> None:
    for patch in patches:
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", str(patch)],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode != 0:
            detail = (check.stderr or check.stdout).strip()
            raise PackagingError(f"patch check failed for {patch.name}: {detail}")
        apply = subprocess.run(
            ["git", "apply", "--whitespace=error-all", str(patch)],
            cwd=source,
            capture_output=True,
            text=True,
            check=False,
        )
        if apply.returncode != 0:
            detail = (apply.stderr or apply.stdout).strip()
            raise PackagingError(f"patch apply failed for {patch.name}: {detail}")


def fetch(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = load_upstream_manifest(manifest_path)
    output = ensure_new_output_path(output_path, label="archive output")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.part-{uuid.uuid4().hex}")
    request = urllib.request.Request(
        manifest["archive_url"], headers={"User-Agent": "potato-hermes-vendor/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("xb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        actual = sha256_file(partial)
        if actual != manifest["archive_sha256"]:
            raise PackagingError(
                "archive SHA256 mismatch: "
                f"expected {manifest['archive_sha256']}, got {actual}"
            )
        os.replace(partial, output)
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
    return {
        "archive": str(output),
        "sha256": manifest["archive_sha256"],
        "commit": manifest["commit"],
        "reference_kind": manifest["reference_kind"],
        "provenance_status": manifest["provenance_status"],
    }


def apply_archive(
    manifest_path: Path,
    archive_path: Path,
    patch_dir: Path,
    output_path: Path | None,
    *,
    check_only: bool,
    work_dir: Path | None,
) -> dict[str, Any]:
    manifest = load_upstream_manifest(manifest_path)
    archive = archive_path.expanduser().resolve()
    if not archive.is_file():
        raise PackagingError(f"archive does not exist: {archive}")
    actual_archive_hash = sha256_file(archive)
    if actual_archive_hash != manifest["archive_sha256"]:
        raise PackagingError(
            "archive SHA256 mismatch: "
            f"expected {manifest['archive_sha256']}, got {actual_archive_hash}"
        )
    patches = _patch_series(manifest, patch_dir)

    if check_only:
        if output_path is not None:
            raise PackagingError("--output is not allowed with --check")
        temp_parent = None
        if work_dir is not None:
            temp_parent = ensure_non_production_path(work_dir, label="work directory")
            temp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="potato-hermes-vendor-", dir=temp_parent) as raw:
            source = _extract_archive(archive, Path(raw))
            _validate_extracted_source(source, manifest)
            _apply_patches(source, patches)
        return {
            "checked": True,
            "patches": [path.name for path in patches],
            "commit": manifest["commit"],
            "provenance_status": manifest["provenance_status"],
        }

    if output_path is None:
        raise PackagingError("--output is required unless --check is used")
    if work_dir is not None:
        raise PackagingError("--work-dir is only supported with --check")
    output = ensure_new_output_path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.vendor-{uuid.uuid4().hex}")
    try:
        staging.mkdir(mode=0o755)
        source = _extract_archive(archive, staging)
        _validate_extracted_source(source, manifest)
        _apply_patches(source, patches)
        os.replace(source, output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "output": str(output),
        "patches": [path.name for path in patches],
        "commit": manifest["commit"],
        "provenance_status": manifest["provenance_status"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="download and verify the archive")
    fetch_parser.add_argument("--manifest", type=Path, default=PACKAGING_DIR / "upstream.yaml")
    fetch_parser.add_argument("--output", type=Path, required=True)

    apply_parser = subparsers.add_parser("apply", help="verify and apply the patch series")
    apply_parser.add_argument("--manifest", type=Path, default=PACKAGING_DIR / "upstream.yaml")
    apply_parser.add_argument("--archive", type=Path, required=True)
    apply_parser.add_argument("--patch-dir", type=Path, default=PACKAGING_DIR / "patches")
    apply_parser.add_argument("--output", type=Path)
    apply_parser.add_argument("--check", action="store_true")
    apply_parser.add_argument("--work-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "fetch":
            result = fetch(args.manifest, args.output)
        else:
            result = apply_archive(
                args.manifest,
                args.archive,
                args.patch_dir,
                args.output,
                check_only=args.check,
                work_dir=args.work_dir,
            )
    except (PackagingError, OSError, urllib.error.URLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

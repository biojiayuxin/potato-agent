#!/usr/bin/env python3
"""Generate a platform lock and wheelhouse manifests from a pip report."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name, parse_wheel_filename


TARGET = {
    "implementation": "cp",
    "python_version": "3.12",
    "platform": "linux",
    "machine": "x86_64",
}


class LockError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_report(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    install = value.get("install") if isinstance(value, dict) else None
    if not isinstance(install, list) or not install:
        raise LockError("pip report has no install records")
    return install


def generate(
    *,
    report_path: Path,
    wheelhouse: Path,
    lock_path: Path,
    wheelhouse_manifest_path: Path,
    distribution_fingerprint_path: Path,
) -> None:
    if sys.version_info[:2] != (3, 12):
        raise LockError("lock generation requires Python 3.12")
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise LockError("lock generation requires Linux x86_64")

    resolved: dict[str, dict[str, str]] = {}
    for item in _load_report(report_path):
        metadata = item.get("metadata") if isinstance(item, dict) else None
        download = item.get("download_info") if isinstance(item, dict) else None
        archive = download.get("archive_info") if isinstance(download, dict) else None
        hashes = archive.get("hashes") if isinstance(archive, dict) else None
        if not isinstance(metadata, dict) or not isinstance(hashes, dict):
            raise LockError("pip report record is missing metadata or hashes")
        name = canonicalize_name(str(metadata.get("name") or ""))
        version = str(metadata.get("version") or "")
        digest = str(hashes.get("sha256") or "")
        if not name or not version or len(digest) != 64:
            raise LockError("pip report record has an invalid name, version, or SHA256")
        if name in resolved:
            raise LockError(f"duplicate distribution in pip report: {name}")
        resolved[name] = {"version": version, "sha256": digest}

    wheel_files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for wheel in sorted(wheelhouse.glob("*.whl")):
        parsed_name, parsed_version, _build, _tags = parse_wheel_filename(wheel.name)
        name = canonicalize_name(parsed_name)
        if name in seen:
            raise LockError(f"wheelhouse contains duplicate distribution: {name}")
        seen.add(name)
        expected = resolved.get(name)
        digest = _sha256(wheel)
        if expected is None:
            raise LockError(f"wheel is absent from pip report: {wheel.name}")
        if str(parsed_version) != expected["version"] or digest != expected["sha256"]:
            raise LockError(f"wheel differs from pip report: {wheel.name}")
        wheel_files.append(
            {
                "filename": wheel.name,
                "name": name,
                "version": str(parsed_version),
                "sha256": digest,
                "size": wheel.stat().st_size,
            }
        )
    if seen != set(resolved):
        raise LockError(f"wheelhouse is incomplete: {sorted(set(resolved) - seen)}")

    lock_lines = [
        "# Generated for CPython 3.12 / Linux x86_64. Do not edit by hand.",
        "--only-binary=:all:",
    ]
    for name in sorted(resolved):
        item = resolved[name]
        lock_lines.append(
            f"{name}=={item['version']} --hash=sha256:{item['sha256']}"
        )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("\n".join(lock_lines) + "\n", encoding="ascii")

    wheelhouse_manifest = {
        "schema_version": 1,
        "target": TARGET,
        "lock": {"path": lock_path.name, "sha256": _sha256(lock_path)},
        "files": wheel_files,
    }
    wheelhouse_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    wheelhouse_manifest_path.write_text(
        json.dumps(wheelhouse_manifest, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )

    fingerprint = {
        "schema_version": 1,
        "target": TARGET,
        "distributions": {
            name: resolved[name]["version"] for name in sorted(resolved)
        },
    }
    distribution_fingerprint_path.parent.mkdir(parents=True, exist_ok=True)
    distribution_fingerprint_path.write_text(
        json.dumps(fingerprint, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--wheelhouse-manifest", type=Path, required=True)
    parser.add_argument("--distribution-fingerprint", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        generate(
            report_path=args.report,
            wheelhouse=args.wheelhouse,
            lock_path=args.lock,
            wheelhouse_manifest_path=args.wheelhouse_manifest,
            distribution_fingerprint_path=args.distribution_fingerprint,
        )
    except (LockError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

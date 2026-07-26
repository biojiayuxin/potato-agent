#!/usr/bin/env python3
"""Validate a CPython 3.12 Linux x86_64 requirements lock and manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


LOCK_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$"
)
TARGET = {
    "implementation": "cp",
    "python_version": "3.12",
    "platform": "linux",
    "machine": "x86_64",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(
    *, requirements: Path, lock: Path, wheelhouse_manifest: Path, fingerprint: Path
) -> None:
    lines = lock.read_text(encoding="ascii").splitlines()
    if lines[:2] != [
        "# Generated for CPython 3.12 / Linux x86_64. Do not edit by hand.",
        "--only-binary=:all:",
    ]:
        raise RuntimeError("lock target header is invalid")
    locked: dict[str, dict[str, str]] = {}
    for line in lines[2:]:
        if not line:
            continue
        match = LOCK_RE.fullmatch(line)
        if match is None:
            raise RuntimeError(f"invalid lock line: {line!r}")
        name = canonicalize_name(match.group(1))
        if name in locked:
            raise RuntimeError(f"duplicate lock distribution: {name}")
        locked[name] = {"version": match.group(2), "sha256": match.group(3)}

    environment = default_environment()
    environment.update(
        {
            "implementation_name": "cpython",
            "platform_machine": "x86_64",
            "python_version": "3.12",
            "python_full_version": "3.12.0",
            "sys_platform": "linux",
        }
    )
    direct = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for raw in direct:
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        name = canonicalize_name(requirement.name)
        selected = locked.get(name)
        if selected is None or Version(selected["version"]) not in requirement.specifier:
            raise RuntimeError(f"lock does not satisfy direct requirement: {raw}")

    wheelhouse = json.loads(wheelhouse_manifest.read_text(encoding="utf-8"))
    if wheelhouse.get("schema_version") != 1 or wheelhouse.get("target") != TARGET:
        raise RuntimeError("wheelhouse target is invalid")
    if wheelhouse.get("lock") != {"path": lock.name, "sha256": _sha256(lock)}:
        raise RuntimeError("wheelhouse does not bind the lock")
    artifacts = {}
    for item in wheelhouse.get("files", []):
        name = canonicalize_name(str(item.get("name") or ""))
        if not name or name in artifacts:
            raise RuntimeError("wheelhouse contains an invalid distribution")
        artifacts[name] = {
            "version": str(item.get("version") or ""),
            "sha256": str(item.get("sha256") or ""),
        }
    if artifacts != locked:
        raise RuntimeError("wheelhouse artifacts differ from the lock")

    expected = json.loads(fingerprint.read_text(encoding="utf-8"))
    if expected != {
        "schema_version": 1,
        "target": TARGET,
        "distributions": {
            name: item["version"] for name, item in sorted(locked.items())
        },
    }:
        raise RuntimeError("distribution fingerprint differs from the lock")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--wheelhouse-manifest", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verify(
            requirements=args.requirements,
            lock=args.lock,
            wheelhouse_manifest=args.wheelhouse_manifest,
            fingerprint=args.fingerprint,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

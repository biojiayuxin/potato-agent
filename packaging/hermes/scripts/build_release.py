#!/usr/bin/env python3
"""Build an immutable local Hermes wheel release with separately hashed skills."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from _common import (
    PACKAGING_DIR,
    REPO_ROOT,
    PackagingError,
    canonical_json_bytes,
    ensure_new_output_path,
    ensure_non_production_path,
    load_upstream_manifest,
    sha256_file,
    tree_inventory,
)
from release_gate import (
    evaluate_release_gate,
    materialize_release_inputs,
    release_attestation_state,
)
from verify_profile import verify


SOURCE_IGNORES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


def _ignore_source(_directory: str, names: list[str]) -> set[str]:
    ignored = {
        name
        for name in names
        if name in SOURCE_IGNORES
        or name.endswith(".pyc")
        or name.endswith(".pyo")
        or name.endswith(".egg-info")
    }
    return ignored


def _build_version(manifest: dict[str, Any]) -> str:
    return f"{manifest['upstream_version']}+potato.{manifest['potato_revision']}"


def _patch_build_version(source: Path, manifest: dict[str, Any]) -> str:
    path = source / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
        current = tomllib.loads(text)["project"]["version"]
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise PackagingError(f"invalid source pyproject.toml: {exc}") from exc
    if current != manifest["upstream_version"]:
        raise PackagingError(
            f"source project version {current!r} does not match upstream "
            f"{manifest['upstream_version']!r}"
        )
    version = _build_version(manifest)
    pattern = re.compile(rf'(?m)^version = "{re.escape(current)}"$')
    updated, count = pattern.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        raise PackagingError("could not replace the [project] version exactly once")
    path.write_text(updated, encoding="utf-8")
    return version


def _build_wheel(
    *, python: Path, source: Path, wheel_dir: Path, home: Path, build_tmp: Path
) -> Path:
    executable = Path(os.path.abspath(python.expanduser()))
    if not executable.is_file():
        raise PackagingError(f"Python executable does not exist: {executable}")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(home / ".hermes"),
            "TMPDIR": str(build_tmp),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_CACHE_DIR": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "SOURCE_DATE_EPOCH": os.environ.get("SOURCE_DATE_EPOCH", "315532800"),
        }
    )
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            str(executable),
            "-B",
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(source),
        ],
        cwd=build_tmp,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise PackagingError(f"wheel build failed with exit {completed.returncode}: {detail}")
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise PackagingError(f"wheel build must produce exactly one wheel, got {wheels}")
    return wheels[0]


def _inspect_wheel(wheel: Path, expected_version: str) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if "runtime_profile.py" not in names:
                raise PackagingError("sealed wheel is missing top-level runtime_profile.py")
            leaked = [
                name
                for name in names
                if name.startswith("skills/") or name.startswith("optional-skills/")
            ]
            if leaked:
                raise PackagingError("skills must not be embedded in the wheel: " + leaked[0])
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                raise PackagingError("wheel must contain exactly one METADATA file")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackagingError(f"cannot inspect wheel {wheel}: {exc}") from exc
    if metadata.get("Name") != "hermes-agent":
        raise PackagingError(f"unexpected wheel project name: {metadata.get('Name')!r}")
    if metadata.get("Version") != expected_version:
        raise PackagingError(
            f"unexpected wheel version: expected {expected_version!r}, "
            f"got {metadata.get('Version')!r}"
        )
    return {
        "filename": wheel.name,
        "sha256": sha256_file(wheel),
        "size": wheel.stat().st_size,
        "version": expected_version,
        "runtime_profile_packaged": True,
        "skills_embedded": False,
    }


def _copy_skills(source: Path, release: Path) -> dict[str, Any]:
    share = release / "share" / "hermes"
    result: dict[str, Any] = {}
    for name in ("skills", "optional-skills"):
        source_root = source / name
        destination = share / name
        shutil.copytree(source_root, destination, copy_function=shutil.copy2)
        source_inventory = tree_inventory(source_root, include_files=True)
        copied_inventory = tree_inventory(destination, include_files=True)
        if copied_inventory != source_inventory:
            raise PackagingError(f"copied {name} tree failed hash verification")
        result[name] = copied_inventory
    return result


def build_release(
    *,
    source: Path,
    output: Path | None,
    python: Path,
    profile_path: Path,
    expected_dir: Path,
    upstream_path: Path,
    inventory_path: Path,
    dry_run: bool,
    allow_unverified_provenance: bool,
    allow_dirty_source: bool,
    work_dir: Path | None,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise PackagingError(f"source directory does not exist: {source}")
    upstream = load_upstream_manifest(upstream_path)
    release_gate = evaluate_release_gate(
        manifest=upstream,
        source=source,
        packaging_dir=PACKAGING_DIR,
        profile_path=profile_path,
        expected_dir=expected_dir,
        upstream_path=upstream_path,
        inventory_path=inventory_path,
    )
    source_state = release_gate["git"]
    provenance_verified = upstream["provenance_status"] == "verified"

    if not provenance_verified and not allow_unverified_provenance:
        raise PackagingError(
            "release provenance is unverified; local development builds require "
            "the explicit --allow-unverified-provenance override"
        )
    if provenance_verified and not release_gate["ready"] and not allow_dirty_source:
        raise PackagingError(
            "verified releases require attested replay identity and clean, committed "
            "release inputs: "
            + "; ".join(release_gate["errors"])
            + ". Use --allow-dirty-source only for a non-publishable development build"
        )

    source_verification = verify(
        source=source,
        installed=False,
        wheel=None,
        python=python,
        profile_path=profile_path,
        expected_dir=expected_dir,
        upstream_path=upstream_path,
        inventory_path=inventory_path,
        timeout=120.0,
        require_publishable=False,
    )
    version = _build_version(upstream)
    publishable = (
        release_gate["ready"]
        and not allow_unverified_provenance
        and not allow_dirty_source
    )
    plan = {
        "build_version": version,
        "dry_run": dry_run,
        "publishable": publishable,
        "provenance_status": upstream["provenance_status"],
        "source_state": source_state,
        "release_gate": release_gate,
        "tool_count": source_verification["expected_tool_count"],
        "skills": source_verification.get("skills"),
        "unverified_override": bool(allow_unverified_provenance and not provenance_verified),
        "dirty_override": bool(allow_dirty_source),
    }
    if output is not None:
        plan["output"] = str(ensure_non_production_path(output, label="release output"))
    if dry_run:
        return plan
    if output is None:
        raise PackagingError("--output is required for an actual build")

    destination = ensure_new_output_path(output, label="release output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.release-{uuid.uuid4().hex}")
    temp_parent = None
    if work_dir is not None:
        temp_parent = ensure_non_production_path(work_dir, label="work directory")
        temp_parent.mkdir(parents=True, exist_ok=True)

    try:
        staging.mkdir(mode=0o755)
        with tempfile.TemporaryDirectory(prefix="potato-hermes-build-", dir=temp_parent) as raw:
            work = Path(raw)
            build_source = source
            build_profile_path = profile_path
            build_expected_dir = expected_dir
            build_upstream_path = upstream_path
            build_inventory_path = inventory_path
            if publishable:
                committed_inputs = materialize_release_inputs(
                    source=source,
                    git_state=source_state,
                    destination=work / "committed-inputs",
                )
                build_source = committed_inputs["hermes_source"]
                build_profile_path = committed_inputs["runtime_profile"]
                build_expected_dir = committed_inputs["expected_manifests"]
                build_upstream_path = committed_inputs["upstream_manifest"]
                build_inventory_path = committed_inputs["skill_inventory"]
                committed_upstream = load_upstream_manifest(build_upstream_path)
                if committed_upstream != upstream:
                    raise PackagingError(
                        "working upstream manifest differed from the gated commit"
                    )
                committed_attestation = release_attestation_state(
                    committed_upstream,
                    committed_inputs["release_packaging"] / "patches",
                )
                if not committed_attestation["ready"]:
                    raise PackagingError(
                        "committed release attestation failed after materialization: "
                        + "; ".join(committed_attestation["errors"])
                    )
                source_verification = verify(
                    source=build_source,
                    installed=False,
                    wheel=None,
                    python=python,
                    profile_path=build_profile_path,
                    expected_dir=build_expected_dir,
                    upstream_path=build_upstream_path,
                    inventory_path=build_inventory_path,
                    timeout=120.0,
                    require_publishable=False,
                )
                plan["tool_count"] = source_verification["expected_tool_count"]
                plan["skills"] = source_verification.get("skills")

            copied_source = work / "source"
            shutil.copytree(
                build_source,
                copied_source,
                ignore=_ignore_source,
                copy_function=shutil.copy2,
            )
            built_version = _patch_build_version(copied_source, upstream)
            build_input = tree_inventory(copied_source)

            wheel_dir = staging / "wheel"
            wheel_dir.mkdir()
            home = work / "home"
            build_tmp = work / "tmp"
            home.mkdir()
            build_tmp.mkdir()
            wheel = _build_wheel(
                python=python,
                source=copied_source,
                wheel_dir=wheel_dir,
                home=home,
                build_tmp=build_tmp,
            )
            wheel_info = _inspect_wheel(wheel, built_version)

            config_dir = staging / "config"
            config_dir.mkdir()
            shutil.copy2(build_profile_path, config_dir / "runtime-profile.yaml")
            shutil.copy2(build_upstream_path, config_dir / "upstream.yaml")
            shutil.copytree(
                build_expected_dir,
                config_dir / "expected",
                copy_function=shutil.copy2,
            )
            assets = _copy_skills(copied_source, staging)

            wheel_verification = verify(
                source=None,
                installed=False,
                wheel=wheel,
                python=python,
                profile_path=config_dir / "runtime-profile.yaml",
                expected_dir=config_dir / "expected",
                upstream_path=config_dir / "upstream.yaml",
                inventory_path=config_dir / "expected" / "skills-inventory.json",
                timeout=120.0,
                require_publishable=False,
            )

            if publishable:
                final_gate = evaluate_release_gate(
                    manifest=upstream,
                    source=source,
                    packaging_dir=PACKAGING_DIR,
                    profile_path=profile_path,
                    expected_dir=expected_dir,
                    upstream_path=upstream_path,
                    inventory_path=inventory_path,
                )
                final_errors = list(final_gate["errors"])
                if final_gate["git"].get("head") != source_state.get("head"):
                    final_errors.append("Git HEAD no longer matches the materialized commit")
                if final_errors:
                    raise PackagingError(
                        "release inputs changed during the publishable build: "
                        + "; ".join(final_errors)
                    )
                release_gate = final_gate
                source_state = final_gate["git"]

            manifest = {
                "schema_version": 1,
                "build_version": built_version,
                "created_at_unix": int(time.time()),
                "publishable": publishable,
                "provenance_status": upstream["provenance_status"],
                "provenance_note": upstream["provenance_note"],
                "reference_commit": upstream["commit"],
                "vendored_baseline_commit": upstream["vendored_baseline_commit"],
                "vendored_tree": upstream["vendored_tree"],
                "source_state": source_state,
                "release_gate": release_gate,
                "build_input_tree": build_input,
                "profile": {
                    "sha256": sha256_file(config_dir / "runtime-profile.yaml"),
                    "upstream_manifest_sha256": sha256_file(config_dir / "upstream.yaml"),
                    "expected_manifest_sha256": {
                        path.relative_to(config_dir / "expected").as_posix(): sha256_file(path)
                        for path in sorted((config_dir / "expected").rglob("*"))
                        if path.is_file()
                    },
                    "expected_tool_count": wheel_verification["expected_tool_count"],
                    "logical_model_tools": wheel_verification["logical_model_tools"],
                    "forbidden_module_prefixes": wheel_verification[
                        "forbidden_module_prefixes"
                    ],
                    "forbidden_imports": [],
                    "runtime_profile_origin": wheel_verification[
                        "runtime_profile_origin"
                    ],
                    "gateway_entry_origin": wheel_verification["gateway_entry_origin"],
                },
                "wheel": wheel_info,
                "assets": assets,
                "overrides": {
                    "allow_unverified_provenance": bool(allow_unverified_provenance),
                    "allow_dirty_source": bool(allow_dirty_source),
                },
            }
            (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        os.replace(staging, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        **plan,
        "dry_run": False,
        "manifest": str(destination / "manifest.json"),
        "wheel": str(destination / "wheel" / wheel_info["filename"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "hermes-agent")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--profile", type=Path, default=PACKAGING_DIR / "runtime-profile.yaml")
    parser.add_argument("--expected-dir", type=Path, default=PACKAGING_DIR / "expected")
    parser.add_argument("--upstream", type=Path, default=PACKAGING_DIR / "upstream.yaml")
    parser.add_argument(
        "--skill-inventory",
        type=Path,
        default=PACKAGING_DIR / "expected" / "skills-inventory.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-unverified-provenance", action="store_true")
    parser.add_argument("--allow-dirty-source", action="store_true")
    parser.add_argument("--work-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_release(
            source=args.source,
            output=args.output,
            python=args.python,
            profile_path=args.profile,
            expected_dir=args.expected_dir,
            upstream_path=args.upstream,
            inventory_path=args.skill_inventory,
            dry_run=args.dry_run,
            allow_unverified_provenance=args.allow_unverified_provenance,
            allow_dirty_source=args.allow_dirty_source,
            work_dir=args.work_dir,
        )
    except (PackagingError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

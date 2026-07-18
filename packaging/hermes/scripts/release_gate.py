#!/usr/bin/env python3
"""Fail-closed release identity and Git-input checks for Potato Hermes."""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping

from _common import (
    PackagingError,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    validate_patch_series,
)


def statement_sha256(value: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def baseline_statement(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical identity that a baseline attestation signs off on."""
    return {
        "statement_type": "potato-hermes-baseline-v1",
        "reference_kind": manifest["reference_kind"],
        "repository": manifest["repository"],
        "tag": manifest["tag"],
        "commit": manifest["commit"],
        "tree": manifest["tree"],
        "archive_url": manifest["archive_url"],
        "archive_sha256": manifest["archive_sha256"],
        "upstream_version": manifest["upstream_version"],
        "python": manifest["python"],
        "uv_lock_sha256": manifest["uv_lock_sha256"],
    }


def replay_statement(
    manifest: Mapping[str, Any], patch_dir: Path
) -> dict[str, Any]:
    """Return the canonical replay identity, including every patch byte."""
    patches = validate_patch_series(manifest, patch_dir)
    return {
        "statement_type": "potato-hermes-replay-v1",
        "baseline_statement_sha256": statement_sha256(
            baseline_statement(manifest)
        ),
        "vendored_baseline_commit": manifest["vendored_baseline_commit"],
        "vendored_tree": manifest["vendored_tree"],
        "vendored_uv_lock_sha256": manifest["vendored_uv_lock_sha256"],
        "potato_revision": manifest["potato_revision"],
        "patches": [
            {
                "path": path.relative_to(patch_dir.resolve()).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in patches
        ],
        "fully_patched_release_tree": manifest["fully_patched_release_tree"],
    }


def release_attestation_state(
    manifest: Mapping[str, Any], patch_dir: Path
) -> dict[str, Any]:
    """Validate explicit attestations and their content-bound statements."""
    errors: list[str] = []
    if manifest["provenance_status"] != "verified":
        errors.append("provenance_status is not verified")
    if manifest["reference_kind"] != "baseline":
        errors.append("reference_kind is not baseline")

    baseline = manifest["baseline_attestation"]
    computed_baseline = statement_sha256(baseline_statement(manifest))
    baseline_valid = (
        baseline["status"] == "attested"
        and baseline["statement_sha256"] == computed_baseline
    )
    if baseline["status"] != "attested":
        errors.append("baseline attestation is pending")
    elif baseline["statement_sha256"] != computed_baseline:
        errors.append("baseline attestation statement SHA256 does not match its inputs")

    computed_replay: str | None = None
    replay_error: str | None = None
    try:
        computed_replay = statement_sha256(replay_statement(manifest, patch_dir))
    except (PackagingError, OSError, ValueError) as exc:
        replay_error = str(exc)
        errors.append("replay inputs are invalid: " + replay_error)

    replay = manifest["replay_attestation"]
    replay_valid = (
        replay_error is None
        and replay["status"] == "attested"
        and replay["statement_sha256"] == computed_replay
    )
    if replay["status"] != "attested":
        errors.append("replay attestation is pending")
    elif replay_error is None and replay["statement_sha256"] != computed_replay:
        errors.append("replay attestation statement SHA256 does not match its inputs")

    release_tree = manifest["fully_patched_release_tree"]
    if release_tree is None:
        errors.append("fully_patched_release_tree is not declared")

    return {
        "ready": not errors,
        "errors": errors,
        "baseline": {
            "status": baseline["status"],
            "declared_statement_sha256": baseline["statement_sha256"],
            "computed_statement_sha256": computed_baseline,
            "valid": baseline_valid,
        },
        "replay": {
            "status": replay["status"],
            "declared_statement_sha256": replay["statement_sha256"],
            "computed_statement_sha256": computed_replay,
            "valid": replay_valid,
            "input_error": replay_error,
        },
        "fully_patched_release_tree": release_tree,
    }


def _git(
    root: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def release_git_state(
    *,
    source: Path,
    packaging_dir: Path,
    build_inputs: Mapping[str, Path],
    fully_patched_release_tree: str | None,
) -> dict[str, Any]:
    """Prove all release inputs are committed, clean, and on one Git HEAD."""
    errors: list[str] = []
    try:
        root = Path(_git(source, ["rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
        head_before = _git(root, ["rev-parse", "HEAD"]).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return {
            "git_checkout": False,
            "ready": False,
            "dirty": None,
            "errors": ["Hermes source is not in an auditable Git checkout"],
        }

    requested = {
        "hermes_source": source,
        "release_packaging": packaging_dir,
        **build_inputs,
    }
    inputs: list[dict[str, Any]] = []
    pathspecs: list[str] = []
    source_relative: str | None = None
    source_object: str | None = None

    for label, raw_path in requested.items():
        try:
            path = raw_path.expanduser().resolve(strict=True)
            relative = path.relative_to(root).as_posix()
        except FileNotFoundError:
            errors.append(f"build input {label} does not exist")
            continue
        except (OSError, ValueError):
            errors.append(f"build input {label} is outside the Hermes Git checkout")
            continue

        try:
            object_hash = _git(
                root, ["rev-parse", "--verify", f"HEAD:{relative}"]
            ).stdout.strip()
            object_type = _git(root, ["cat-file", "-t", object_hash]).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            object_hash = None
            object_type = None
            errors.append(f"build input {label} is not committed at HEAD")

        expected_type = "tree" if path.is_dir() else "blob"
        if object_type is not None and object_type != expected_type:
            errors.append(
                f"build input {label} has Git object type {object_type}, "
                f"expected {expected_type}"
            )
        inputs.append(
            {
                "label": label,
                "path": relative,
                "object": object_hash,
                "object_type": object_type,
            }
        )
        pathspecs.append(relative)
        if label == "hermes_source":
            source_relative = relative
            source_object = object_hash if object_type == "tree" else None

    status: list[str] = []
    diff_sha256: str | None = None
    if pathspecs:
        try:
            status_output = _git(
                root,
                [
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--",
                    *sorted(set(pathspecs)),
                ],
            ).stdout
            status = [line for line in status_output.splitlines() if line]
            diff = subprocess.run(
                [
                    "git",
                    "-c",
                    "tar.umask=0002",
                    "-C",
                    str(root),
                    "diff",
                    "--binary",
                    "--no-ext-diff",
                    "HEAD",
                    "--",
                    *sorted(set(pathspecs)),
                ],
                capture_output=True,
                check=True,
            ).stdout
            diff_sha256 = hashlib.sha256(diff).hexdigest()
        except (subprocess.CalledProcessError, OSError):
            errors.append("could not inspect the complete release Git input set")

    if status:
        errors.append("release Git inputs are dirty or untracked")
    if source_object is not None and source_object != fully_patched_release_tree:
        errors.append(
            "committed Hermes source tree does not match fully_patched_release_tree"
        )
    try:
        head_after = _git(root, ["rev-parse", "HEAD"]).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        head_after = None
    if head_after != head_before:
        errors.append("Git HEAD changed while release inputs were inspected")

    return {
        "git_checkout": True,
        "ready": not errors,
        "head": head_before,
        "source_path": source_relative,
        "committed_tree": source_object,
        "manifest_release_tree": fully_patched_release_tree,
        "tree_matches": source_object is not None
        and source_object == fully_patched_release_tree,
        "dirty": bool(status),
        "status": status,
        "diff_sha256": diff_sha256,
        "inputs": inputs,
        "errors": errors,
    }


def _minimal_pathspecs(paths: list[str]) -> list[str]:
    selected: list[str] = []
    for path in sorted(set(paths), key=lambda item: (len(PurePosixPath(item).parts), item)):
        if any(path == parent or path.startswith(parent + "/") for parent in selected):
            continue
        selected.append(path)
    return selected


def materialize_release_inputs(
    *, source: Path, git_state: Mapping[str, Any], destination: Path
) -> dict[str, Path]:
    """Materialize every build input from the gate's fixed commit, never the worktree."""
    if not git_state.get("git_checkout") or not git_state.get("ready"):
        raise PackagingError("cannot materialize release inputs from an unready Git gate")
    head = git_state.get("head")
    raw_inputs = git_state.get("inputs")
    if not isinstance(head, str) or not isinstance(raw_inputs, list):
        raise PackagingError("release Git gate did not record a fixed commit and inputs")

    try:
        root = Path(_git(source, ["rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
        current_head = _git(root, ["rev-parse", "HEAD"]).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        raise PackagingError("could not reopen the gated Git checkout") from exc
    if current_head != head:
        raise PackagingError("Git HEAD changed before release inputs were materialized")

    inputs: dict[str, str] = {}
    for item in raw_inputs:
        if not isinstance(item, dict):
            raise PackagingError("release Git gate recorded an invalid input")
        label = item.get("label")
        relative = item.get("path")
        object_hash = item.get("object")
        if (
            not isinstance(label, str)
            or not isinstance(relative, str)
            or not isinstance(object_hash, str)
        ):
            raise PackagingError("release Git gate recorded an uncommitted input")
        inputs[label] = relative

    destination = destination.resolve(strict=False)
    if destination.exists():
        raise PackagingError(f"release input snapshot already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    archive_path = destination.parent / f".{destination.name}.git-archive.tar"
    pathspecs = _minimal_pathspecs(list(inputs.values()))
    try:
        with archive_path.open("xb") as archive_handle:
            completed = subprocess.run(
                [
                    "git",
                    "-c",
                    "tar.umask=0002",
                    "-C",
                    str(root),
                    "archive",
                    "--format=tar",
                    head,
                    "--",
                    *pathspecs,
                ],
                stdout=archive_handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise PackagingError(f"could not archive committed release inputs: {detail}")
        with tarfile.open(archive_path, mode="r:") as archive:
            seen: set[str] = set()
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                normalized = path.as_posix().rstrip("/")
                if path.is_absolute() or not path.parts or ".." in path.parts:
                    raise PackagingError(
                        f"Git archive contained an unsafe release input: {member.name!r}"
                    )
                if normalized in seen:
                    raise PackagingError(
                        f"Git archive contained a duplicate release input: {member.name!r}"
                    )
                seen.add(normalized)
                if not (member.isdir() or member.isfile() or member.issym()):
                    raise PackagingError(
                        f"Git archive contained an unsupported release input: {member.name!r}"
                    )
            def committed_mode_filter(
                member: tarfile.TarInfo, target: str
            ) -> tarfile.TarInfo | None:
                filtered = tarfile.data_filter(member, target)
                if filtered is not None and (member.isdir() or member.isfile()):
                    # The repository inventory uses the fixed Git-archive 0002
                    # normalization, including group-write bits on regular files.
                    filtered.mode = member.mode & 0o777
                return filtered

            archive.extractall(destination, filter=committed_mode_filter)
    finally:
        try:
            archive_path.unlink()
        except FileNotFoundError:
            pass

    materialized: dict[str, Path] = {}
    for label, relative in inputs.items():
        path = destination / relative
        if not path.exists():
            raise PackagingError(f"committed release input was not materialized: {label}")
        materialized[label] = path
    return materialized


def evaluate_release_gate(
    *,
    manifest: Mapping[str, Any],
    source: Path,
    packaging_dir: Path,
    profile_path: Path,
    expected_dir: Path,
    upstream_path: Path,
    inventory_path: Path,
) -> dict[str, Any]:
    """Evaluate every condition required before a release may be publishable."""
    attestation = release_attestation_state(manifest, packaging_dir / "patches")
    git_state = release_git_state(
        source=source,
        packaging_dir=packaging_dir,
        build_inputs={
            "runtime_profile": profile_path,
            "expected_manifests": expected_dir,
            "upstream_manifest": upstream_path,
            "skill_inventory": inventory_path,
        },
        fully_patched_release_tree=manifest["fully_patched_release_tree"],
    )
    errors = [*attestation["errors"], *git_state["errors"]]
    return {
        "ready": not errors,
        "errors": errors,
        "attestation": attestation,
        "git": git_state,
    }

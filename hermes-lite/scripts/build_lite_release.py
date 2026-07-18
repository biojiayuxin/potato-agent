#!/usr/bin/env python3
"""Build an immutable Hermes Lite wheel and separately hashed skill assets."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from _lite_common import (
    DEFAULT_PROFILE,
    LITE_ROOT,
    MANIFEST_DIR,
    LiteReleaseError,
    canonical_json_bytes,
    copy_inventory_files,
    ensure_new_output_path,
    ensure_non_production_path,
    load_json_object,
    sha256_file,
    temporary_directory,
    tree_summary,
)
from verify_lite import (
    SOURCE_INVENTORY,
    normalize_site_packages,
    verify_source,
    verify_wheel,
)


BROWSER_ASSETS_MANIFEST = "browser-assets.json"


def _build_wheel(
    *, python: Path, source: Path, wheel_dir: Path, home: Path, build_tmp: Path
) -> Path:
    executable = Path(os.path.abspath(python.expanduser()))
    if not executable.is_file():
        raise LiteReleaseError(f"Python executable does not exist: {executable}")
    wheel_dir.mkdir(parents=True, mode=0o755)
    home.mkdir(parents=True, mode=0o700)
    build_tmp.mkdir(parents=True, mode=0o700)
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
        raise LiteReleaseError(
            f"Hermes Lite wheel build failed with exit {completed.returncode}: {detail}"
        )
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise LiteReleaseError(f"wheel build must produce exactly one wheel: {wheels}")
    return wheels[0]


def _copy_asset_tree(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_dir():
        raise LiteReleaseError(f"release asset source is missing: {source}")
    expected = tree_summary(source)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    actual = tree_summary(destination)
    if actual != expected:
        raise LiteReleaseError(f"copied asset tree differs from source: {source.name}")
    return {
        "file_count": actual["file_count"],
        "byte_count": actual["byte_count"],
        "tree_sha256": actual["tree_sha256"],
    }


def _load_browser_asset_contract(manifest_dir: Path) -> dict[str, Any]:
    value = load_json_object(manifest_dir / BROWSER_ASSETS_MANIFEST)
    if set(value) != {"schema_version", "agent_browser", "chrome_for_testing"}:
        raise LiteReleaseError("browser asset manifest has invalid keys")
    if value.get("schema_version") != 1:
        raise LiteReleaseError("browser asset manifest requires schema_version=1")

    agent = value.get("agent_browser")
    chrome = value.get("chrome_for_testing")
    if not isinstance(agent, dict) or set(agent) != {"path", "version", "sha256"}:
        raise LiteReleaseError("browser asset manifest has invalid agent_browser")
    if not isinstance(chrome, dict) or set(chrome) != {"path", "version", "archive"}:
        raise LiteReleaseError("browser asset manifest has invalid chrome_for_testing")
    archive = chrome.get("archive")
    if not isinstance(archive, dict) or set(archive) != {"url", "size", "sha256"}:
        raise LiteReleaseError("browser asset manifest has invalid Chrome archive")

    if agent.get("path") != "browser/bin/agent-browser":
        raise LiteReleaseError("browser asset manifest has an unexpected agent-browser path")
    if chrome.get("path") != "browser/chrome/chrome-linux64/chrome":
        raise LiteReleaseError("browser asset manifest has an unexpected Chrome path")
    for item, label in ((agent, "agent-browser"), (archive, "Chrome archive")):
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise LiteReleaseError(f"browser asset manifest has invalid {label} SHA256")
    if not isinstance(agent.get("version"), str) or not agent["version"]:
        raise LiteReleaseError("browser asset manifest has invalid agent-browser version")
    if not isinstance(chrome.get("version"), str) or not chrome["version"]:
        raise LiteReleaseError("browser asset manifest has invalid Chrome version")
    if not isinstance(archive.get("url"), str) or not archive["url"].startswith(
        "https://storage.googleapis.com/chrome-for-testing-public/"
    ):
        raise LiteReleaseError("browser asset manifest has invalid Chrome archive URL")
    if (
        isinstance(archive.get("size"), bool)
        or not isinstance(archive.get("size"), int)
        or archive["size"] <= 0
    ):
        raise LiteReleaseError("browser asset manifest has invalid Chrome archive size")
    return value


def _executable_version(path: Path, *, expected: str, label: str) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise LiteReleaseError(f"missing {label} executable: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
        raise LiteReleaseError(f"{label} must be a regular executable: {path}")
    completed = subprocess.run(
        [str(path), "--version"],
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
    )
    output = " ".join((completed.stdout + " " + completed.stderr).split())
    if completed.returncode != 0 or expected not in output:
        raise LiteReleaseError(
            f"{label} version mismatch: expected output containing {expected!r}, "
            f"got {output!r}"
        )
    return output


def _validate_browser_assets(
    browser_assets: Path, manifest_dir: Path
) -> tuple[Path, dict[str, Any]]:
    raw_root = browser_assets.expanduser()
    if raw_root.is_symlink():
        raise LiteReleaseError(f"browser asset root must not be a symlink: {raw_root}")
    root = raw_root.resolve()
    browser_root = root / "browser"
    if not browser_root.is_dir() or browser_root.is_symlink():
        raise LiteReleaseError(f"browser asset tree is missing: {browser_root}")
    contract = _load_browser_asset_contract(manifest_dir)
    agent = contract["agent_browser"]
    chrome = contract["chrome_for_testing"]
    agent_path = root / agent["path"]
    chrome_path = root / chrome["path"]
    agent_output = _executable_version(
        agent_path,
        expected=f"agent-browser {agent['version']}",
        label="agent-browser",
    )
    if sha256_file(agent_path) != agent["sha256"]:
        raise LiteReleaseError("agent-browser SHA256 differs from browser-assets.json")
    chrome_output = _executable_version(
        chrome_path,
        expected=f"Chrome for Testing {chrome['version']}",
        label="Chrome for Testing",
    )
    return browser_root, {
        "contract_sha256": sha256_file(manifest_dir / BROWSER_ASSETS_MANIFEST),
        "agent_browser": {
            "path": agent["path"],
            "sha256": agent["sha256"],
            "version": agent["version"],
            "version_output": agent_output,
        },
        "chrome_for_testing": {
            "path": chrome["path"],
            "version": chrome["version"],
            "version_output": chrome_output,
            "archive": dict(chrome["archive"]),
        },
    }


def _guard_build_paths(source: Path, output: Path | None, work_dir: Path | None) -> None:
    source = source.resolve()
    for value, label in ((output, "release output"), (work_dir, "work directory")):
        if value is None:
            continue
        resolved = ensure_non_production_path(value, label=label)
        if resolved == source or source in resolved.parents:
            raise LiteReleaseError(f"{label} must not be inside source tree: {resolved}")


def build_release(
    *,
    source: Path,
    output: Path | None,
    python: Path,
    profile: Path,
    manifest_dir: Path,
    site_packages: Sequence[Path] | None,
    work_dir: Path | None,
    browser_assets: Path | None,
    dry_run: bool,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    manifest_dir = manifest_dir.expanduser().resolve()
    profile = profile.expanduser().resolve()
    _guard_build_paths(source, output, work_dir)
    explicit_sites = normalize_site_packages(python, site_packages)
    browser_root = None
    browser_contract = None
    if browser_assets is not None:
        browser_root, browser_contract = _validate_browser_assets(
            browser_assets, manifest_dir
        )
    source_result = verify_source(
        source=source,
        manifest_dir=manifest_dir,
        python=python,
        profile=profile,
        site_packages=explicit_sites,
        probe=True,
    )
    plan: dict[str, Any] = {
        "dry_run": dry_run,
        "project": source_result["project"],
        "source_inventory": source_result["inventory"],
        "profile": str(profile),
        "profile_sha256": sha256_file(profile),
        "python": str(Path(os.path.abspath(python.expanduser()))),
        "python_probe_flags": ["-S", "-B", "-P"],
        "site_packages": [str(path) for path in explicit_sites],
        "browser_assets": browser_contract,
    }
    if output is not None:
        plan["output"] = str(output.expanduser().resolve(strict=False))
    if dry_run:
        return plan
    if output is None:
        raise LiteReleaseError("--output is required unless --dry-run is used")

    destination = ensure_new_output_path(output, label="release output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.staging-{uuid.uuid4().hex}")
    if staging.exists():
        raise LiteReleaseError(f"release staging path already exists: {staging}")

    source_manifest = load_json_object(manifest_dir / SOURCE_INVENTORY)
    try:
        staging.mkdir(mode=0o755)
        with temporary_directory(
            prefix="potato-hermes-lite-build-", parent=work_dir
        ) as raw_work:
            work = Path(raw_work)
            copied_source = work / "source"
            copy_inventory_files(source, copied_source, source_manifest)
            copied_source_result = verify_source(
                source=copied_source,
                manifest_dir=manifest_dir,
                python=python,
                profile=profile,
                site_packages=explicit_sites,
                probe=True,
            )

            built_wheel = _build_wheel(
                python=python,
                source=copied_source,
                wheel_dir=work / "wheel",
                home=work / "home",
                build_tmp=work / "tmp",
            )
            wheel_result = verify_wheel(
                wheel=built_wheel,
                source=copied_source,
                manifest_dir=manifest_dir,
                python=python,
                profile=profile,
                site_packages=explicit_sites,
                probe=True,
            )

            wheel_dir = staging / "wheel"
            wheel_dir.mkdir(mode=0o755)
            release_wheel = wheel_dir / built_wheel.name
            shutil.copy2(built_wheel, release_wheel)
            if sha256_file(release_wheel) != wheel_result["wheel_sha256"]:
                raise LiteReleaseError("release wheel copy failed SHA256 verification")

            share = staging / "share" / "hermes"
            share.mkdir(parents=True, mode=0o755)
            assets = {
                name: _copy_asset_tree(copied_source / name, share / name)
                for name in ("skills", "optional-skills")
            }
            browser_release = None
            if browser_root is not None and browser_contract is not None:
                browser_tree = _copy_asset_tree(browser_root, staging / "browser")
                _copied_root, copied_contract = _validate_browser_assets(
                    staging, manifest_dir
                )
                if copied_contract != browser_contract:
                    raise LiteReleaseError(
                        "copied browser executables differ from validated inputs"
                    )
                browser_release = {
                    **copied_contract,
                    "tree": browser_tree,
                }

            config = staging / "config"
            config.mkdir(mode=0o755)
            shutil.copy2(profile, config / "runtime-profile.yaml")
            shutil.copytree(manifest_dir, config / "manifests", copy_function=shutil.copy2)

            release_manifest = {
                "schema_version": 1,
                "created_at_unix": int(time.time()),
                "project": copied_source_result["project"],
                "source_inventory": copied_source_result["inventory"],
                "wheel": {
                    "filename": release_wheel.name,
                    "sha256": wheel_result["wheel_sha256"],
                    "size": release_wheel.stat().st_size,
                    "inventory": wheel_result["inventory"],
                },
                "runtime_profile": {
                    "path": "config/runtime-profile.yaml",
                    "sha256": sha256_file(config / "runtime-profile.yaml"),
                },
                "assets": assets,
                "browser_assets": browser_release,
                "verification": {
                    "python_flags": ["-S", "-B", "-P"],
                    "logical_model_tools": wheel_result["probe"]["tools"],
                    "forbidden_imports": wheel_result["probe"]["forbidden_imports"],
                    "origins": wheel_result["probe"]["origins"],
                },
            }
            (staging / "manifest.json").write_bytes(
                canonical_json_bytes(release_manifest)
            )
        os.replace(staging, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        **plan,
        "dry_run": False,
        "manifest": str(destination / "manifest.json"),
        "wheel": str(destination / "wheel" / release_wheel.name),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=LITE_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR)
    parser.add_argument("--site-packages", type=Path, action="append", default=[])
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--browser-assets", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_release(
            source=args.source,
            output=args.output,
            python=args.python,
            profile=args.profile,
            manifest_dir=args.manifest_dir,
            site_packages=args.site_packages,
            work_dir=args.work_dir,
            browser_assets=args.browser_assets,
            dry_run=args.dry_run,
        )
    except (LiteReleaseError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

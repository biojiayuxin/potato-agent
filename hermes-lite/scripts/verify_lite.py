#!/usr/bin/env python3
"""Verify Hermes Lite source and wheel boundaries against exact manifests."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Mapping, Sequence

from _lite_common import (
    DEFAULT_PROFILE,
    LITE_ROOT,
    MANIFEST_DIR,
    LiteReleaseError,
    compare_inventory,
    load_json_object,
    materialize_wheel,
    sha256_file,
    source_inventory,
    wheel_inventory,
    write_json_object,
)


PROJECT_NAME = "potato-hermes-lite"
PROBE_PREFIX = "POTATO_HERMES_LITE_PROBE="
SOURCE_INVENTORY = "source-inventory.json"
WHEEL_INVENTORY = "wheel-inventory.json"
FORBIDDEN_PATHS = "forbidden-paths.json"
CONSOLE_ENTRYPOINTS = "console-entrypoints.json"
DIRECT_DEPENDENCIES = "direct-dependencies.json"
MODEL_TOOLS = "model-tools.txt"


def _load_manifest(manifest_dir: Path, name: str) -> dict[str, Any]:
    return load_json_object(manifest_dir / name)


def _read_manifest_lines(path: Path) -> list[str]:
    try:
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except (FileNotFoundError, OSError) as exc:
        raise LiteReleaseError(f"cannot read line manifest {path}: {exc}") from exc
    if len(lines) != len(set(lines)):
        raise LiteReleaseError(f"line manifest contains duplicates: {path}")
    return lines


def _validate_simple_manifest(
    value: Mapping[str, Any], *, field: str, value_type: type
) -> Any:
    if set(value) != {"schema_version", field} or value.get("schema_version") != 1:
        raise LiteReleaseError(
            f"manifest must contain exactly schema_version=1 and {field}"
        )
    result = value[field]
    if not isinstance(result, value_type):
        raise LiteReleaseError(f"manifest field {field} has the wrong type")
    return result


def _load_project(source: Path) -> dict[str, Any]:
    path = source / "pyproject.toml"
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        project = value["project"]
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError, KeyError) as exc:
        raise LiteReleaseError(f"invalid Hermes Lite pyproject.toml: {exc}") from exc
    if not isinstance(project, dict):
        raise LiteReleaseError("pyproject [project] must be a table")
    return project


def _requirement_key(raw: str) -> tuple[str, tuple[str, ...], str, str, str]:
    try:
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name
    except ImportError as exc:
        raise LiteReleaseError(
            "the packaging module is required to compare wheel dependencies"
        ) from exc
    try:
        requirement = Requirement(raw)
    except Exception as exc:
        raise LiteReleaseError(f"invalid dependency requirement {raw!r}: {exc}") from exc
    return (
        canonicalize_name(requirement.name),
        tuple(sorted(extra.lower() for extra in requirement.extras)),
        str(requirement.specifier),
        str(requirement.marker or ""),
        str(requirement.url or ""),
    )


def _validate_project_contract(
    source: Path, manifest_dir: Path
) -> dict[str, Any]:
    project = _load_project(source)
    if project.get("name") != PROJECT_NAME:
        raise LiteReleaseError(
            f"unexpected project name: expected {PROJECT_NAME!r}, got {project.get('name')!r}"
        )
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise LiteReleaseError("project version must be a non-empty string")

    dependency_manifest = _load_manifest(manifest_dir, DIRECT_DEPENDENCIES)
    expected_dependencies = _validate_simple_manifest(
        dependency_manifest, field="requirements", value_type=list
    )
    if not all(isinstance(item, str) and item for item in expected_dependencies):
        raise LiteReleaseError("direct dependency manifest entries must be strings")
    actual_dependencies = project.get("dependencies")
    if actual_dependencies != expected_dependencies:
        raise LiteReleaseError(
            "pyproject direct dependencies differ from direct-dependencies.json"
        )
    if "optional-dependencies" in project:
        raise LiteReleaseError("Hermes Lite must not declare optional dependency extras")

    entrypoint_manifest = _load_manifest(manifest_dir, CONSOLE_ENTRYPOINTS)
    expected_scripts = _validate_simple_manifest(
        entrypoint_manifest, field="project_scripts", value_type=dict
    )
    if not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and value
        for key, value in expected_scripts.items()
    ):
        raise LiteReleaseError("console entrypoint manifest must map names to targets")
    scripts = project.get("scripts")
    if scripts != expected_scripts:
        raise LiteReleaseError(
            "pyproject console scripts differ from console-entrypoints.json"
        )
    return {
        "name": PROJECT_NAME,
        "version": version,
        "dependencies": list(expected_dependencies),
        "scripts": dict(expected_scripts),
    }


def _validate_forbidden_manifest(value: Mapping[str, Any]) -> dict[str, list[str]]:
    fields = {
        "schema_version",
        "source_paths",
        "source_prefixes",
        "wheel_paths",
        "wheel_prefixes",
        "module_prefixes",
    }
    if set(value) != fields or value.get("schema_version") != 1:
        raise LiteReleaseError(
            "forbidden path manifest has invalid keys or schema_version"
        )
    result: dict[str, list[str]] = {}
    for field in sorted(fields - {"schema_version"}):
        items = value[field]
        if (
            not isinstance(items, list)
            or not all(isinstance(item, str) and item for item in items)
            or items != sorted(set(items))
        ):
            raise LiteReleaseError(
                f"forbidden manifest {field} must be sorted unique strings"
            )
        result[field] = list(items)
    return result


def _check_forbidden_paths(
    paths: Sequence[str], *, exact: Sequence[str], prefixes: Sequence[str], label: str
) -> None:
    leaked = sorted(
        path
        for path in paths
        if path in exact or any(path.startswith(prefix) for prefix in prefixes)
    )
    if leaked:
        raise LiteReleaseError(f"forbidden {label} paths are present: {leaked[:30]}")


def discover_site_packages(python: Path) -> list[Path]:
    executable = Path(os.path.abspath(python.expanduser()))
    if not executable.is_file():
        raise LiteReleaseError(f"Python executable does not exist: {executable}")
    code = (
        "import json,site,sysconfig; "
        "paths=list(site.getsitepackages()); "
        "paths.extend([sysconfig.get_path('purelib'),sysconfig.get_path('platlib')]); "
        "print(json.dumps(paths))"
    )
    completed = subprocess.run(
        [str(executable), "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise LiteReleaseError(f"could not discover site-packages: {detail}")
    try:
        raw_paths = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise LiteReleaseError("site-packages discovery returned invalid JSON") from exc
    paths: list[Path] = []
    for raw in raw_paths:
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw).resolve()
        if path.is_dir() and path not in paths:
            paths.append(path)
    if not paths:
        raise LiteReleaseError("no usable site-packages directories were discovered")
    return paths


def normalize_site_packages(
    python: Path, site_packages: Sequence[Path] | None
) -> list[Path]:
    if not site_packages:
        return discover_site_packages(python)
    result: list[Path] = []
    for raw in site_packages:
        path = raw.expanduser().resolve()
        if not path.is_dir():
            raise LiteReleaseError(f"site-packages directory does not exist: {path}")
        if path not in result:
            result.append(path)
    return result


def _probe_code() -> str:
    return r'''
import json
import os
import pathlib
import sys

injection = pathlib.Path(os.environ["POTATO_LITE_IMPORT_ROOT"]).resolve()
site_packages = [pathlib.Path(item).resolve() for item in json.loads(os.environ["POTATO_LITE_SITE_PACKAGES"])]

# -S prevents site.py and .pth processing. Add only the release tree and the
# explicitly declared dependency directories.
sys.path = [item for item in sys.path if item and pathlib.Path(item).resolve() != pathlib.Path.cwd().resolve()]
for path in reversed(site_packages):
    sys.path.insert(0, str(path))
sys.path.insert(0, str(injection))

import runtime_profile
from runtime_profile import get_runtime_profile
import agent.codex_runtime as codex_runtime
import model_tools
import run_agent
import potato_hermes_lite.cli as lite_cli
import providers
import tools.registry as registry_module
from tools.registry import registry
import tui_gateway.entry as gateway_entry

profile = get_runtime_profile()
if profile is None:
    raise RuntimeError("runtime profile was not activated")
provider = providers.get_provider_profile("custom")
if provider is None or provider.name != "custom":
    raise RuntimeError("custom provider was not packaged")

for entry in registry._snapshot_entries():
    entry.check_fn = None
model_tools._clear_tool_defs_cache()
definitions = model_tools.get_tool_definitions(
    enabled_toolsets=list(profile.enabled_toolsets),
    disabled_toolsets=list(profile.disabled_toolsets),
    quiet_mode=True,
    skip_tool_search_assembly=True,
)
tools = [item["function"]["name"] for item in definitions]
expected_tools = json.loads(os.environ["POTATO_LITE_EXPECTED_TOOLS"])
if len(tools) != len(set(tools)) or len(tools) != len(expected_tools) or set(tools) != set(expected_tools):
    raise RuntimeError(f"tool surface mismatch: expected={expected_tools!r}, actual={tools!r}")

forbidden = json.loads(os.environ["POTATO_LITE_FORBIDDEN_MODULES"])
loaded_forbidden = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
if loaded_forbidden:
    raise RuntimeError("forbidden modules imported: " + ", ".join(loaded_forbidden))

owned = {
    "agent.codex_runtime": codex_runtime,
    "runtime_profile": runtime_profile,
    "model_tools": model_tools,
    "run_agent": run_agent,
    "potato_hermes_lite.cli": lite_cli,
    "providers": providers,
    "tools.registry": registry_module,
    "tui_gateway.entry": gateway_entry,
}
origins = {}
root_prefix = str(injection) + os.sep
for name, module in owned.items():
    origin = str(pathlib.Path(module.__file__).resolve())
    if not origin.startswith(root_prefix):
        raise RuntimeError(f"{name} imported outside isolated release tree: {origin}")
    origins[name] = origin[len(root_prefix):]

sys_path = [str(pathlib.Path(item).resolve()) for item in sys.path if item]
for item in sys_path:
    item_path = pathlib.Path(item)
    if any(part in {"hermes-agent", "hermes-agent-src"} for part in item_path.parts) or (
        item.endswith("hermes-lite") and pathlib.Path(item).resolve() != injection
    ):
        raise RuntimeError(f"editable Hermes source leaked into sys.path: {item}")

print("POTATO_HERMES_LITE_PROBE=" + json.dumps({
    "tools": tools,
    "origins": origins,
    "site_packages": [str(path) for path in site_packages],
    "forbidden_imports": [],
}, sort_keys=True))
'''


def run_isolated_probe(
    *,
    import_root: Path,
    python: Path,
    profile: Path,
    manifest_dir: Path,
    site_packages: Sequence[Path] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    import_root = import_root.expanduser().resolve()
    if not import_root.is_dir():
        raise LiteReleaseError(f"probe import root does not exist: {import_root}")
    profile = profile.expanduser().resolve()
    if not profile.is_file():
        raise LiteReleaseError(f"runtime profile does not exist: {profile}")
    explicit_sites = normalize_site_packages(python, site_packages)
    expected_tools = _read_manifest_lines(manifest_dir / MODEL_TOOLS)
    forbidden = _validate_forbidden_manifest(
        _load_manifest(manifest_dir, FORBIDDEN_PATHS)
    )["module_prefixes"]
    executable = Path(os.path.abspath(python.expanduser()))
    env = os.environ.copy()
    env.update(
        {
            "HERMES_RUNTIME_PROFILE_PATH": str(profile),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "HERMES_DISABLE_GATEWAY_PLATFORMS": "1",
            "HERMES_DISABLE_MCP": "1",
            "HERMES_DISABLE_CRON": "1",
            "HERMES_DISABLE_KANBAN": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "POTATO_LITE_IMPORT_ROOT": str(import_root),
            "POTATO_LITE_SITE_PACKAGES": json.dumps(
                [str(path) for path in explicit_sites]
            ),
            "POTATO_LITE_EXPECTED_TOOLS": json.dumps(expected_tools),
            "POTATO_LITE_FORBIDDEN_MODULES": json.dumps(forbidden),
        }
    )
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="potato-hermes-lite-probe-") as raw_home:
        home = Path(raw_home)
        env["HOME"] = str(home)
        env["HERMES_HOME"] = str(home / ".hermes")
        completed = subprocess.run(
            [str(executable), "-S", "-B", "-P", "-c", _probe_code()],
            cwd=home,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    captured = (completed.stdout + "\n" + completed.stderr).splitlines()
    marker = next(
        (line for line in reversed(captured) if line.startswith(PROBE_PREFIX)), None
    )
    if completed.returncode != 0 or marker is None:
        detail = "\n".join(captured[-30:])
        raise LiteReleaseError(
            f"isolated python -S probe failed with exit {completed.returncode}: {detail}"
        )
    try:
        result = json.loads(marker[len(PROBE_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise LiteReleaseError("isolated probe returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise LiteReleaseError("isolated probe result must be an object")
    result["python_flags"] = ["-S", "-B", "-P"]
    return result


def verify_source(
    *,
    source: Path,
    manifest_dir: Path,
    python: Path,
    profile: Path,
    site_packages: Sequence[Path] | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    manifest_dir = manifest_dir.expanduser().resolve()
    actual = source_inventory(source)
    expected = _load_manifest(manifest_dir, SOURCE_INVENTORY)
    inventory_result = compare_inventory(expected, actual, label="source")
    forbidden = _validate_forbidden_manifest(
        _load_manifest(manifest_dir, FORBIDDEN_PATHS)
    )
    paths = [item["path"] for item in actual["files"]]
    _check_forbidden_paths(
        paths,
        exact=forbidden["source_paths"],
        prefixes=forbidden["source_prefixes"],
        label="source",
    )
    project = _validate_project_contract(source, manifest_dir)
    result: dict[str, Any] = {
        "source": str(source),
        "inventory": inventory_result,
        "project": project,
        "forbidden_paths": [],
    }
    if probe:
        result["probe"] = run_isolated_probe(
            import_root=source,
            python=python,
            profile=profile,
            manifest_dir=manifest_dir,
            site_packages=site_packages,
        )
    return result


def _wheel_metadata(wheel: Path) -> tuple[Any, dict[str, str]]:
    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata_paths = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            entrypoint_paths = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/entry_points.txt")
            ]
            if len(metadata_paths) != 1 or len(entrypoint_paths) != 1:
                raise LiteReleaseError(
                    "wheel must contain exactly one METADATA and entry_points.txt"
                )
            metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read_string(archive.read(entrypoint_paths[0]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, configparser.Error, zipfile.BadZipFile) as exc:
        raise LiteReleaseError(f"cannot read wheel metadata: {exc}") from exc
    scripts = dict(parser.items("console_scripts")) if parser.has_section("console_scripts") else {}
    return metadata, scripts


def _validate_wheel_metadata_contract(
    metadata: Any, scripts: Mapping[str, str], project: Mapping[str, Any]
) -> None:
    if metadata.get("Name") != PROJECT_NAME or metadata.get("Version") != project["version"]:
        raise LiteReleaseError(
            f"wheel identity mismatch: {metadata.get('Name')!r} {metadata.get('Version')!r}"
        )
    wheel_requirements = metadata.get_all("Requires-Dist") or []
    expected_keys = sorted(_requirement_key(item) for item in project["dependencies"])
    actual_keys = sorted(_requirement_key(item) for item in wheel_requirements)
    if actual_keys != expected_keys:
        raise LiteReleaseError(
            f"wheel direct dependencies differ: expected={expected_keys}, actual={actual_keys}"
        )
    if metadata.get_all("Provides-Extra"):
        raise LiteReleaseError("Hermes Lite wheel must not advertise optional extras")
    if dict(scripts) != project["scripts"]:
        raise LiteReleaseError(
            f"wheel console entrypoints differ: expected={project['scripts']}, "
            f"actual={dict(scripts)}"
        )


def verify_wheel(
    *,
    wheel: Path,
    source: Path,
    manifest_dir: Path,
    python: Path,
    profile: Path,
    site_packages: Sequence[Path] | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    wheel = wheel.expanduser().resolve()
    if not wheel.is_file():
        raise LiteReleaseError(f"wheel does not exist: {wheel}")
    project = _validate_project_contract(source, manifest_dir)
    actual = wheel_inventory(wheel)
    expected = _load_manifest(manifest_dir, WHEEL_INVENTORY)
    inventory_result = compare_inventory(expected, actual, label="wheel")
    forbidden = _validate_forbidden_manifest(
        _load_manifest(manifest_dir, FORBIDDEN_PATHS)
    )
    paths = [item["path"] for item in actual["files"]]
    _check_forbidden_paths(
        paths,
        exact=forbidden["wheel_paths"],
        prefixes=forbidden["wheel_prefixes"],
        label="wheel",
    )

    metadata, scripts = _wheel_metadata(wheel)
    _validate_wheel_metadata_contract(metadata, scripts, project)

    result: dict[str, Any] = {
        "wheel": str(wheel),
        "wheel_sha256": sha256_file(wheel),
        "inventory": inventory_result,
        "project": project,
        "forbidden_paths": [],
    }
    if probe:
        with tempfile.TemporaryDirectory(prefix="potato-hermes-lite-wheel-") as raw:
            import_root = materialize_wheel(wheel, Path(raw) / "installed")
            result["probe"] = run_isolated_probe(
                import_root=import_root,
                python=python,
                profile=profile,
                manifest_dir=manifest_dir,
                site_packages=site_packages,
            )
    return result


def write_source_inventory(*, source: Path, manifest_dir: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    manifest_dir = manifest_dir.expanduser().resolve()
    actual = source_inventory(source)
    forbidden = _validate_forbidden_manifest(
        _load_manifest(manifest_dir, FORBIDDEN_PATHS)
    )
    _check_forbidden_paths(
        [item["path"] for item in actual["files"]],
        exact=forbidden["source_paths"],
        prefixes=forbidden["source_prefixes"],
        label="source",
    )
    _validate_project_contract(source, manifest_dir)
    destination = manifest_dir / SOURCE_INVENTORY
    write_json_object(destination, actual)
    return {
        "path": str(destination),
        "file_count": len(actual["files"]),
        "sha256": sha256_file(destination),
    }


def write_wheel_inventory(*, wheel: Path, manifest_dir: Path) -> dict[str, Any]:
    wheel = wheel.expanduser().resolve()
    manifest_dir = manifest_dir.expanduser().resolve()
    actual = wheel_inventory(wheel)
    forbidden = _validate_forbidden_manifest(
        _load_manifest(manifest_dir, FORBIDDEN_PATHS)
    )
    _check_forbidden_paths(
        [item["path"] for item in actual["files"]],
        exact=forbidden["wheel_paths"],
        prefixes=forbidden["wheel_prefixes"],
        label="wheel",
    )
    destination = manifest_dir / WHEEL_INVENTORY
    write_json_object(destination, actual)
    return {
        "path": str(destination),
        "file_count": len(actual["files"]),
        "sha256": sha256_file(destination),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=LITE_ROOT)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--site-packages", type=Path, action="append", default=[])
    parser.add_argument("--write-source-inventory", action="store_true")
    parser.add_argument("--write-wheel-inventory", action="store_true")
    parser.add_argument("--no-probe", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.write_source_inventory and args.write_wheel_inventory:
            raise LiteReleaseError("choose only one inventory write operation")
        if args.write_source_inventory:
            result = write_source_inventory(
                source=args.source, manifest_dir=args.manifest_dir
            )
        elif args.write_wheel_inventory:
            if args.wheel is None:
                raise LiteReleaseError("--write-wheel-inventory requires --wheel")
            # Metadata and the isolated probe must pass before blessing paths.
            project = _validate_project_contract(args.source, args.manifest_dir)
            metadata, scripts = _wheel_metadata(args.wheel)
            _validate_wheel_metadata_contract(metadata, scripts, project)
            if not args.no_probe:
                with tempfile.TemporaryDirectory(
                    prefix="potato-hermes-lite-wheel-bootstrap-"
                ) as raw:
                    import_root = materialize_wheel(args.wheel, Path(raw) / "installed")
                    run_isolated_probe(
                        import_root=import_root,
                        python=args.python,
                        profile=args.profile,
                        manifest_dir=args.manifest_dir,
                        site_packages=args.site_packages,
                    )
            result = write_wheel_inventory(
                wheel=args.wheel, manifest_dir=args.manifest_dir
            )
        else:
            result = {
                "source": verify_source(
                    source=args.source,
                    manifest_dir=args.manifest_dir,
                    python=args.python,
                    profile=args.profile,
                    site_packages=args.site_packages,
                    probe=not args.no_probe,
                )
            }
            if args.wheel is not None:
                result["wheel"] = verify_wheel(
                    wheel=args.wheel,
                    source=args.source,
                    manifest_dir=args.manifest_dir,
                    python=args.python,
                    profile=args.profile,
                    site_packages=args.site_packages,
                    probe=not args.no_probe,
                )
    except (LiteReleaseError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

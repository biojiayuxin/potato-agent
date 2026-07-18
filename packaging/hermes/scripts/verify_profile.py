#!/usr/bin/env python3
"""Verify the Potato Hermes profile, tool surface, imports, and skill hashes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _common import (
    PACKAGING_DIR,
    PackagingError,
    canonical_json_bytes,
    ensure_new_output_path,
    load_upstream_manifest,
    load_yaml_mapping,
    read_manifest_lines,
    sha256_file,
    skill_tree_summaries,
    validate_skill_inventory,
)
from release_gate import evaluate_release_gate


EXPECTED_TOOL_COUNT = 27
FORBIDDEN_MODEL_TOOLS = {
    "clarify",
    "web_search",
    "web_extract",
    "send_message",
    "cronjob",
    "kanban",
    "mixture_of_agents",
    "text_to_speech",
    "image_generate",
    "video_generate",
}
PROBE_PREFIX = "POTATO_PROFILE_PROBE="


def _validate_profile_configuration(
    profile_path: Path, tools_path: Path, forbidden_path: Path
) -> tuple[dict[str, Any], list[str], list[str]]:
    profile = load_yaml_mapping(profile_path)
    expected = read_manifest_lines(tools_path)
    forbidden = read_manifest_lines(forbidden_path)

    profile_tools = profile.get("expected_tools")
    if not isinstance(profile_tools, list) or not all(
        isinstance(item, str) and item for item in profile_tools
    ):
        raise PackagingError("runtime profile expected_tools must be a list of names")
    if profile_tools != expected:
        raise PackagingError("runtime profile expected_tools differs from expected/tools.txt")
    if len(expected) != EXPECTED_TOOL_COUNT:
        raise PackagingError(
            f"Potato profile must expose exactly {EXPECTED_TOOL_COUNT} tools, got {len(expected)}"
        )
    leaked = sorted(set(expected) & FORBIDDEN_MODEL_TOOLS)
    if leaked:
        raise PackagingError("forbidden model tools are present: " + ", ".join(leaked))

    providers = profile.get("providers")
    plugins = profile.get("plugins")
    runtime = profile.get("runtime")
    mcp = profile.get("mcp")
    fixed = {
        "providers.model": (providers or {}).get("model"),
        "providers.api_modes": (providers or {}).get("api_modes"),
        "providers.browser": (providers or {}).get("browser"),
        "providers.memory": (providers or {}).get("memory"),
        "providers.context_engine": (providers or {}).get("context_engine"),
        "providers.web": (providers or {}).get("web"),
        "plugins.allow_user": (plugins or {}).get("allow_user"),
        "plugins.allow_project": (plugins or {}).get("allow_project"),
        "plugins.allow_entrypoint": (plugins or {}).get("allow_entrypoint"),
        "plugins.allowed_general_keys": (plugins or {}).get("allowed_general_keys"),
        "mcp.enabled": (mcp or {}).get("enabled"),
        "runtime.allow_lazy_installs": (runtime or {}).get("allow_lazy_installs"),
        "runtime.lsp_install_strategy": (runtime or {}).get("lsp_install_strategy"),
        "runtime.terminal_backend": (runtime or {}).get("terminal_backend"),
        "runtime.skills_dependency_strategy": (runtime or {}).get(
            "skills_dependency_strategy"
        ),
    }
    required = {
        "providers.model": ["custom"],
        "providers.api_modes": ["codex_responses", "chat_completions"],
        "providers.browser": "local",
        "providers.memory": "builtin",
        "providers.context_engine": "compressor",
        "providers.web": [],
        "plugins.allow_user": False,
        "plugins.allow_project": False,
        "plugins.allow_entrypoint": False,
        "plugins.allowed_general_keys": [],
        "mcp.enabled": False,
        "runtime.allow_lazy_installs": False,
        "runtime.lsp_install_strategy": "manual",
        "runtime.terminal_backend": "local",
        "runtime.skills_dependency_strategy": "user_managed",
    }
    mismatches = [
        f"{field}: expected {required[field]!r}, got {actual!r}"
        for field, actual in fixed.items()
        if actual != required[field]
    ]
    kinds = (plugins or {}).get("forbidden_kinds")
    if not isinstance(kinds, list) or "platform" not in kinds:
        mismatches.append("plugins.forbidden_kinds must include 'platform'")
    if mismatches:
        raise PackagingError("runtime profile policy mismatch: " + "; ".join(mismatches))

    prefix_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
    invalid_prefixes = [prefix for prefix in forbidden if prefix_re.fullmatch(prefix) is None]
    if invalid_prefixes or not forbidden:
        raise PackagingError(
            "invalid or empty forbidden module prefix manifest: "
            + ", ".join(invalid_prefixes)
        )
    return profile, expected, forbidden


def _probe_code() -> str:
    return r'''
import json
import os
import sys

injected = os.environ.get("POTATO_WHEEL_IMPORT_PATH")
if injected:
    sys.path.insert(0, injected)

import runtime_profile
from runtime_profile import get_runtime_profile
import model_tools
import tools.registry as registry_module
from tools.registry import registry
import tui_gateway.entry as gateway_entry

profile = get_runtime_profile()
if profile is None:
    raise RuntimeError("runtime profile was not activated")

# Availability checks depend on deployment binaries and credentials. The release
# contract is the pre-availability model surface, so disable checks only inside
# this disposable validation process, then exercise the public assembler.
for entry in registry._snapshot_entries():
    entry.check_fn = None
checks_disabled = [entry.name for entry in registry._snapshot_entries() if entry.check_fn is None]
model_tools._clear_tool_defs_cache()
definitions = model_tools.get_tool_definitions(
    enabled_toolsets=list(profile.enabled_toolsets),
    disabled_toolsets=list(profile.disabled_toolsets),
    quiet_mode=True,
    skip_tool_search_assembly=True,
)
names = [item["function"]["name"] for item in definitions]
prefixes = json.loads(os.environ["POTATO_FORBIDDEN_PREFIXES"])
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)
)
print("POTATO_PROFILE_PROBE=" + json.dumps({
    "tools": names,
    "forbidden_imports": loaded,
    "runtime_profile_file": runtime_profile.__file__,
    "model_tools_file": model_tools.__file__,
    "registry_file": registry_module.__file__,
    "gateway_entry_file": gateway_entry.__file__,
    "checks_disabled": checks_disabled,
    "module_count": len(sys.modules),
}, sort_keys=True))
'''


def _run_probe(
    *,
    python: Path,
    profile_path: Path,
    forbidden: list[str],
    source: Path | None,
    wheel: Path | None,
    timeout: float,
) -> dict[str, Any]:
    # Keep a venv's python symlink intact; resolving it would silently execute
    # the base interpreter and drop the venv's installed dependencies.
    executable = Path(os.path.abspath(python.expanduser()))
    if not executable.is_file():
        raise PackagingError(f"Python executable does not exist: {executable}")
    env = os.environ.copy()
    env.update(
        {
            "HERMES_RUNTIME_PROFILE_PATH": str(profile_path.resolve()),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "POTATO_FORBIDDEN_PREFIXES": json.dumps(forbidden),
        }
    )
    if source is not None:
        env["PYTHONPATH"] = str(source.resolve())
    else:
        env.pop("PYTHONPATH", None)
    if wheel is not None:
        env["POTATO_WHEEL_IMPORT_PATH"] = str(wheel.resolve())
    else:
        env.pop("POTATO_WHEEL_IMPORT_PATH", None)

    with tempfile.TemporaryDirectory(prefix="potato-profile-verify-") as raw_home:
        home = Path(raw_home)
        env["HOME"] = str(home)
        env["HERMES_HOME"] = str(home / ".hermes")
        completed = subprocess.run(
            [str(executable), "-B", "-c", _probe_code()],
            cwd=home,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PackagingError(f"profile import probe failed with exit {completed.returncode}: {detail}")
    # tui_gateway.entry installs a diagnostic stdout tee during import, so the
    # marker may be routed to stderr. Accept either captured stream.
    captured_lines = (completed.stdout + "\n" + completed.stderr).splitlines()
    payload_line = next(
        (line for line in reversed(captured_lines) if line.startswith(PROBE_PREFIX)),
        None,
    )
    if payload_line is None:
        detail = "\n".join(captured_lines[-20:])
        raise PackagingError(
            "profile import probe did not emit its result marker"
            + (f": {detail}" if detail else "")
        )
    try:
        payload = json.loads(payload_line[len(PROBE_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise PackagingError(f"profile import probe returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PackagingError("profile import probe result must be an object")
    return payload


def verify(
    *,
    source: Path | None,
    installed: bool,
    wheel: Path | None,
    python: Path,
    profile_path: Path,
    expected_dir: Path,
    upstream_path: Path,
    inventory_path: Path,
    timeout: float,
    require_publishable: bool,
) -> dict[str, Any]:
    modes = sum((source is not None, installed, wheel is not None))
    if modes != 1:
        raise PackagingError("choose exactly one of --source, --installed, or --wheel")
    profile, expected, forbidden = _validate_profile_configuration(
        profile_path,
        expected_dir / "tools.txt",
        expected_dir / "forbidden-module-prefixes.txt",
    )
    upstream = load_upstream_manifest(upstream_path)
    if require_publishable and upstream["provenance_status"] != "verified":
        raise PackagingError(
            "release provenance is unverified: " + upstream["provenance_note"]
        )

    result: dict[str, Any] = {
        "mode": "source" if source is not None else "wheel" if wheel else "installed",
        "profile": str(profile_path.resolve()),
        "profile_sha256": sha256_file(profile_path),
        "expected_tool_count": len(expected),
        "expected_tools": expected,
        "forbidden_module_prefixes": forbidden,
        "provenance_status": upstream["provenance_status"],
        "publishable": False,
    }

    source_path: Path | None = None
    wheel_path: Path | None = None
    if source is not None:
        source_path = source.expanduser().resolve()
        if not source_path.is_dir():
            raise PackagingError(f"source directory does not exist: {source_path}")
        lock = source_path / "uv.lock"
        actual_lock = sha256_file(lock)
        if actual_lock != upstream["vendored_uv_lock_sha256"]:
            raise PackagingError(
                "vendored source uv.lock mismatch: expected "
                f"{upstream['vendored_uv_lock_sha256']}, got {actual_lock}"
            )
        result["vendored_uv_lock_sha256"] = actual_lock
        result["skills"] = validate_skill_inventory(source_path, inventory_path)
        release_gate = evaluate_release_gate(
            manifest=upstream,
            source=source_path,
            packaging_dir=PACKAGING_DIR,
            profile_path=profile_path,
            expected_dir=expected_dir,
            upstream_path=upstream_path,
            inventory_path=inventory_path,
        )
        result["release_gate"] = release_gate
        result["publishable"] = release_gate["ready"]
        if require_publishable and not release_gate["ready"]:
            raise PackagingError(
                "release is not publishable: " + "; ".join(release_gate["errors"])
            )
    elif wheel is not None:
        wheel_path = wheel.expanduser().resolve()
        if not wheel_path.is_file():
            raise PackagingError(f"wheel does not exist: {wheel_path}")
        result["wheel_sha256"] = sha256_file(wheel_path)
        if require_publishable:
            raise PackagingError(
                "--require-publishable requires --source so committed release inputs "
                "can be verified"
            )
    elif require_publishable:
        raise PackagingError(
            "--require-publishable requires --source so committed release inputs "
            "can be verified"
        )

    probe = _run_probe(
        python=python,
        profile_path=profile_path,
        forbidden=forbidden,
        source=source_path,
        wheel=wheel_path,
        timeout=timeout,
    )
    actual_tools = probe.get("tools")
    if not isinstance(actual_tools, list) or len(actual_tools) != len(set(actual_tools)):
        raise PackagingError("profile import probe returned invalid or duplicate tool names")
    if set(actual_tools) != set(expected) or len(actual_tools) != EXPECTED_TOOL_COUNT:
        missing = sorted(set(expected) - set(actual_tools))
        extra = sorted(set(actual_tools) - set(expected))
        raise PackagingError(
            f"model tool surface mismatch: missing={missing}, extra={extra}, "
            f"count={len(actual_tools)}"
        )
    forbidden_imports = probe.get("forbidden_imports")
    if forbidden_imports:
        raise PackagingError("forbidden modules were imported: " + ", ".join(forbidden_imports))
    result["logical_model_tools"] = actual_tools
    runtime_profile_file = str(probe.get("runtime_profile_file") or "")
    gateway_entry_file = str(probe.get("gateway_entry_file") or "")
    if source_path is not None:
        try:
            runtime_relative = Path(runtime_profile_file).resolve().relative_to(source_path)
            gateway_relative = Path(gateway_entry_file).resolve().relative_to(source_path)
        except ValueError as exc:
            raise PackagingError("source probe imported modules outside the requested source") from exc
        result["runtime_profile_origin"] = runtime_relative.as_posix()
        result["gateway_entry_origin"] = gateway_relative.as_posix()
    elif wheel_path is not None:
        wheel_prefix = str(wheel_path) + os.sep
        if not runtime_profile_file.startswith(wheel_prefix) or not gateway_entry_file.startswith(
            wheel_prefix
        ):
            raise PackagingError("sealed-wheel probe imported Hermes modules outside the wheel")
        result["runtime_profile_origin"] = runtime_profile_file[len(wheel_prefix) :]
        result["gateway_entry_origin"] = gateway_entry_file[len(wheel_prefix) :]
    else:
        result["runtime_profile_origin"] = "installed"
        result["gateway_entry_origin"] = "installed"
    result["module_count"] = probe.get("module_count")
    return result


def write_skill_inventory(source: Path, output: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise PackagingError(f"source directory does not exist: {source}")
    destination = ensure_new_output_path(output, label="skill inventory output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    value = {"schema_version": 1, "roots": skill_tree_summaries(source)}
    destination.write_bytes(canonical_json_bytes(value))
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--source", type=Path)
    mode.add_argument("--installed", action="store_true")
    mode.add_argument("--wheel", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--profile", type=Path, default=PACKAGING_DIR / "runtime-profile.yaml")
    parser.add_argument("--expected-dir", type=Path, default=PACKAGING_DIR / "expected")
    parser.add_argument("--upstream", type=Path, default=PACKAGING_DIR / "upstream.yaml")
    parser.add_argument(
        "--skill-inventory",
        type=Path,
        default=PACKAGING_DIR / "expected" / "skills-inventory.json",
    )
    parser.add_argument("--write-skill-inventory", type=Path)
    parser.add_argument("--require-publishable", action="store_true")
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.write_skill_inventory is not None:
            if args.source is None or args.installed or args.wheel is not None:
                raise PackagingError("--write-skill-inventory requires --source")
            result = write_skill_inventory(args.source, args.write_skill_inventory)
        else:
            result = verify(
                source=args.source,
                installed=args.installed,
                wheel=args.wheel,
                python=args.python,
                profile_path=args.profile,
                expected_dir=args.expected_dir,
                upstream_path=args.upstream,
                inventory_path=args.skill_inventory,
                timeout=args.timeout,
                require_publishable=args.require_publishable,
            )
    except (PackagingError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

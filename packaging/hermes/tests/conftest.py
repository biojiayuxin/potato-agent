from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


PACKAGING_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PACKAGING_DIR / "scripts"
REPO_ROOT = PACKAGING_DIR.parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _common import canonical_json_bytes, skill_tree_summaries  # noqa: E402


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def fake_profile_bundle(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "bundle"
    source = root / "source"
    expected = root / "expected"
    source.mkdir(parents=True)
    expected.mkdir()

    profile = root / "runtime-profile.yaml"
    shutil.copy2(PACKAGING_DIR / "runtime-profile.yaml", profile)
    shutil.copy2(PACKAGING_DIR / "expected" / "tools.txt", expected / "tools.txt")
    shutil.copy2(
        PACKAGING_DIR / "expected" / "forbidden-module-prefixes.txt",
        expected / "forbidden-module-prefixes.txt",
    )

    lock_bytes = b"fixture-lock\n"
    (source / "uv.lock").write_bytes(lock_bytes)
    (source / "pyproject.toml").write_text(
        """\
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "hermes-agent"
version = "0.16.0"
dependencies = ["pyyaml"]

[tool.setuptools]
py-modules = ["runtime_profile", "model_tools"]

[tool.setuptools.packages.find]
include = ["tools", "tui_gateway"]
""",
        encoding="utf-8",
    )
    shutil.copy2(REPO_ROOT / "hermes-agent" / "runtime_profile.py", source / "runtime_profile.py")
    (source / "model_tools.py").write_text(
        """\
from tools.registry import registry

def _clear_tool_defs_cache():
    return None

def get_tool_definitions(**_kwargs):
    return [
        {"type": "function", "function": {"name": entry.name}}
        for entry in registry._snapshot_entries()
    ]
""",
        encoding="utf-8",
    )
    tools = source / "tools"
    tools.mkdir()
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "registry.py").write_text(
        """\
from runtime_profile import get_runtime_profile

class Entry:
    def __init__(self, name):
        self.name = name
        self.check_fn = lambda: True

class Registry:
    def __init__(self):
        self._entries = [Entry(name) for name in get_runtime_profile().expected_tools]
    def _snapshot_entries(self):
        return list(self._entries)

registry = Registry()
""",
        encoding="utf-8",
    )
    gateway = source / "tui_gateway"
    gateway.mkdir()
    (gateway / "__init__.py").write_text("", encoding="utf-8")
    (gateway / "entry.py").write_text("", encoding="utf-8")

    (source / "skills" / "fixture").mkdir(parents=True)
    (source / "skills" / "fixture" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (source / "optional-skills" / "fixture").mkdir(parents=True)
    (source / "optional-skills" / "fixture" / "SKILL.md").write_text(
        "# Optional\n", encoding="utf-8"
    )
    # Git archive uses its deterministic 0002 tar umask. Match those materialized
    # modes so the fixture exercises the same mode-sensitive skill inventory.
    for path in source.rglob("*"):
        if path.is_dir():
            path.chmod(0o775)
        elif path.stat().st_mode & 0o111:
            path.chmod(0o775)
        else:
            path.chmod(0o664)
    inventory = expected / "skills-inventory.json"
    inventory.write_bytes(
        canonical_json_bytes({"schema_version": 1, "roots": skill_tree_summaries(source)})
    )

    upstream_data = {
        "schema_version": 2,
        "reference_kind": "baseline",
        "provenance_status": "verified",
        "provenance_note": "fixture provenance",
        "repository": "https://example.invalid/hermes-agent",
        "tag": "vfixture",
        "commit": "1" * 40,
        "tree": "2" * 40,
        "archive_url": "file:///unused",
        "archive_sha256": "3" * 64,
        "upstream_version": "0.16.0",
        "python": "3.12.3",
        "uv_lock_sha256": "4" * 64,
        "vendored_baseline_commit": "5" * 40,
        "vendored_tree": "6" * 40,
        "vendored_uv_lock_sha256": _sha(lock_bytes),
        "potato_revision": 1,
        "patch_series": [],
        "baseline_attestation": {
            "status": "pending",
            "statement_sha256": None,
        },
        "replay_attestation": {
            "status": "pending",
            "statement_sha256": None,
        },
        "fully_patched_release_tree": None,
    }
    upstream = root / "upstream.yaml"
    upstream.write_text(yaml.safe_dump(upstream_data, sort_keys=False), encoding="utf-8")
    return SimpleNamespace(
        root=root,
        source=source,
        expected=expected,
        profile=profile,
        inventory=inventory,
        upstream=upstream,
        upstream_data=upstream_data,
    )


@pytest.fixture
def build_python() -> Path:
    preferred = Path("/opt/hermes-agent-venv/bin/python3")
    if preferred.is_file():
        return preferred
    pytest.skip("the Hermes development interpreter is required for wheel tests")

from __future__ import annotations

import json
import subprocess
from email.message import EmailMessage
from pathlib import Path

import pytest

import verify_lite
from _lite_common import LiteReleaseError, source_inventory, write_json_object


def _manifests(root: Path) -> Path:
    manifests = root / "manifests"
    manifests.mkdir()
    write_json_object(
        manifests / "forbidden-paths.json",
        {
            "schema_version": 1,
            "source_paths": ["forbidden.py"],
            "source_prefixes": ["cron/"],
            "wheel_paths": ["forbidden.py"],
            "wheel_prefixes": ["cron/"],
            "module_prefixes": ["cron"],
        },
    )
    (manifests / "model-tools.txt").write_text("terminal\n", encoding="utf-8")
    return manifests


def test_forbidden_paths_are_fail_closed() -> None:
    with pytest.raises(LiteReleaseError, match="forbidden source paths"):
        verify_lite._check_forbidden_paths(
            ["ok.py", "cron/jobs.py"],
            exact=["forbidden.py"],
            prefixes=["cron/"],
            label="source",
        )


def test_isolated_probe_uses_python_s_and_explicit_site_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import_root = tmp_path / "installed"
    import_root.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text("name: fixture\n", encoding="utf-8")
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    manifests = _manifests(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        marker = verify_lite.PROBE_PREFIX + json.dumps(
            {
                "tools": ["terminal"],
                "origins": {},
                "site_packages": [str(site_packages)],
                "forbidden_imports": [],
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout=marker + "\n", stderr="")

    monkeypatch.setattr(verify_lite.subprocess, "run", fake_run)
    result = verify_lite.run_isolated_probe(
        import_root=import_root,
        python=Path("/bin/sh"),
        profile=profile,
        manifest_dir=manifests,
        site_packages=[site_packages],
    )

    assert captured["command"][1:4] == ["-S", "-B", "-P"]
    assert json.loads(captured["env"]["POTATO_LITE_SITE_PACKAGES"]) == [
        str(site_packages)
    ]
    assert "PYTHONPATH" not in captured["env"]
    assert result["python_flags"] == ["-S", "-B", "-P"]


def test_isolated_probe_owns_codex_runtime() -> None:
    probe = verify_lite._probe_code()

    assert "import agent.codex_runtime as codex_runtime" in probe
    assert '"agent.codex_runtime": codex_runtime' in probe


def test_project_contract_requires_exact_dependency_order(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        """\
[project]
name = "potato-hermes-lite"
version = "1.0"
dependencies = ["two==2", "one==1"]

[project.scripts]
hermes = "potato_hermes_lite.cli:main"
""",
        encoding="utf-8",
    )
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    write_json_object(
        manifests / "direct-dependencies.json",
        {"schema_version": 1, "requirements": ["one==1", "two==2"]},
    )
    write_json_object(
        manifests / "console-entrypoints.json",
        {
            "schema_version": 1,
            "project_scripts": {"hermes": "potato_hermes_lite.cli:main"},
        },
    )

    with pytest.raises(LiteReleaseError, match="direct dependencies differ"):
        verify_lite._validate_project_contract(source, manifests)


def test_wheel_metadata_contract_rejects_dependency_drift() -> None:
    metadata = EmailMessage()
    metadata["Name"] = "potato-hermes-lite"
    metadata["Version"] = "1.0"
    metadata["Requires-Dist"] = "one==1"
    project = {
        "version": "1.0",
        "dependencies": ["one==1", "two==2"],
        "scripts": {"hermes": "potato_hermes_lite.cli:main"},
    }

    with pytest.raises(LiteReleaseError, match="wheel direct dependencies differ"):
        verify_lite._validate_wheel_metadata_contract(
            metadata,
            {"hermes": "potato_hermes_lite.cli:main"},
            project,
        )

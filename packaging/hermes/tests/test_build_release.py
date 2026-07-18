from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from _common import PackagingError
import build_release as build_release_module
from build_release import build_release
from release_gate import (
    baseline_statement,
    replay_statement,
    statement_sha256,
)


def _build(bundle, python: Path, output: Path, **overrides):
    return build_release(
        source=bundle.source,
        output=output,
        python=python,
        profile_path=bundle.profile,
        expected_dir=bundle.expected,
        upstream_path=bundle.upstream,
        inventory_path=bundle.inventory,
        dry_run=False,
        allow_unverified_provenance=overrides.get("allow_unverified", False),
        allow_dirty_source=overrides.get("allow_dirty", True),
        work_dir=None,
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _attest_bundle(bundle) -> Path:
    packaging_dir = bundle.root / "release-packaging"
    patches = packaging_dir / "patches"
    patches.mkdir(parents=True)
    (patches / "series").write_text("# empty\n", encoding="utf-8")
    _git(bundle.root, "init")
    _git(bundle.root, "config", "user.email", "release-test@example.invalid")
    _git(bundle.root, "config", "user.name", "Release Test")
    _git(bundle.root, "add", "-A")
    _git(bundle.root, "commit", "-m", "initial inputs")

    data = yaml.safe_load(bundle.upstream.read_text(encoding="utf-8"))
    data["fully_patched_release_tree"] = _git(bundle.root, "rev-parse", "HEAD:source")
    data["baseline_attestation"] = {
        "status": "attested",
        "statement_sha256": statement_sha256(baseline_statement(data)),
    }
    data["replay_attestation"] = {
        "status": "attested",
        "statement_sha256": statement_sha256(replay_statement(data, patches)),
    }
    bundle.upstream.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    _git(bundle.root, "add", "-A")
    _git(bundle.root, "commit", "-m", "attest replay")
    return packaging_dir


def test_build_release_keeps_skills_outside_sealed_wheel(
    tmp_path: Path, fake_profile_bundle, build_python
) -> None:
    output = tmp_path / "release"
    result = _build(fake_profile_bundle, build_python, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert Path(result["wheel"]).is_file()
    assert (output / "share" / "hermes" / "skills" / "fixture" / "SKILL.md").is_file()
    assert (output / "share" / "hermes" / "optional-skills" / "fixture" / "SKILL.md").is_file()
    assert manifest["wheel"]["runtime_profile_packaged"] is True
    assert manifest["wheel"]["skills_embedded"] is False
    assert manifest["profile"]["runtime_profile_origin"] == "runtime_profile.py"
    assert manifest["profile"]["expected_tool_count"] == 27
    assert set(manifest["profile"]["expected_manifest_sha256"]) == {
        "forbidden-module-prefixes.txt",
        "skills-inventory.json",
        "tools.txt",
    }
    assert manifest["assets"]["skills"]["files"]
    assert manifest["publishable"] is False
    assert manifest["release_gate"]["attestation"]["baseline"]["status"] == "pending"


def test_build_release_rejects_verified_status_without_attested_replay(
    tmp_path: Path, fake_profile_bundle, build_python
) -> None:
    with pytest.raises(PackagingError, match="baseline attestation is pending"):
        _build(
            fake_profile_bundle,
            build_python,
            tmp_path / "blocked-status-only",
            allow_dirty=False,
        )


def test_build_release_requires_explicit_unverified_override(
    tmp_path: Path, fake_profile_bundle, build_python
) -> None:
    data = yaml.safe_load(fake_profile_bundle.upstream.read_text(encoding="utf-8"))
    data["provenance_status"] = "unverified"
    fake_profile_bundle.upstream.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(PackagingError, match="explicit --allow-unverified-provenance"):
        _build(fake_profile_bundle, build_python, tmp_path / "blocked")

    output = tmp_path / "dev-release"
    _build(fake_profile_bundle, build_python, output, allow_unverified=True)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["publishable"] is False
    assert manifest["overrides"]["allow_unverified_provenance"] is True


def test_publishable_build_reads_source_and_config_from_fixed_head(
    tmp_path: Path, fake_profile_bundle, build_python, monkeypatch
) -> None:
    packaging_dir = _attest_bundle(fake_profile_bundle)
    original_source = (fake_profile_bundle.source / "model_tools.py").read_bytes()
    original_profile = fake_profile_bundle.profile.read_bytes()
    original_materialize = build_release_module.materialize_release_inputs

    def materialize_during_transient_drift(**kwargs):
        source_path = fake_profile_bundle.source / "model_tools.py"
        source_path.write_text("transient source drift\n", encoding="utf-8")
        fake_profile_bundle.profile.write_text(
            "transient profile drift\n", encoding="utf-8"
        )
        try:
            return original_materialize(**kwargs)
        finally:
            source_path.write_bytes(original_source)
            fake_profile_bundle.profile.write_bytes(original_profile)

    monkeypatch.setattr(build_release_module, "PACKAGING_DIR", packaging_dir)
    monkeypatch.setattr(
        build_release_module,
        "materialize_release_inputs",
        materialize_during_transient_drift,
    )
    output = tmp_path / "publishable"
    result = _build(
        fake_profile_bundle,
        build_python,
        output,
        allow_dirty=False,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert result["publishable"] is True
    assert manifest["publishable"] is True
    assert (output / "config" / "runtime-profile.yaml").read_bytes() == original_profile
    with zipfile.ZipFile(result["wheel"]) as archive:
        assert b"transient source drift" not in archive.read("model_tools.py")

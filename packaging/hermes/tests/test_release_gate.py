from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from _common import load_upstream_manifest
from release_gate import (
    baseline_statement,
    evaluate_release_gate,
    materialize_release_inputs,
    replay_statement,
    statement_sha256,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)


def _attested_checkout(bundle) -> Path:
    packaging_dir = bundle.root / "release-packaging"
    patches = packaging_dir / "patches"
    patches.mkdir(parents=True)
    (patches / "series").write_text("# no downstream patches\n", encoding="utf-8")

    _git(bundle.root, "init")
    _git(bundle.root, "config", "user.email", "release-test@example.invalid")
    _git(bundle.root, "config", "user.name", "Release Test")
    _commit(bundle.root, "initial release inputs")

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
    _commit(bundle.root, "attest release replay")
    return packaging_dir


def _evaluate(bundle, packaging_dir: Path):
    return evaluate_release_gate(
        manifest=load_upstream_manifest(bundle.upstream),
        source=bundle.source,
        packaging_dir=packaging_dir,
        profile_path=bundle.profile,
        expected_dir=bundle.expected,
        upstream_path=bundle.upstream,
        inventory_path=bundle.inventory,
    )


def test_release_gate_accepts_only_bound_committed_inputs(fake_profile_bundle) -> None:
    packaging_dir = _attested_checkout(fake_profile_bundle)
    result = _evaluate(fake_profile_bundle, packaging_dir)

    assert result["ready"] is True
    assert result["attestation"]["baseline"]["valid"] is True
    assert result["attestation"]["replay"]["valid"] is True
    assert result["git"]["tree_matches"] is True
    assert result["git"]["dirty"] is False


def test_release_gate_covers_non_source_build_input_drift(fake_profile_bundle) -> None:
    packaging_dir = _attested_checkout(fake_profile_bundle)
    fake_profile_bundle.profile.write_text("changed\n", encoding="utf-8")

    result = _evaluate(fake_profile_bundle, packaging_dir)

    assert result["ready"] is False
    assert "release Git inputs are dirty or untracked" in result["errors"]


def test_release_gate_binds_patch_bytes(fake_profile_bundle) -> None:
    packaging_dir = _attested_checkout(fake_profile_bundle)
    patch = packaging_dir / "patches" / "change.patch"
    patch.write_text("not declared\n", encoding="utf-8")

    result = _evaluate(fake_profile_bundle, packaging_dir)

    assert result["ready"] is False
    assert any("undeclared patch files" in error for error in result["errors"])
    assert "release Git inputs are dirty or untracked" in result["errors"]


def test_release_gate_rejects_committed_tree_identity_mismatch(fake_profile_bundle) -> None:
    packaging_dir = _attested_checkout(fake_profile_bundle)
    data = yaml.safe_load(fake_profile_bundle.upstream.read_text(encoding="utf-8"))
    data["fully_patched_release_tree"] = "f" * 40
    data["replay_attestation"]["statement_sha256"] = statement_sha256(
        replay_statement(data, packaging_dir / "patches")
    )
    fake_profile_bundle.upstream.write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    _commit(fake_profile_bundle.root, "record wrong release tree")

    result = _evaluate(fake_profile_bundle, packaging_dir)

    assert result["attestation"]["ready"] is True
    assert result["git"]["dirty"] is False
    assert result["git"]["tree_matches"] is False
    assert "committed Hermes source tree does not match fully_patched_release_tree" in result[
        "errors"
    ]


def test_publishable_inputs_are_materialized_from_fixed_head_despite_worktree_race(
    tmp_path: Path, fake_profile_bundle
) -> None:
    packaging_dir = _attested_checkout(fake_profile_bundle)
    # A developer-level Git setting must not change the normalized modes in
    # the immutable release snapshot.
    _git(fake_profile_bundle.root, "config", "tar.umask", "0077")
    gate = _evaluate(fake_profile_bundle, packaging_dir)
    original_source = (fake_profile_bundle.source / "model_tools.py").read_bytes()
    original_profile = fake_profile_bundle.profile.read_bytes()

    (fake_profile_bundle.source / "model_tools.py").write_text(
        "transient source drift\n", encoding="utf-8"
    )
    fake_profile_bundle.profile.write_text("transient profile drift\n", encoding="utf-8")
    materialized = materialize_release_inputs(
        source=fake_profile_bundle.source,
        git_state=gate["git"],
        destination=tmp_path / "committed-inputs",
    )
    (fake_profile_bundle.source / "model_tools.py").write_bytes(original_source)
    fake_profile_bundle.profile.write_bytes(original_profile)

    assert (materialized["hermes_source"] / "model_tools.py").read_bytes() == original_source
    assert materialized["runtime_profile"].read_bytes() == original_profile
    assert (materialized["hermes_source"] / "skills").stat().st_mode & 0o777 == 0o775
    assert (
        materialized["hermes_source"] / "skills" / "fixture" / "SKILL.md"
    ).stat().st_mode & 0o777 == 0o664
    assert _evaluate(fake_profile_bundle, packaging_dir)["ready"] is True

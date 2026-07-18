from pathlib import Path

import pytest

from _common import PackagingError
from verify_profile import verify


def _verify(bundle, python: Path, *, require_publishable: bool = False):
    return verify(
        source=bundle.source,
        installed=False,
        wheel=None,
        python=python,
        profile_path=bundle.profile,
        expected_dir=bundle.expected,
        upstream_path=bundle.upstream,
        inventory_path=bundle.inventory,
        timeout=30,
        require_publishable=require_publishable,
    )


def test_verify_profile_requires_exact_27_tool_surface(fake_profile_bundle, build_python) -> None:
    result = _verify(fake_profile_bundle, build_python)
    assert result["expected_tool_count"] == 27
    assert set(result["logical_model_tools"]) == set(result["expected_tools"])
    assert result["runtime_profile_origin"] == "runtime_profile.py"
    assert result["gateway_entry_origin"] == "tui_gateway/entry.py"


def test_verify_profile_detects_skill_content_drift(fake_profile_bundle, build_python) -> None:
    skill = fake_profile_bundle.source / "skills" / "fixture" / "SKILL.md"
    skill.write_text("changed", encoding="utf-8")
    with pytest.raises(PackagingError, match="skill inventory mismatch"):
        _verify(fake_profile_bundle, build_python)


def test_verify_profile_rejects_unverified_publishable_gate(
    fake_profile_bundle, build_python
) -> None:
    text = fake_profile_bundle.upstream.read_text(encoding="utf-8")
    fake_profile_bundle.upstream.write_text(
        text.replace("provenance_status: verified", "provenance_status: unverified"),
        encoding="utf-8",
    )
    with pytest.raises(PackagingError, match="provenance is unverified"):
        _verify(fake_profile_bundle, build_python, require_publishable=True)


def test_verify_profile_rejects_status_only_publishable_claim(
    fake_profile_bundle, build_python
) -> None:
    with pytest.raises(PackagingError, match="baseline attestation is pending"):
        _verify(fake_profile_bundle, build_python, require_publishable=True)

from __future__ import annotations

from pathlib import Path
import hashlib
import json

import pytest

from _lite_common import LiteReleaseError, sha256_file, write_json_object
from build_lite_release import _guard_build_paths, _validate_browser_assets
from generate_hash_lock import generate


def test_build_paths_cannot_pollute_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(LiteReleaseError, match="must not be inside source tree"):
        _guard_build_paths(source, source / "dist" / "release", None)


def test_build_paths_reject_production_roots(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(LiteReleaseError, match="must not be under /opt"):
        _guard_build_paths(source, Path("/opt/potato-hermes-lite/release"), None)


def test_browser_asset_contract_validates_pinned_executables(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    agent = assets / "browser" / "bin" / "agent-browser"
    chrome = assets / "browser" / "chrome" / "chrome-linux64" / "chrome"
    agent.parent.mkdir(parents=True)
    chrome.parent.mkdir(parents=True)
    agent.write_text("#!/bin/sh\necho 'agent-browser 0.26.0'\n", encoding="utf-8")
    chrome.write_text(
        "#!/bin/sh\necho 'Google Chrome for Testing 151.0.7922.34'\n",
        encoding="utf-8",
    )
    agent.chmod(0o755)
    chrome.chmod(0o755)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    write_json_object(
        manifests / "browser-assets.json",
        {
            "schema_version": 1,
            "agent_browser": {
                "path": "browser/bin/agent-browser",
                "version": "0.26.0",
                "sha256": sha256_file(agent),
            },
            "chrome_for_testing": {
                "path": "browser/chrome/chrome-linux64/chrome",
                "version": "151.0.7922.34",
                "archive": {
                    "url": (
                        "https://storage.googleapis.com/"
                        "chrome-for-testing-public/151.0.7922.34/linux64/"
                        "chrome-linux64.zip"
                    ),
                    "size": 1,
                    "sha256": "0" * 64,
                },
            },
        },
    )

    browser_root, contract = _validate_browser_assets(assets, manifests)

    assert browser_root == assets / "browser"
    assert contract["agent_browser"]["version_output"] == "agent-browser 0.26.0"
    assert "Chrome for Testing 151.0.7922.34" in contract["chrome_for_testing"][
        "version_output"
    ]


def test_browser_asset_contract_rejects_non_executable(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    agent = assets / "browser" / "bin" / "agent-browser"
    chrome = assets / "browser" / "chrome" / "chrome-linux64" / "chrome"
    agent.parent.mkdir(parents=True)
    chrome.parent.mkdir(parents=True)
    agent.write_text("agent-browser 0.26.0\n", encoding="utf-8")
    chrome.write_text("Chrome for Testing 151.0.7922.34\n", encoding="utf-8")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    write_json_object(
        manifests / "browser-assets.json",
        {
            "schema_version": 1,
            "agent_browser": {
                "path": "browser/bin/agent-browser",
                "version": "0.26.0",
                "sha256": sha256_file(agent),
            },
            "chrome_for_testing": {
                "path": "browser/chrome/chrome-linux64/chrome",
                "version": "151.0.7922.34",
                "archive": {
                    "url": (
                        "https://storage.googleapis.com/"
                        "chrome-for-testing-public/151.0.7922.34/linux64/"
                        "chrome-linux64.zip"
                    ),
                    "size": 1,
                    "sha256": "0" * 64,
                },
            },
        },
    )

    with pytest.raises(LiteReleaseError, match="regular executable"):
        _validate_browser_assets(assets, manifests)


def test_hash_lock_generation_binds_report_wheel_and_fingerprint(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "example_pkg-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"wheel bytes")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "install": [
                    {
                        "metadata": {"name": "example-pkg", "version": "1.2.3"},
                        "download_info": {"archive_info": {"hashes": {"sha256": digest}}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    lock = tmp_path / "requirements.lock"
    wheel_manifest = tmp_path / "wheelhouse.json"
    fingerprint = tmp_path / "distributions.json"

    generate(
        report_path=report,
        wheelhouse=wheelhouse,
        lock_path=lock,
        wheelhouse_manifest_path=wheel_manifest,
        distribution_fingerprint_path=fingerprint,
    )

    assert f"example-pkg==1.2.3 --hash=sha256:{digest}" in lock.read_text()
    assert json.loads(wheel_manifest.read_text())["lock"]["sha256"] == hashlib.sha256(
        lock.read_bytes()
    ).hexdigest()
    assert json.loads(fingerprint.read_text())["distributions"] == {
        "example-pkg": "1.2.3"
    }


def test_installer_requires_hashes_and_installs_project_without_deps() -> None:
    installer = (
        Path(__file__).resolve().parents[1] / "scripts" / "install_lite_release.sh"
    ).read_text(encoding="utf-8")

    assert "--require-hashes" in installer
    assert "--no-index" in installer
    assert "--no-deps" in installer
    assert "distribution_file_fingerprints" in installer
    assert 'chmod -R a+rX "${final}/venv"' in installer

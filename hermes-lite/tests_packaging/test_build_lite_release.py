from __future__ import annotations

from pathlib import Path

import pytest

from _lite_common import LiteReleaseError, sha256_file, write_json_object
from build_lite_release import _guard_build_paths, _validate_browser_assets


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

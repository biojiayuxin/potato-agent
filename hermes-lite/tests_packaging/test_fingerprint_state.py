from __future__ import annotations

import json
import stat
from pathlib import Path

from fingerprint_state import (
    capture_state,
    compare_manifests,
    main,
    write_manifest_atomic,
)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    data = tmp_path / "interface-data"
    hermes_home = tmp_path / "alice" / ".hermes"
    workdir = tmp_path / "alice" / "work"
    data.mkdir()
    hermes_home.mkdir(parents=True)
    workdir.mkdir()
    (data / "interface.db").write_bytes(b"database-v1")
    (hermes_home / "state.db").write_bytes(b"state-v1")
    (hermes_home / "config.yaml").write_text("model: fixture\n", encoding="utf-8")
    (workdir / "must-not-be-read.txt").write_text("private work data\n", encoding="utf-8")
    (hermes_home / "work-link").symlink_to(workdir, target_is_directory=True)
    mapping = tmp_path / "users_mapping.yaml"
    mapping.write_text(
        "users:\n"
        "  - username: alice\n"
        "    linux_user: hmx_alice\n"
        f"    hermes_home: {hermes_home}\n"
        f"    workdir: {workdir}\n",
        encoding="utf-8",
    )
    return mapping, data, hermes_home, workdir


def test_capture_hashes_only_declared_state_and_does_not_follow_links(
    tmp_path: Path,
) -> None:
    mapping, data, hermes_home, workdir = _fixture(tmp_path)

    manifest = capture_state(mapping_path=mapping, data_dir=data)
    entries = {item["path"]: item for item in manifest["entries"]}

    assert str(mapping) in entries
    assert str(data / "interface.db") in entries
    assert str(hermes_home / "state.db") in entries
    assert entries[str(hermes_home / "work-link")]["type"] == "symlink"
    assert str(workdir / "must-not-be-read.txt") not in entries


def test_capture_uses_metadata_only_for_user_home_subtree(tmp_path: Path) -> None:
    mapping, data, hermes_home, _workdir = _fixture(tmp_path)
    user_home = hermes_home / "home"
    user_home.mkdir()
    dependency = user_home / "large-package.tar"
    dependency.write_bytes(b"package-cache")

    manifest = capture_state(mapping_path=mapping, data_dir=data)
    entries = {item["path"]: item for item in manifest["entries"]}

    assert str(dependency) not in entries
    assert entries[str(user_home)]["verification"] == "metadata_only"
    assert entries[str(user_home)]["type"] == "metadata_tree"
    assert entries[str(user_home)]["regular_files"] == 1
    assert entries[str(user_home)]["tree_sha256"]
    assert entries[str(hermes_home / "state.db")]["sha256"]
    assert str(user_home) in manifest["roots"]["metadata_only"]


def test_compare_reports_only_added_removed_and_changed_paths(tmp_path: Path) -> None:
    mapping, data, hermes_home, _workdir = _fixture(tmp_path)
    before = capture_state(mapping_path=mapping, data_dir=data)
    (data / "interface.db").write_bytes(b"database-v2")
    (hermes_home / "state.db").unlink()
    added = hermes_home / "new-state.json"
    added.write_text("{}\n", encoding="utf-8")
    after = capture_state(mapping_path=mapping, data_dir=data)

    assert compare_manifests(before, after) == {
        "added": [str(added)],
        "removed": [str(hermes_home / "state.db")],
        "changed": [str(hermes_home), str(data / "interface.db")],
    }


def test_atomic_manifest_is_private_and_compare_cli_emits_path_lists(
    tmp_path: Path, capsys
) -> None:
    mapping, data, _hermes_home, _workdir = _fixture(tmp_path)
    manifest = capture_state(mapping_path=mapping, data_dir=data)
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    write_manifest_atomic(before, manifest)
    write_manifest_atomic(after, manifest)

    assert stat.S_IMODE(before.stat().st_mode) == 0o600
    assert main(["compare", "--before", str(before), "--after", str(after)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "added": [],
        "changed": [],
        "removed": [],
    }

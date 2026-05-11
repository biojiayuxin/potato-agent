from __future__ import annotations

import importlib
import os
import stat

import yaml


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_auth_and_archive_db_are_private(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "state" / "interface.db"
    archive_path = tmp_path / "state" / "archive.db"
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(auth_path))
    monkeypatch.setenv("INTERFACE_ARCHIVE_DB", str(archive_path))

    import interface.auth_db as auth_db
    import interface.archive_store as archive_store

    importlib.reload(auth_db)
    importlib.reload(archive_store)

    auth_db.ensure_auth_db()
    archive_store.ensure_archive_db()

    assert _mode(auth_path.parent) == 0o700
    assert _mode(auth_path) == 0o600
    assert _mode(archive_path) == 0o600


def test_mapping_default_path_and_write_mode(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.delenv("POTATO_AGENT_MAPPING_PATH", raising=False)
    monkeypatch.setenv("POTATO_AGENT_STATE_DIR", str(state_dir))

    import interface.secure_paths as secure_paths
    import interface.mapping as mapping

    importlib.reload(secure_paths)
    importlib.reload(mapping)

    assert mapping.DEFAULT_MAPPING_PATH == state_dir / "config" / "users_mapping.yaml"

    mapping.write_mapping(mapping.DEFAULT_MAPPING_PATH, {"users": []})

    assert _mode(mapping.DEFAULT_MAPPING_PATH.parent) == 0o750
    assert _mode(mapping.DEFAULT_MAPPING_PATH) == 0o640
    assert yaml.safe_load(mapping.DEFAULT_MAPPING_PATH.read_text(encoding="utf-8")) == {
        "users": []
    }

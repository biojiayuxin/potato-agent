from __future__ import annotations

from pathlib import Path
import stat
import subprocess
import sys
import textwrap

import yaml


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _run_isolated_python(source: str) -> None:
    # Default paths are resolved at import time. A separate interpreter avoids
    # stale module references and leaking environment-specific paths to tests.
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_auth_and_archive_db_are_private(tmp_path, monkeypatch) -> None:
    auth_path = tmp_path / "state" / "interface.db"
    archive_path = tmp_path / "state" / "archive.db"
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(auth_path))
    monkeypatch.setenv("INTERFACE_ARCHIVE_DB", str(archive_path))

    _run_isolated_python("""
        from interface import auth_db, archive_store
        auth_db.ensure_auth_db()
        archive_store.ensure_archive_db()
    """)

    assert _mode(auth_path.parent) == 0o700
    assert _mode(auth_path) == 0o600
    assert _mode(archive_path) == 0o600


def test_mapping_default_path_and_write_mode(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.delenv("POTATO_AGENT_MAPPING_PATH", raising=False)
    monkeypatch.setenv("POTATO_AGENT_STATE_DIR", str(state_dir))

    _run_isolated_python("""
        import os
        from pathlib import Path
        from interface import mapping
        expected = Path(os.environ["POTATO_AGENT_STATE_DIR"]) / "config" / "users_mapping.yaml"
        assert mapping.DEFAULT_MAPPING_PATH == expected
        mapping.write_mapping(mapping.DEFAULT_MAPPING_PATH, {"users": []})
    """)

    mapping_path = state_dir / "config" / "users_mapping.yaml"
    assert _mode(mapping_path.parent) == 0o750
    assert _mode(mapping_path) == 0o640
    assert yaml.safe_load(mapping_path.read_text(encoding="utf-8")) == {
        "users": []
    }

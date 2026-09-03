from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import backup_cutover_state as backup


def _create_database(path: Path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("create table records (value text not null)")
        connection.execute("insert into records values (?)", (value,))


def _fixture(tmp_path: Path, *, users: int = 2) -> dict[str, object]:
    data_dir = tmp_path / "data"
    proxy_dir = tmp_path / "proxy"
    data_dir.mkdir(parents=True)
    proxy_dir.mkdir()
    databases = {
        "interface_db": data_dir / "interface.db",
        "archive_db": data_dir / "archive.db",
        "chat_shares_db": data_dir / "chat_shares.db",
        "usage_db": proxy_dir / "usage.db",
    }
    for role, path in databases.items():
        _create_database(path, role)
        path.chmod(0o640)

    mapping_lines = ["users:"]
    state_databases: list[Path] = []
    for index in range(users):
        hermes_home = tmp_path / f"private-user-{index}" / ".hermes"
        hermes_home.mkdir(parents=True)
        state_db = hermes_home / "state.db"
        _create_database(state_db, f"state-{index}")
        state_db.chmod(0o600)
        state_databases.append(state_db)
        mapping_lines.extend(
            (
                f"  - username: secret-user-{index}",
                f"    linux_user: hmx_secret_{index}",
                f"    hermes_home: {hermes_home}",
            )
        )
    mapping_path = tmp_path / "users_mapping.yaml"
    mapping_path.write_text("\n".join(mapping_lines) + "\n", encoding="utf-8")
    mapping_path.chmod(0o640)
    config_path = proxy_dir / "model_proxy.yaml"
    config_path.write_text("api_key: top-secret-value\n", encoding="utf-8")
    config_path.chmod(0o640)
    return {
        **databases,
        "mapping_path": mapping_path,
        "model_proxy_config": config_path,
        "state_databases": state_databases,
        "destination": tmp_path / "cutover" / "sensitive-state",
    }


def _run(paths: dict[str, object]) -> dict[str, object]:
    destination = paths["destination"]
    assert isinstance(destination, Path)
    destination.parent.mkdir(exist_ok=True)
    return backup.create_backup(
        mapping_path=paths["mapping_path"],
        destination=destination,
        interface_db=paths["interface_db"],
        archive_db=paths["archive_db"],
        chat_shares_db=paths["chat_shares_db"],
        model_proxy_config=paths["model_proxy_config"],
        usage_db=paths["usage_db"],
    )


def _values(path: Path) -> list[str]:
    with sqlite3.connect(path) as connection:
        assert connection.execute("pragma integrity_check").fetchall() == [("ok",)]
        return [row[0] for row in connection.execute("select value from records")]


def test_backup_copies_all_requested_state_with_private_manifest(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    destination = paths["destination"]
    assert isinstance(destination, Path)

    manifest = _run(paths)

    expected_values = {
        destination / "interface/interface.db": ["interface_db"],
        destination / "interface/archive.db": ["archive_db"],
        destination / "interface/chat_shares.db": ["chat_shares_db"],
        destination / "model-proxy/usage.db": ["usage_db"],
        destination / "users/0000/state.db": ["state-0"],
        destination / "users/0001/state.db": ["state-1"],
    }
    for path, values in expected_values.items():
        assert _values(path) == values
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    config_backup = destination / "model-proxy/model_proxy.yaml"
    assert config_backup.read_bytes() == paths["model_proxy_config"].read_bytes()
    assert stat.S_IMODE(config_backup.stat().st_mode) == 0o600
    manifest_path = destination / "manifest.json"
    on_disk_manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    assert on_disk_manifest == manifest
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert on_disk_manifest["totals"] == {
        "sqlite_databases": 6,
        "mapped_user_databases": 2,
        "configuration_files": 1,
    }
    assert [item.get("mapping_index") for item in manifest["databases"][-2:]] == [
        0,
        1,
    ]
    assert manifest["databases"][-1]["source"]["mode"] == "0600"
    assert manifest["files"][0]["source"]["mode"] == "0640"
    assert manifest["files"][0]["backup"]["sha256"] == hashlib.sha256(
        paths["model_proxy_config"].read_bytes()
    ).hexdigest()
    assert (
        destination.parent / "sensitive-state.complete"
    ).read_text() == "complete\n"
    assert stat.S_IMODE(
        (destination.parent / "sensitive-state.complete").stat().st_mode
    ) == 0o600
    for directory in (
        destination,
        destination / "interface",
        destination / "model-proxy",
        destination / "users",
        destination / "users/0000",
        destination / "users/0001",
    ):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_backup_includes_committed_wal_data(tmp_path: Path) -> None:
    paths = _fixture(tmp_path, users=1)
    interface_db = paths["interface_db"]
    assert isinstance(interface_db, Path)
    writer = sqlite3.connect(interface_db)
    try:
        assert writer.execute("pragma journal_mode=wal").fetchone() == ("wal",)
        writer.execute("pragma wal_autocheckpoint=0")
        writer.execute("pragma wal_checkpoint(truncate)")
        writer.execute("insert into records values ('committed-in-wal')")
        writer.commit()
        assert Path(f"{interface_db}-wal").stat().st_size > 0

        _run(paths)
    finally:
        writer.close()

    destination = paths["destination"]
    assert isinstance(destination, Path)
    assert _values(destination / "interface/interface.db") == [
        "interface_db",
        "committed-in-wal",
    ]


def test_missing_required_database_fails_before_creating_backup(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    archive_db = paths["archive_db"]
    destination = paths["destination"]
    assert isinstance(archive_db, Path)
    assert isinstance(destination, Path)
    archive_db.unlink()

    with pytest.raises(backup.BackupError, match="archive database is unavailable"):
        _run(paths)

    assert not destination.exists()
    assert not (destination.parent / "sensitive-state.complete").exists()


def test_corrupt_database_leaves_backup_incomplete(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    interface_db = paths["interface_db"]
    destination = paths["destination"]
    assert isinstance(interface_db, Path)
    assert isinstance(destination, Path)
    interface_db.write_bytes(b"not a sqlite database")

    with pytest.raises(backup.BackupError, match="SQLite backup or validation failed"):
        _run(paths)

    assert destination.is_dir()
    assert not (destination / "manifest.json").exists()
    assert not (destination.parent / "sensitive-state.complete").exists()


def test_symlink_source_and_destination_are_rejected(tmp_path: Path) -> None:
    source_paths = _fixture(tmp_path / "source-case")
    config = source_paths["model_proxy_config"]
    assert isinstance(config, Path)
    real_config = config.with_name("real-model-proxy.yaml")
    config.rename(real_config)
    config.symlink_to(real_config)

    with pytest.raises(backup.BackupError, match="contains a symbolic link"):
        _run(source_paths)

    destination_paths = _fixture(tmp_path / "destination-case")
    destination = destination_paths["destination"]
    assert isinstance(destination, Path)
    destination.parent.mkdir(exist_ok=True)
    destination.symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(backup.BackupError, match="destination is a symbolic link"):
        backup.create_backup(
            mapping_path=destination_paths["mapping_path"],
            destination=destination,
            interface_db=destination_paths["interface_db"],
            archive_db=destination_paths["archive_db"],
            chat_shares_db=destination_paths["chat_shares_db"],
            model_proxy_config=destination_paths["model_proxy_config"],
            usage_db=destination_paths["usage_db"],
        )


def test_shared_user_state_database_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    state_databases = paths["state_databases"]
    destination = paths["destination"]
    assert isinstance(state_databases, list)
    assert isinstance(destination, Path)
    state_databases[1].unlink()
    os.link(state_databases[0], state_databases[1])

    with pytest.raises(backup.BackupError, match="share a state database"):
        _run(paths)

    assert not destination.exists()


def test_insufficient_space_fails_before_creating_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture(tmp_path)
    destination = paths["destination"]
    assert isinstance(destination, Path)
    monkeypatch.setattr(
        backup.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0)
    )

    with pytest.raises(backup.BackupError, match="insufficient free space"):
        _run(paths)

    assert not destination.exists()
    assert not (destination.parent / "sensitive-state.complete").exists()


def test_cli_output_does_not_disclose_usernames_paths_or_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _fixture(tmp_path, users=1)
    destination = paths["destination"]
    assert isinstance(destination, Path)
    destination.parent.mkdir(exist_ok=True)
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0)

    assert backup.main(
        [
            "--mapping",
            str(paths["mapping_path"]),
            "--destination",
            str(destination),
            "--interface-db",
            str(paths["interface_db"]),
            "--archive-db",
            str(paths["archive_db"]),
            "--chat-shares-db",
            str(paths["chat_shares_db"]),
            "--model-proxy-config",
            str(paths["model_proxy_config"]),
            "--usage-db",
            str(paths["usage_db"]),
        ]
    ) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "7 databases (1 mapped user databases)" not in output.out
    assert "5 databases (1 mapped user databases)" in output.out
    assert "secret-user" not in output.out
    assert "top-secret-value" not in output.out
    assert str(tmp_path) not in output.out

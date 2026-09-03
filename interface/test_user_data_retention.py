from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from interface.mapping import HermesTarget
from interface import user_data_retention as retention
from interface import user_lifecycle_lock as lifecycle


def _identity(tmp_path: Path) -> tuple[retention.UserIdentity, retention.Journal]:
    home = tmp_path / "home"
    history = tmp_path / "history"
    (home / ".hermes").mkdir(parents=True)
    history.mkdir(mode=0o700)
    mount_id, major_minor, mountpoint, options = retention._mountinfo_for(home)
    fs = retention.FilesystemConfig(
        uuid="test-uuid",
        mountpoint=mountpoint,
        home_bases=(tmp_path,),
        history_root=history,
    )
    target = HermesTarget(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
        linux_user="alice",
        home_dir=home,
        hermes_home=home / ".hermes",
        workdir=home,
        api_server_host="127.0.0.1",
        api_port=8643,
        api_key="test-key",
        api_server_model_name="test-model",
        systemd_service="hermes-alice.service",
        extra_env={},
        config_overrides={},
    )
    identity = retention.UserIdentity(
        target=target,
        uid=os.getuid(),
        gid=os.getgid(),
        home_dev=home.stat().st_dev,
        home_ino=home.stat().st_ino,
        filesystem=fs,
        mount=retention.MountIdentity(
            mount_id=mount_id,
            major_minor=major_minor,
            mountpoint=mountpoint,
            uuid="test-uuid",
            options=options,
            st_dev=home.stat().st_dev,
        ),
        hermes_relative=b".hermes",
    )
    return identity, retention.Journal(history)


@pytest.mark.parametrize(
    "path",
    [
        b".hermes/state.db",
        b"public_data",
        b"public_data/result.bin",
        b".ssh/id_ed25519",
        b"project/.git/index",
        b"src/result.bin",
        b"tools/archive.dat",
        b"id_backup",
        b"certificate.pem",
        b"analysis.py",
        b"notebook.ipynb",
        b"pyproject.toml",
        b"package-lock.json",
    ],
)
def test_protected_paths_cover_runtime_secrets_source_and_manifests(
    path: bytes,
) -> None:
    assert retention.path_is_protected(path, hermes_relative=b".hermes")


def test_upload_tree_is_the_only_hidden_tree_scanned() -> None:
    assert not retention.path_is_protected(
        b".potato-interface-uploads/alice/result.fastq.gz",
        hermes_relative=b".hermes",
    )
    assert not retention.directory_should_prune(
        b".potato-interface-uploads/alice",
        hermes_relative=b".hermes",
    )
    assert retention.directory_should_prune(b".cache/data", hermes_relative=b".hermes")


def test_raw_mapping_home_symlink_is_rejected(tmp_path: Path) -> None:
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(real_home, target_is_directory=True)

    assert retention._raw_mapping_home_is_symlink(linked_home)
    assert not retention._raw_mapping_home_is_symlink(real_home)


def test_flat_home_base_allows_protected_sibling_history_root() -> None:
    filesystem = retention.FilesystemConfig(
        uuid="test-uuid",
        mountpoint=Path("/mnt/data"),
        home_bases=(Path("/mnt/data"),),
        history_root=Path("/mnt/data/.potato-user-history"),
    )

    assert (
        retention._configured_filesystem_for_home(
            Path("/mnt/data/alice"), (filesystem,)
        )
        is filesystem
    )


@pytest.mark.parametrize(
    ("home", "history_root"),
    [
        (
            Path("/mnt/data/.potato-user-history"),
            Path("/mnt/data/.potato-user-history"),
        ),
        (
            Path("/mnt/data/.potato-user-history/origins"),
            Path("/mnt/data/.potato-user-history"),
        ),
        (Path("/mnt/data/alice"), Path("/mnt/data/alice/.retention-history")),
    ],
)
def test_mapped_home_cannot_overlap_history_root(
    home: Path, history_root: Path
) -> None:
    filesystem = retention.FilesystemConfig(
        uuid="test-uuid",
        mountpoint=Path("/mnt/data"),
        home_bases=(Path("/mnt/data"),),
        history_root=history_root,
    )

    with pytest.raises(retention.UnsafeFilesystemError):
        retention._configured_filesystem_for_home(home, (filesystem,))


def test_streaming_scan_filters_links_executables_and_time_boundary(
    tmp_path: Path,
) -> None:
    identity, journal = _identity(tmp_path)
    home = identity.target.home_dir
    (home / ".potato-interface-uploads").mkdir()
    (home / "src").mkdir()
    included = home / "result.dat"
    boundary = home / "boundary.dat"
    upload = home / ".potato-interface-uploads" / "upload.dat"
    executable = home / "run.bin"
    source = home / "analysis.py"
    hidden = home / ".cache"
    hidden.mkdir()
    for path in (
        included,
        boundary,
        upload,
        executable,
        source,
        hidden / "cache.dat",
        home / "src" / "output.dat",
    ):
        path.write_bytes(b"payload")
    executable.chmod(0o755)
    os.symlink(included, home / "result-link")
    os.link(included, home / "result-hardlink")
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    cutoff_ns = max(
        boundary.stat().st_atime_ns,
        boundary.stat().st_mtime_ns,
        boundary.stat().st_ctime_ns,
    )

    exact = list(
        retention.scan_candidates(
            identity,
            cutoff_ns=cutoff_ns,
            origin_id=origin_id,
            journal=journal,
            now=time.time(),
            max_files=100,
            deadline=time.monotonic() + 10,
        )
    )
    assert b"boundary.dat" not in {item.relpath for item in exact}

    candidates = list(
        retention.scan_candidates(
            identity,
            cutoff_ns=time.time_ns() + 1_000_000_000,
            origin_id=origin_id,
            journal=journal,
            now=time.time(),
            max_files=100,
            deadline=time.monotonic() + 10,
        )
    )
    assert {item.relpath for item in candidates} == {
        b".potato-interface-uploads/upload.dat",
        b"boundary.dat",
    }


def test_scan_preserves_invalid_utf8_names(tmp_path: Path) -> None:
    identity, journal = _identity(tmp_path)
    home_bytes = os.fsencode(identity.target.home_dir)
    raw_name = b"result-\xff.dat"
    fd = os.open(
        home_bytes + b"/" + raw_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    os.close(fd)
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    candidates = list(
        retention.scan_candidates(
            identity,
            cutoff_ns=time.time_ns() + 1_000_000_000,
            origin_id=origin_id,
            journal=journal,
            now=time.time(),
            max_files=10,
            deadline=time.monotonic() + 10,
        )
    )
    assert [item.relpath for item in candidates] == [raw_name]


def test_activity_is_rechecked_after_scanned_non_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    identity, journal = _identity(tmp_path)
    for name in ("recent-a.dat", "recent-b.dat"):
        (identity.target.home_dir / name).write_bytes(b"recent")
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    checks = 0

    def activity_check() -> str:
        nonlocal checks
        checks += 1
        return "uid_processes"

    monkeypatch.setattr(retention, "BATCH_SIZE", 2)
    with pytest.raises(retention.UserBecameActive, match="uid_processes"):
        list(
            retention.scan_candidates(
                identity,
                cutoff_ns=0,
                origin_id=origin_id,
                journal=journal,
                now=time.time(),
                max_files=10,
                deadline=time.monotonic() + 10,
                activity_check=activity_check,
            )
        )
    assert checks == 1


def _candidate(path: Path, relpath: bytes) -> retention.FileCandidate:
    value = path.stat()
    return retention.FileCandidate(
        relpath=relpath,
        dev=value.st_dev,
        ino=value.st_ino,
        mode=value.st_mode,
        uid=value.st_uid,
        gid=value.st_gid,
        size=value.st_size,
        blocks=value.st_blocks,
        atime_ns=value.st_atime_ns,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
    )


def test_stage_is_noreplace_and_crash_reconciliation_commits_rename(
    tmp_path: Path,
) -> None:
    identity, journal = _identity(tmp_path)
    source = identity.target.home_dir / "result.dat"
    source.write_bytes(b"payload")
    candidate = _candidate(source, b"result.dat")
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    run_id = str(uuid.uuid4())
    journal.create_run(
        run_id=run_id,
        origin_id=origin_id,
        mode="enforce",
        fingerprint="fingerprint",
        started_at=time.time(),
    )
    [(entry_id, _candidate_value, archive_relpath)] = journal.prepare_entries(
        origin_id=origin_id,
        run_id=run_id,
        candidates=[candidate],
        prepared_at=time.time(),
    )
    home_fd = retention._open_root_fd(identity.target.home_dir)
    history_fd = retention._open_root_fd(identity.filesystem.history_root)
    try:
        retention._ensure_archive_parents(history_fd, archive_relpath)
        retention.rename_noreplace(
            home_fd, candidate.relpath, history_fd, archive_relpath
        )
    finally:
        os.close(history_fd)
        os.close(home_fd)

    journal.reconcile()
    with journal.connect() as connection:
        row = connection.execute(
            "select state from entries where entry_id = ?", (entry_id,)
        ).fetchone()
    assert row["state"] == "STAGED"
    assert not source.exists()


def test_stage_collision_never_overwrites_existing_archive(tmp_path: Path) -> None:
    identity, journal = _identity(tmp_path)
    source = identity.target.home_dir / "result.dat"
    source.write_bytes(b"source")
    candidate = _candidate(source, b"result.dat")
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    run_id = str(uuid.uuid4())
    journal.create_run(
        run_id=run_id,
        origin_id=origin_id,
        mode="enforce",
        fingerprint="fingerprint",
        started_at=time.time(),
    )
    archive = (
        identity.filesystem.history_root
        / "origins"
        / origin_id
        / "runs"
        / run_id
        / "payload"
        / "result.dat"
    )
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"existing")
    staged_files, _bytes, _dirs = retention.stage_batch(
        identity,
        origin_id=origin_id,
        run_id=run_id,
        candidates=[candidate],
        journal=journal,
        now=time.time(),
    )
    assert staged_files == 0
    assert source.read_bytes() == b"source"
    assert archive.read_bytes() == b"existing"


def test_directory_metadata_failure_disables_empty_directory_removal(
    tmp_path: Path, monkeypatch
) -> None:
    identity, journal = _identity(tmp_path)
    directory = identity.target.home_dir / "results"
    directory.mkdir()
    source = directory / "result.dat"
    source.write_bytes(b"payload")
    candidate = _candidate(source, b"results/result.dat")
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    run_id = str(uuid.uuid4())
    journal.create_run(
        run_id=run_id,
        origin_id=origin_id,
        mode="enforce",
        fingerprint="fingerprint",
        started_at=time.time(),
    )
    monkeypatch.setattr(
        retention,
        "_capture_directory_metadata",
        lambda _fd: (_ for _ in ()).throw(OSError("xattrs unavailable")),
    )

    staged_files, _bytes, empty_dirs = retention.stage_batch(
        identity,
        origin_id=origin_id,
        run_id=run_id,
        candidates=[candidate],
        journal=journal,
        now=time.time(),
    )
    retention.remove_batch_empty_directories(
        identity,
        origin_id=origin_id,
        journal=journal,
        relative_dirs=empty_dirs,
    )

    assert staged_files == 1
    assert empty_dirs == set()
    assert directory.is_dir()


def test_prepared_directory_removal_is_reconciled(tmp_path: Path) -> None:
    identity, journal = _identity(tmp_path)
    directory = identity.target.home_dir / "results"
    directory.mkdir()
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        metadata = retention._capture_directory_metadata(directory_fd)
    finally:
        os.close(directory_fd)
    journal.record_directory(origin_id, b"results", metadata)

    assert journal.prepare_directory_removal(origin_id, b"results")
    journal.reconcile()
    assert journal.directory_record(origin_id, b"results")["removed"] == 0

    assert journal.prepare_directory_removal(origin_id, b"results")
    directory.rmdir()
    journal.reconcile()
    assert journal.directory_record(origin_id, b"results")["removed"] == 1


def test_purge_prepared_reconciliation_retries_without_deleting(tmp_path: Path) -> None:
    identity, journal = _identity(tmp_path)
    source = identity.target.home_dir / "result.dat"
    source.write_bytes(b"payload")
    candidate = _candidate(source, b"result.dat")
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    run_id = str(uuid.uuid4())
    journal.create_run(
        run_id=run_id,
        origin_id=origin_id,
        mode="enforce",
        fingerprint="fingerprint",
        started_at=time.time(),
    )
    assert (
        retention.stage_batch(
            identity,
            origin_id=origin_id,
            run_id=run_id,
            candidates=[candidate],
            journal=journal,
            now=time.time(),
        )[0]
        == 1
    )
    [row] = journal.staged_due(time.time() + 1)
    assert journal.prepare_purge(str(row["entry_id"]), time.time())
    journal.reconcile()
    with journal.connect() as connection:
        state = connection.execute(
            "select state from entries where entry_id = ?", (row["entry_id"],)
        ).fetchone()["state"]
    assert state == "STAGED"


def _stage_one(
    identity: retention.UserIdentity,
    journal: retention.Journal,
    *,
    name: str = "result.dat",
) -> tuple[str, str, Path]:
    source = identity.target.home_dir / name
    source.write_bytes(b"payload")
    candidate = _candidate(source, os.fsencode(name))
    origin_id, _new, _eligible = journal.origin_for(identity, time.time())
    run_id = str(uuid.uuid4())
    journal.create_run(
        run_id=run_id,
        origin_id=origin_id,
        mode="enforce",
        fingerprint="fingerprint",
        started_at=time.time(),
    )
    assert (
        retention.stage_batch(
            identity,
            origin_id=origin_id,
            run_id=run_id,
            candidates=[candidate],
            journal=journal,
            now=time.time(),
        )[0]
        == 1
    )
    [row] = journal.staged_due(time.time() + 1)
    archive = identity.filesystem.history_root / os.fsdecode(
        bytes(row["archive_relpath"])
    )
    return origin_id, run_id, archive


def test_purge_deletes_only_matching_staged_archive(
    tmp_path: Path, monkeypatch
) -> None:
    identity, journal = _identity(tmp_path)
    _origin_id, _run_id, archive = _stage_one(identity, journal)
    with journal.connect() as connection:
        connection.execute("update entries set staged_at = 1 where state = 'STAGED'")
        connection.commit()
    result = retention.UserResult(mapping_username="alice")
    monkeypatch.setattr(retention, "user_activity_reason", lambda _identity: None)
    retention.purge_due_entries(
        journal,
        cutoff=2,
        identities={"alice": identity},
        mode="enforce",
        now=3,
        result_by_username={"alice": result},
        lock_dir=tmp_path / "locks",
    )
    assert not archive.exists()
    assert result.purged_files == 1
    with journal.connect() as connection:
        assert (
            connection.execute("select state from entries").fetchone()["state"]
            == "PURGED"
        )


def test_purge_holds_archive_whose_metadata_changed(
    tmp_path: Path, monkeypatch
) -> None:
    identity, journal = _identity(tmp_path)
    _origin_id, _run_id, archive = _stage_one(identity, journal)
    archive.chmod(0o640)
    with journal.connect() as connection:
        connection.execute("update entries set staged_at = 1 where state = 'STAGED'")
        connection.commit()
    monkeypatch.setattr(retention, "user_activity_reason", lambda _identity: None)
    retention.purge_due_entries(
        journal,
        cutoff=2,
        identities={"alice": identity},
        mode="enforce",
        now=3,
        result_by_username={"alice": retention.UserResult(mapping_username="alice")},
        lock_dir=tmp_path / "locks",
    )
    assert archive.exists()
    with journal.connect() as connection:
        row = connection.execute("select state, hold_reason from entries").fetchone()
    assert (row["state"], row["hold_reason"]) == (
        "HOLD",
        "archive_metadata_changed",
    )


def test_restore_is_noreplace_and_adds_cooldown(tmp_path: Path, monkeypatch) -> None:
    identity, journal = _identity(tmp_path)
    origin_id, run_id, archive = _stage_one(identity, journal)
    config = retention.RetentionConfig(
        mode="preview",
        inactive_days=30,
        history_days=30,
        filesystems=(identity.filesystem,),
    )
    monkeypatch.setattr(retention, "require_root", lambda: None)
    monkeypatch.setattr(
        retention, "validate_filesystem", lambda _filesystem: identity.mount
    )
    monkeypatch.setattr(
        retention,
        "_prepare_identities",
        lambda **_kwargs: ([identity.target], {"alice": identity}, {}),
    )
    monkeypatch.setattr(retention, "user_activity_reason", lambda _identity: None)
    restored = retention.restore_entries(
        config=config,
        origin_id=origin_id,
        run_id=run_id,
        relpath=None,
        mapping_path=tmp_path / "mapping.yaml",
        lock_dir=tmp_path / "locks",
        now=100,
    )
    source = identity.target.home_dir / "result.dat"
    assert restored == {"restored_files": 1, "held_files": 0}
    assert source.read_bytes() == b"payload"
    assert not archive.exists()
    assert journal.cooldown_active(
        origin_id, b"result.dat", 100 + 29 * retention.DAY_SECONDS
    )

    source.unlink()
    _origin_id, collision_run_id, collision_archive = _stage_one(identity, journal)
    source.write_bytes(b"new data")
    collision = retention.restore_entries(
        config=config,
        origin_id=origin_id,
        run_id=collision_run_id,
        relpath=b"result.dat",
        mapping_path=tmp_path / "mapping.yaml",
        lock_dir=tmp_path / "locks",
        now=200,
    )
    assert collision == {"restored_files": 0, "held_files": 1}
    assert source.read_bytes() == b"new data"
    assert collision_archive.read_bytes() == b"payload"


def test_user_lifecycle_lock_blocks_controlled_entry(tmp_path: Path) -> None:
    with lifecycle.acquire_user_lock(
        "alice",
        exclusive=True,
        publish_marker=True,
        run_id="run-id",
        lock_dir=tmp_path,
    ):
        with pytest.raises(lifecycle.UserMaintenanceError):
            lifecycle.acquire_user_entry_lock("alice", lock_dir=tmp_path)
        marker = next(tmp_path.glob("*.maintenance"))
        assert json.loads(marker.read_text(encoding="ascii"))["run_id"] == "run-id"
    assert not list(tmp_path.glob("*.maintenance"))


def test_new_origin_preview_becomes_eligible_at_next_daily_run(tmp_path: Path) -> None:
    identity, journal = _identity(tmp_path)
    started = datetime(2026, 9, 3, 6, 0, tzinfo=retention.SHANGHAI).timestamp()
    origin_id, new_origin, eligible = journal.origin_for(identity, started)
    assert new_origin is True
    assert eligible is None
    run_id = str(uuid.uuid4())
    journal.create_run(
        run_id=run_id,
        origin_id=origin_id,
        mode="preview",
        fingerprint="fingerprint",
        started_at=started,
    )
    journal.finish_run(
        run_id=run_id,
        origin_id=origin_id,
        mode="preview",
        status_value="ok",
        finished_at=started,
    )
    _origin_id, new_origin, eligible = journal.origin_for(identity, started + 1)
    assert new_origin is False
    assert datetime.fromtimestamp(eligible, tz=retention.SHANGHAI).hour == 5
    assert eligible > started


def test_rebind_nonce_never_reuses_origin_with_same_uid_and_home(
    tmp_path: Path,
) -> None:
    identity, journal = _identity(tmp_path)
    first_id, first_new, _eligible = journal.origin_for(
        replace(identity, identity_nonce="first-binding"), time.time()
    )
    second_id, second_new, _eligible = journal.origin_for(
        replace(identity, identity_nonce="second-binding"), time.time() + 1
    )
    assert first_new is True
    assert second_new is True
    assert first_id != second_id
    with journal.connect() as connection:
        rows = connection.execute(
            "select origin_id, active from origins order by created_at"
        ).fetchall()
    assert [(row["origin_id"], row["active"]) for row in rows] == [
        (first_id, 0),
        (second_id, 1),
    ]


def test_enforce_authorization_persists_until_policy_changes(monkeypatch) -> None:
    config = retention.RetentionConfig(
        mode="enforce",
        inactive_days=30,
        history_days=30,
        filesystems=(),
    )
    monkeypatch.setattr(
        retention,
        "_authorization_payload",
        lambda _path: {
            "schema_version": retention.SCHEMA_VERSION,
            "policy_sha256": retention.policy_sha256(config),
            "authorized_at": 100.0,
        },
    )
    assert retention.enforce_is_authorized(
        config,
        authorization_path=Path("/unused"),
        now=100 + 365 * retention.DAY_SECONDS,
    )
    changed = retention.RetentionConfig(
        mode="enforce",
        inactive_days=30,
        history_days=30,
        filesystems=(),
        max_files_per_user=config.max_files_per_user + 1,
    )
    assert not retention.enforce_is_authorized(
        changed,
        authorization_path=Path("/unused"),
        now=101,
    )


def test_status_reader_rejects_unknown_shape_and_oversize(tmp_path: Path) -> None:
    status = tmp_path / "status.json"
    status.write_text("{}", encoding="ascii")
    with pytest.raises(retention.RetentionError, match="schema"):
        retention.read_cleanup_status(status, require_secure_owner=False)
    status.write_bytes(b"x" * (retention.MAX_STATUS_BYTES + 1))
    with pytest.raises(retention.RetentionError, match="invalid"):
        retention.read_cleanup_status(status, require_secure_owner=False)


def test_retention_units_are_preview_safe_templates() -> None:
    root = Path(__file__).parents[1] / "packaging" / "systemd"
    service = (root / "potato-user-data-retention.service").read_text(encoding="utf-8")
    timer = (root / "potato-user-data-retention.timer").read_text(encoding="utf-8")
    assert "User=root" in service
    assert "PrivateNetwork=true" in service
    assert "IOSchedulingClass=idle" in service
    assert "ProtectProc=" not in service
    assert "interface.user_data_retention run" in service
    assert "05:00:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
    assert "systemctl enable" not in service + timer

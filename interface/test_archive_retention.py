from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import interface.archive_store as archive_store


DAY_SECONDS = 86400


def _archive(
    db_path: Path,
    *,
    mapping_username: str,
    session_id: str,
    archived_at: int,
) -> None:
    assert archive_store.archive_session_record(
        mapping_username=mapping_username,
        email_snapshot=f"{mapping_username}@example.com",
        session={"id": session_id, "source": "tui", "started_at": 1},
        messages=[{"role": "user", "content": f"secret-{session_id}"}],
        display_messages=[{"role": "user", "content": f"secret-{session_id}"}],
        draft_title=f"title-{session_id}",
        db_path=db_path,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "update archived_sessions set archived_at = ? where original_session_id = ?",
            (archived_at, session_id),
        )
        conn.commit()


def test_archive_cleanup_deletes_expired_content_and_old_run_metadata(tmp_path) -> None:
    db_path = tmp_path / "archive.db"
    now = 100 * DAY_SECONDS
    _archive(
        db_path,
        mapping_username="alice",
        session_id="expired",
        archived_at=now - 31 * DAY_SECONDS,
    )
    _archive(
        db_path,
        mapping_username="alice",
        session_id="retained",
        archived_at=now - 29 * DAY_SECONDS,
    )
    old_run = archive_store.start_archive_run(db_path)
    current_run = archive_store.start_archive_run(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "update archive_runs set started_at = ?, finished_at = ? where run_id = ?",
            (now - 31 * DAY_SECONDS, now - 31 * DAY_SECONDS, old_run),
        )
        conn.execute(
            "update archive_runs set started_at = ?, finished_at = ? where run_id = ?",
            (now - 1 * DAY_SECONDS, now - 1 * DAY_SECONDS, current_run),
        )
        conn.commit()

    result = archive_store.cleanup_expired_archived_sessions(
        retention_days=30,
        now=now,
        db_path=db_path,
    )

    assert result == {"archived_sessions_deleted": 1, "archive_runs_deleted": 1}
    with sqlite3.connect(str(db_path)) as conn:
        sessions = conn.execute(
            "select original_session_id from archived_sessions"
        ).fetchall()
        runs = conn.execute("select run_id from archive_runs").fetchall()
    assert sessions == [("retained",)]
    assert runs == [(current_run,)]


def test_new_archive_records_do_not_store_email_snapshot(tmp_path) -> None:
    db_path = tmp_path / "archive.db"
    _archive(db_path, mapping_username="alice", session_id="a1", archived_at=1)

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "select email_snapshot from archived_sessions where original_session_id = 'a1'"
        ).fetchone()

    assert row == ("",)


def test_archive_count_is_scoped_to_mapping_username(tmp_path) -> None:
    db_path = tmp_path / "archive.db"
    _archive(db_path, mapping_username="alice", session_id="a1", archived_at=1)
    _archive(db_path, mapping_username="alice", session_id="a2", archived_at=1)
    _archive(db_path, mapping_username="bob", session_id="b1", archived_at=1)

    assert archive_store.count_archived_sessions(db_path) == 3
    assert (
        archive_store.count_archived_sessions(
            db_path, mapping_username="alice"
        )
        == 2
    )
    assert archive_store.count_archived_sessions(db_path, mapping_username="bob") == 1


def test_archive_status_does_not_return_global_run_metadata(monkeypatch, tmp_path) -> None:
    import interface.app as app_mod

    requested_mappings: list[str] = []

    def scoped_count(*, mapping_username: str) -> int:
        requested_mappings.append(mapping_username)
        return 2

    monkeypatch.setattr(app_mod, "count_archived_sessions", scoped_count)
    target = SimpleNamespace(
        systemd_service="hermes-alice.service",
        home_dir=tmp_path,
        workdir=tmp_path,
    )
    user = app_mod.CurrentUser(
        id="user-id",
        email="alice@example.com",
        username="alice",
        name="Alice",
        role="user",
        mapping_username="alice",
        target=target,
    )

    general_status = asyncio.run(app_mod.api_status(user))
    archive_status = asyncio.run(app_mod.archive_status(user))

    assert requested_mappings == ["alice", "alice"]
    assert general_status["archived_session_count"] == 2
    assert archive_status["archived_session_count"] == 2
    assert "runs" not in archive_status
    assert "error_message" not in archive_status

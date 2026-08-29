from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import interface.archive_store as archive_store


DAY_SECONDS = 86400


def test_default_archive_retention_does_not_archive_normal_history() -> None:
    import interface.app as app_mod

    assert app_mod.DEFAULT_ARCHIVE_RETENTION_DAYS == 99999


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


def test_archived_session_exists_is_scoped_to_owner_and_session(tmp_path) -> None:
    db_path = tmp_path / "archive.db"
    _archive(db_path, mapping_username="alice", session_id="a1", archived_at=1)

    assert archive_store.archived_session_exists(
        "alice", "a1", db_path=db_path
    )
    assert not archive_store.archived_session_exists(
        "alice", "missing", db_path=db_path
    )
    assert not archive_store.archived_session_exists(
        "bob", "a1", db_path=db_path
    )


def test_archive_scan_skips_restored_copy_before_share_invalidation(
    monkeypatch,
) -> None:
    import interface.app as app_mod

    class FakeDB:
        def list_sessions_rich(self, **kwargs):
            return [
                {
                    "id": "restored-session",
                    "source": "tui",
                    "started_at": 1,
                    "last_active": 1,
                }
            ]

    monkeypatch.setattr(
        app_mod,
        "_open_session_db",
        lambda target: contextlib.nullcontext(FakeDB()),
    )
    monkeypatch.setattr(
        app_mod,
        "archived_session_exists",
        lambda mapping_username, session_id: True,
    )
    monkeypatch.setattr(
        app_mod,
        "_invalidate_chat_share_session_lifecycle_sync",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("share lifecycle must not be touched")
        ),
    )
    target = SimpleNamespace(username="alice")
    auth_user = SimpleNamespace(id="auth-alice")

    assert app_mod._archive_expired_target_sync(target, auth_user, cutoff=10) == 0


def test_archive_finalize_failure_still_deletes_display_transcript(monkeypatch) -> None:
    import interface.app as app_mod

    session = {
        "id": "expired-session",
        "source": "tui",
        "started_at": 1,
        "last_active": 1,
    }
    events: list[object] = []

    class FakeDB:
        def list_sessions_rich(self, **kwargs):
            return [session]

        def get_compression_tip(self, session_id: str) -> str:
            return session_id

        def get_messages(self, session_id: str):
            return [{"role": "user", "content": "hello"}]

        def get_session(self, session_id: str):
            return session

        def delete_session(self, session_id: str) -> bool:
            events.append(("delete", session_id))
            return True

    def fail_finalize(**kwargs) -> None:
        events.append(("finalize", kwargs["logical_session_id"]))
        raise sqlite3.OperationalError("share store unavailable")

    monkeypatch.setattr(
        app_mod,
        "_open_session_db",
        lambda target: contextlib.nullcontext(FakeDB()),
    )
    monkeypatch.setattr(app_mod, "archived_session_exists", lambda *args: False)
    monkeypatch.setattr(
        app_mod,
        "get_display_session_meta",
        lambda *args: {"messages": [], "draft_title": ""},
    )
    monkeypatch.setattr(
        app_mod,
        "_invalidate_chat_share_session_lifecycle_sync",
        lambda **kwargs: "archive-claim",
    )
    monkeypatch.setattr(
        app_mod,
        "_heartbeat_chat_share_session_lifecycle_sync",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(app_mod, "archive_session_record", lambda **kwargs: True)
    monkeypatch.setattr(
        app_mod,
        "_complete_chat_share_session_lifecycle_sync",
        fail_finalize,
    )
    monkeypatch.setattr(app_mod, "is_temporary_user", lambda user_id: False)
    monkeypatch.setattr(
        app_mod,
        "delete_display_messages",
        lambda user_id, session_id: events.append(
            ("delete_display", user_id, session_id)
        ),
    )

    target = SimpleNamespace(username="alice")
    auth_user = SimpleNamespace(id="auth-alice")

    assert app_mod._archive_expired_target_sync(target, auth_user, cutoff=10) == 1
    assert events == [
        ("delete", "expired-session"),
        ("finalize", "expired-session"),
        ("delete_display", "auth-alice", "expired-session"),
    ]


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

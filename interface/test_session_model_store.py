from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from interface import session_model_store as store
from interface.auth_db import connect_auth_db, ensure_auth_db
from interface.display_store import delete_display_user_data


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "interface.db"


def test_default_reads_initialize_without_creating_session_state(db_path: Path) -> None:
    assert store.get_session_model_state("alice", "conversation", db_path) == {
        "model_id": "",
        "model_revision": 0,
    }
    assert store.list_session_model_states("alice", db_path) == {}
    assert store.ensure_session_model_store(db_path) == db_path


@pytest.mark.parametrize("operation", ["list", "set", "initialize", "restore", "delete"])
def test_each_operation_initializes_an_existing_auth_database(
    db_path: Path, operation: str
) -> None:
    ensure_auth_db(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'session_model_state'"
        ).fetchone() is None
    if operation == "list":
        assert store.list_session_model_states("alice", db_path) == {}
    elif operation == "set":
        assert store.set_session_model("alice", "s", "fast", db_path)["model_id"] == "fast"
    elif operation == "initialize":
        assert store.initialize_session_model("alice", "s", "fast", db_path)["model_id"] == "fast"
    elif operation == "restore":
        assert store.restore_session_model(
            "alice", "s", "fast", expected_revision=10, db_path=db_path
        ) is None
    else:
        store.delete_session_model_state("alice", "s", db_path)


def test_selection_is_scoped_to_user_and_logical_session(db_path: Path) -> None:
    alice_one = store.set_session_model("alice", "one", "fast", db_path)
    alice_two = store.set_session_model("alice", "two", "deep", db_path)
    bob_one = store.set_session_model("bob", "one", "backup", db_path)
    assert store.list_session_model_states("alice", db_path) == {
        "one": alice_one,
        "two": alice_two,
    }
    assert store.list_session_model_states("bob", db_path) == {"one": bob_one}
    assert store.get_session_model_state("alice", "unknown", db_path)["model_id"] == ""
    assert 0 < alice_one["model_revision"] < alice_two["model_revision"] < bob_one["model_revision"]


def test_updates_are_idempotent_and_revisions_survive_deletion(db_path: Path) -> None:
    first = store.set_session_model("alice", "one", "fast", db_path)
    assert store.set_session_model("alice", "one", "fast", db_path) == first
    second = store.set_session_model("alice", "one", "deep", db_path)
    assert second["model_revision"] > first["model_revision"]
    store.delete_session_model_state("alice", "one", db_path)
    assert store.get_session_model_state("alice", "one", db_path)["model_revision"] == 0
    third = store.set_session_model("bob", "two", "fast", db_path)
    assert third["model_revision"] > second["model_revision"]


def test_fork_initialization_is_independent_and_write_once(db_path: Path) -> None:
    source = store.set_session_model("alice", "source", "deep", db_path)
    fork = store.initialize_session_model("alice", "fork", source["model_id"], db_path)
    assert fork["model_id"] == source["model_id"]
    assert fork["model_revision"] > source["model_revision"]
    source = store.set_session_model("alice", "source", "fast", db_path)
    assert store.initialize_session_model("alice", "fork", source["model_id"], db_path) == fork
    changed_fork = store.set_session_model("alice", "fork", "backup", db_path)
    assert store.initialize_session_model("alice", "fork", "deep", db_path) == changed_fork
    assert store.get_session_model_state("alice", "source", db_path) == source


def test_concurrent_initialization_and_retry_choose_one_selection(db_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        initial = list(pool.map(
            lambda i: store.initialize_session_model("alice", "draft", f"model-{i}", db_path),
            range(24),
        ))
    assert all(state == initial[0] for state in initial)
    with ThreadPoolExecutor(max_workers=8) as pool:
        retries = list(pool.map(
            lambda _: store.set_session_model("alice", "draft", "deep", db_path),
            range(24),
        ))
    assert all(state == retries[0] for state in retries)
    assert retries[0]["model_revision"] > initial[0]["model_revision"]


def test_concurrent_updates_receive_unique_global_revisions(db_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = list(pool.map(
            lambda i: store.set_session_model(f"user-{i % 3}", f"s-{i}", "fast", db_path),
            range(24),
        ))
    revisions = {state["model_revision"] for state in states}
    assert len(revisions) == 24
    assert min(revisions) > 0
    latest = store.set_session_model("alice", "latest", "fast", db_path)
    assert latest["model_revision"] > max(revisions)


def test_restore_uses_fresh_revision_and_does_not_overwrite_newer_selection(db_path: Path) -> None:
    previous = store.set_session_model("alice", "one", "fast", db_path)
    attempted = store.set_session_model("alice", "one", "deep", db_path)
    restored = store.restore_session_model(
        "alice", "one", previous["model_id"],
        expected_revision=attempted["model_revision"], db_path=db_path,
    )
    assert restored is not None
    assert restored["model_id"] == previous["model_id"]
    assert restored["model_revision"] > attempted["model_revision"]
    assert store.restore_session_model(
        "alice", "one", "backup", expected_revision=attempted["model_revision"], db_path=db_path,
    ) is None
    assert store.get_session_model_state("alice", "one", db_path) == restored


def test_restore_default_keeps_revision_to_reject_delayed_results(db_path: Path) -> None:
    attempted = store.set_session_model("alice", "one", "deep", db_path)
    restored = store.restore_session_model(
        "alice", "one", "", expected_revision=attempted["model_revision"], db_path=db_path,
    )
    assert restored is not None
    assert restored["model_id"] == ""
    assert restored["model_revision"] > attempted["model_revision"]
    assert store.list_session_model_states("alice", db_path) == {"one": restored}
    assert store.initialize_session_model("alice", "one", "deep", db_path) == restored
    assert store.set_session_model("alice", "one", "", db_path) == restored


def test_concurrent_compensation_only_restores_once(db_path: Path) -> None:
    attempted = store.set_session_model("alice", "one", "deep", db_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: store.restore_session_model(
                "alice", "one", "fast", expected_revision=attempted["model_revision"], db_path=db_path,
            ),
            range(24),
        ))
    restored = [state for state in results if state is not None]
    assert len(restored) == 1
    assert store.get_session_model_state("alice", "one", db_path) == restored[0]


def test_delete_does_not_resurrect_session_on_stale_restore(db_path: Path) -> None:
    attempted = store.set_session_model("alice", "one", "deep", db_path)
    other = store.set_session_model("bob", "one", "fast", db_path)
    store.delete_session_model_state("alice", "one", db_path)
    store.delete_session_model_state("alice", "one", db_path)
    assert store.restore_session_model(
        "alice", "one", "fast", expected_revision=attempted["model_revision"], db_path=db_path,
    ) is None
    assert store.list_session_model_states("alice", db_path) == {}
    assert store.list_session_model_states("bob", db_path) == {"one": other}


def test_user_deletion_trigger_and_explicit_cleanup(db_path: Path) -> None:
    store.ensure_session_model_store(db_path)
    with connect_auth_db(db_path) as conn:
        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, name, "
            "mapping_username, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("alice", "alice", "alice@example.com", "unused", "Alice", "alice", 1, 1),
        )
    store.set_session_model("alice", "one", "deep", db_path)
    store.set_session_model("alice", "two", "fast", db_path)
    other = store.set_session_model("bob", "one", "backup", db_path)
    with connect_auth_db(db_path) as conn:
        conn.execute("DELETE FROM users WHERE id = 'alice'")
    assert store.list_session_model_states("alice", db_path) == {}
    assert store.list_session_model_states("bob", db_path) == {"one": other}
    assert delete_display_user_data("bob", db_path)["session_models"] == 1
    assert store.list_session_model_states("bob", db_path) == {}
    assert delete_display_user_data("bob", db_path)["session_models"] == 0


def test_replaced_database_is_initialized_again(db_path: Path) -> None:
    store.set_session_model("alice", "one", "deep", db_path)
    # Rename keeps the old inode alive so the replacement is a distinct DB.
    db_path.rename(db_path.with_suffix(".old"))
    assert store.get_session_model_state("alice", "one", db_path) == {
        "model_id": "", "model_revision": 0,
    }
    assert store.set_session_model("alice", "one", "fast", db_path)["model_revision"] > 0

from __future__ import annotations

import sqlite3

import pytest

import interface.auth_db as auth_db


LEGACY_USERS_SCHEMA = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    mapping_username TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    auth_session_version INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_interface_users_mapping_username ON users(mapping_username);
"""


def _insert_legacy_user(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    username: str,
    mapping_username: str,
) -> None:
    conn.execute(
        "insert into users "
        "(id, username, email, password_hash, name, role, mapping_username, "
        "active, auth_session_version, created_at, updated_at) "
        "values (?, ?, ?, 'hash', ?, 'user', ?, 1, 0, 1, 1)",
        (user_id, username, f"{username}@example.com", username, mapping_username),
    )


def test_existing_duplicate_mappings_fail_without_modifying_users(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(LEGACY_USERS_SCHEMA)
        _insert_legacy_user(
            conn, user_id="one", username="alice", mapping_username="shared"
        )
        _insert_legacy_user(
            conn, user_id="two", username="bob", mapping_username="shared"
        )
        conn.commit()

    with pytest.raises(auth_db.MappingUsernameConflictError, match="'shared'"):
        auth_db.ensure_auth_db(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "select id, mapping_username from users order by id"
        ).fetchall()
        index_row = next(
            row
            for row in conn.execute("pragma index_list(users)").fetchall()
            if row[1] == "idx_interface_users_mapping_username"
        )
    assert rows == [("one", "shared"), ("two", "shared")]
    assert int(index_row[2]) == 0


def test_legacy_mapping_index_is_upgraded_to_unique(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(LEGACY_USERS_SCHEMA)
        _insert_legacy_user(
            conn, user_id="one", username="alice", mapping_username="alice"
        )
        _insert_legacy_user(
            conn, user_id="two", username="bob", mapping_username="bob"
        )
        conn.commit()

    auth_db.ensure_auth_db(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        index_row = next(
            row
            for row in conn.execute("pragma index_list(users)").fetchall()
            if row[1] == "idx_interface_users_mapping_username"
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_legacy_user(
                conn,
                user_id="three",
                username="carol",
                mapping_username="alice",
            )
    assert int(index_row[2]) == 1

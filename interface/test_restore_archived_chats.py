from __future__ import annotations

import json
import os
import pwd
import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml

import restore_archived_chats as restore
from interface import archive_store, chat_share_store


STATE_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT,
    title TEXT,
    api_call_count INTEGER DEFAULT 0,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT,
    codex_message_items TEXT,
    platform_message_id TEXT,
    observed INTEGER DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX idx_sessions_title_unique
ON sessions(title) WHERE title IS NOT NULL;

CREATE VIRTUAL TABLE messages_fts USING fts5(content);
CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, COALESCE(new.content, ''));
END;

CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(content, tokenize='trigram');
CREATE TRIGGER messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts_trigram(rowid, content) VALUES (
        new.id,
        COALESCE(new.content, '')
    );
END;
"""


INTERFACE_SCHEMA = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    mapping_username TEXT NOT NULL UNIQUE
);
CREATE TABLE session_display_transcripts (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    messages_json TEXT NOT NULL DEFAULT '[]',
    draft_title TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, session_id)
);
"""


class RestoreArchivedChatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="restore-archived-chats-")
        self.root = Path(self.temp_dir.name)
        self.archive_db = self.root / "archive.db"
        self.interface_db = self.root / "interface.db"
        self.mapping_path = self.root / "users_mapping.yaml"
        self.hermes_home = self.root / "alice" / ".hermes"
        self.hermes_home.mkdir(parents=True)
        self.state_db = self.hermes_home / "state.db"
        with sqlite3.connect(str(self.state_db)) as conn:
            conn.executescript(STATE_SCHEMA)
        with sqlite3.connect(str(self.interface_db)) as conn:
            conn.executescript(INTERFACE_SCHEMA)
            conn.execute(
                "insert into users (id, mapping_username) values (?, ?)",
                ("auth-alice", "alice"),
            )
        linux_user = pwd.getpwuid(os.getuid()).pw_name
        self.mapping_path.write_text(
            yaml.safe_dump(
                {
                    "users": [
                        {
                            "username": "alice",
                            "linux_user": linux_user,
                            "home_dir": str(self.hermes_home.parent),
                            "hermes_home": str(self.hermes_home),
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self.paths = restore.RestorePaths(
            archive_db=self.archive_db,
            interface_db=self.interface_db,
            mapping=self.mapping_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def archive_chat(
        self,
        *,
        session_id: str,
        message_id: int,
        title: str,
        hermes_title: str | None = None,
        logical_root_id: str | None = None,
        structured: bool = False,
    ) -> None:
        session = {
            "id": session_id,
            "source": "tui",
            "model": "test-model",
            "model_config": '{"temperature":0}',
            "system_prompt": "system",
            "parent_session_id": None,
            "started_at": 1000.0 + message_id,
            "last_active": 1050.25 + message_id,
            "ended_at": 1100.0 + message_id,
            "end_reason": "tui_shutdown",
            "message_count": 1,
            "tool_call_count": 1,
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 3,
            "cache_write_tokens": 4,
            "reasoning_tokens": 5,
            "cwd": str(self.root),
            "title": title if hermes_title is None else hermes_title,
            "api_call_count": 1,
            "rewind_count": 0,
            "archived": 0,
        }
        if logical_root_id:
            session["_lineage_root_id"] = logical_root_id
        content = (
            [{"type": "text", "text": "structured archive"}]
            if structured
            else "archived message"
        )
        messages = [
            {
                "id": message_id,
                "session_id": session_id,
                "role": "assistant",
                "content": content,
                "tool_call_id": None,
                "tool_calls": [{"id": "call-1", "type": "function"}],
                "tool_name": None,
                "timestamp": 1050.25 + message_id,
                "token_count": 7,
                "finish_reason": "stop",
                "reasoning": "reasoning",
                "reasoning_content": "reasoning-content",
                "reasoning_details": '{"kind":"detail"}',
                "codex_reasoning_items": '[{"kind":"reason"}]',
                "codex_message_items": '[{"kind":"message"}]',
                "platform_message_id": "platform-1",
                "observed": 1,
                "active": 1,
            }
        ]
        display_messages = [
            {
                "id": f"display-{message_id}",
                "role": "assistant",
                "content": "visible archive",
                "timestamp": 1050.25 + message_id,
            }
        ]
        self.assertTrue(
            archive_store.archive_session_record(
                mapping_username="alice",
                email_snapshot="",
                session=session,
                messages=messages,
                display_messages=display_messages,
                draft_title=title,
                db_path=self.archive_db,
            )
        )

    def test_dry_run_and_apply_restore_regular_and_compressed_chats(self) -> None:
        self.archive_chat(
            session_id="regular-session",
            message_id=101,
            title="Duplicate archive title",
            hermes_title="",
            structured=True,
        )
        self.archive_chat(
            session_id="compressed-tip",
            logical_root_id="compressed-root",
            message_id=202,
            title="Duplicate archive title",
            hermes_title="",
        )

        dry_run, backup = restore.run_restore(self.paths)

        self.assertTrue(dry_run.ok, dry_run.errors)
        self.assertIsNone(backup)
        self.assertEqual(dry_run.archive_records, 2)
        self.assertEqual(dry_run.compressed_records, 1)
        self.assertEqual(dry_run.needs_state, 2)
        self.assertEqual(dry_run.needs_display, 2)
        with sqlite3.connect(str(self.state_db)) as conn:
            self.assertEqual(conn.execute("select count(*) from sessions").fetchone()[0], 0)

        backup_dir = self.root / "backup-first"
        applied, created_backup = restore.run_restore(
            self.paths,
            apply=True,
            backup_dir=backup_dir,
        )

        self.assertTrue(applied.ok, applied.errors)
        self.assertEqual(applied.restored_state, 2)
        self.assertEqual(applied.restored_display, 2)
        self.assertEqual(created_backup, backup_dir.absolute())
        self.assertTrue((backup_dir / "archive.db").is_file())
        self.assertTrue((backup_dir / "interface.db").is_file())
        self.assertTrue((backup_dir / "state--alice.db").is_file())
        self.assertEqual(backup_dir.stat().st_mode & 0o777, 0o700)

        with sqlite3.connect(str(self.state_db)) as conn:
            conn.row_factory = sqlite3.Row
            sessions = {
                row["id"]: dict(row)
                for row in conn.execute("select * from sessions order by id")
            }
            self.assertEqual(set(sessions), {"regular-session", "compressed-root"})
            self.assertIsNone(sessions["compressed-root"]["parent_session_id"])
            self.assertIsNone(sessions["regular-session"]["title"])
            self.assertIsNone(sessions["compressed-root"]["title"])
            self.assertEqual(sessions["compressed-root"]["message_count"], 1)
            messages = {
                row["id"]: dict(row)
                for row in conn.execute("select * from messages order by id")
            }
            self.assertEqual(set(messages), {101, 202})
            self.assertEqual(messages[202]["session_id"], "compressed-root")
            self.assertEqual(
                json.loads(messages[101]["content"][len(restore.CONTENT_JSON_PREFIX) :]),
                [{"type": "text", "text": "structured archive"}],
            )
            self.assertEqual(
                json.loads(messages[101]["tool_calls"]),
                [{"id": "call-1", "type": "function"}],
            )
            self.assertEqual(messages[101]["reasoning_details"], '{"kind":"detail"}')
            self.assertEqual(
                conn.execute(
                    "select count(*) from messages_fts where rowid in (101, 202)"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "select count(*) from messages_fts_trigram where rowid in (101, 202)"
                ).fetchone()[0],
                2,
            )
        with sqlite3.connect(str(self.interface_db)) as conn:
            display = conn.execute(
                "select session_id, created_at, updated_at, draft_title "
                "from session_display_transcripts order by session_id"
            ).fetchall()
            self.assertEqual(
                [row[0] for row in display],
                ["compressed-root", "regular-session"],
            )
            self.assertTrue(all(row[1] < row[2] for row in display))
            self.assertEqual(
                {row[3] for row in display},
                {"Duplicate archive title"},
            )
        with sqlite3.connect(str(self.archive_db)) as conn:
            self.assertEqual(
                conn.execute("select count(*) from archived_sessions").fetchone()[0],
                2,
            )

    def test_restore_is_idempotent_and_repairs_missing_display(self) -> None:
        self.archive_chat(
            session_id="session-one",
            message_id=303,
            title="Idempotent archive",
        )
        first, _ = restore.run_restore(
            self.paths,
            apply=True,
            backup_dir=self.root / "backup-first",
        )
        self.assertTrue(first.ok, first.errors)
        with sqlite3.connect(str(self.state_db)) as conn:
            self.assertEqual(
                conn.execute(
                    "select title from sessions where id = ?",
                    ("session-one",),
                ).fetchone()[0],
                "Idempotent archive",
            )
        with sqlite3.connect(str(self.interface_db)) as conn:
            conn.execute("delete from session_display_transcripts")

        preflight, _ = restore.run_restore(self.paths)
        self.assertTrue(preflight.ok, preflight.errors)
        self.assertEqual(preflight.needs_state, 0)
        self.assertEqual(preflight.needs_display, 1)

        second, _ = restore.run_restore(
            self.paths,
            apply=True,
            backup_dir=self.root / "backup-second",
        )
        self.assertTrue(second.ok, second.errors)
        self.assertEqual(second.restored_state, 0)
        self.assertEqual(second.restored_display, 1)
        third, _ = restore.run_restore(self.paths)
        self.assertTrue(third.ok, third.errors)
        self.assertEqual(third.already_restored, 1)

    def test_restore_clears_source_share_tombstone_and_backs_up_share_db(self) -> None:
        self.archive_chat(
            session_id="restored-share-source",
            message_id=350,
            title="Restored share source",
        )
        share_db = self.root / "chat-shares.db"
        chat_share_store.invalidate_source_session_shares(
            "auth-alice",
            "restored-share-source",
            lifecycle_claim_id="archive-claim",
            db_path=share_db,
        )
        paths = restore.RestorePaths(
            archive_db=self.archive_db,
            interface_db=self.interface_db,
            mapping=self.mapping_path,
            chat_share_db=share_db,
        )

        restored, backup_dir = restore.run_restore(
            paths,
            apply=True,
            backup_dir=self.root / "backup-with-shares",
        )

        self.assertTrue(restored.ok, restored.errors)
        self.assertIsNotNone(backup_dir)
        self.assertTrue((backup_dir / "chat-shares.db").is_file())
        with sqlite3.connect(str(share_db)) as conn:
            tombstones = conn.execute(
                "select owner_user_id, source_session_id "
                "from chat_share_source_tombstones"
            ).fetchall()
        self.assertEqual(tombstones, [])

    def test_conflicting_live_session_blocks_restore_without_writes(self) -> None:
        self.archive_chat(
            session_id="conflict-session",
            message_id=404,
            title="Conflict archive",
        )
        with sqlite3.connect(str(self.state_db)) as conn:
            conn.execute(
                "insert into sessions (id, source, started_at, title) values (?, ?, ?, ?)",
                ("conflict-session", "tui", 1, "Existing title"),
            )
            conn.execute(
                "insert into messages (id, session_id, role, content, timestamp) "
                "values (?, ?, ?, ?, ?)",
                (999, "conflict-session", "user", "different", 2),
            )

        summary, backup = restore.run_restore(self.paths)

        self.assertFalse(summary.ok)
        self.assertIsNone(backup)
        self.assertTrue(any("different messages" in error for error in summary.errors))
        with sqlite3.connect(str(self.interface_db)) as conn:
            self.assertEqual(
                conn.execute("select count(*) from session_display_transcripts").fetchone()[0],
                0,
            )

    def test_stale_trigram_fts_row_blocks_restore_without_writes(self) -> None:
        self.archive_chat(
            session_id="fts-conflict-session",
            message_id=505,
            title="FTS conflict archive",
        )
        with sqlite3.connect(str(self.state_db)) as conn:
            conn.execute(
                "insert into messages_fts_trigram(rowid, content) values (?, ?)",
                (505, "stale row"),
            )

        summary, backup = restore.run_restore(self.paths)

        self.assertFalse(summary.ok)
        self.assertIsNone(backup)
        self.assertTrue(
            any("messages_fts_trigram" in error for error in summary.errors),
            summary.errors,
        )
        with sqlite3.connect(str(self.state_db)) as conn:
            self.assertEqual(conn.execute("select count(*) from sessions").fetchone()[0], 0)

    def test_non_null_archived_field_requires_target_schema_column(self) -> None:
        self.archive_chat(
            session_id="missing-column-session",
            message_id=606,
            title="Missing column archive",
        )
        with sqlite3.connect(str(self.archive_db)) as conn:
            row = conn.execute(
                "select archive_id, session_json from archived_sessions"
            ).fetchone()
            session = json.loads(row[1])
            session["billing_provider"] = "custom"
            conn.execute(
                "update archived_sessions set session_json = ? where archive_id = ?",
                (json.dumps(session), row[0]),
            )

        summary, backup = restore.run_restore(self.paths)

        self.assertFalse(summary.ok)
        self.assertIsNone(backup)
        self.assertTrue(
            any("billing_provider" in error for error in summary.errors),
            summary.errors,
        )


if __name__ == "__main__":
    unittest.main()

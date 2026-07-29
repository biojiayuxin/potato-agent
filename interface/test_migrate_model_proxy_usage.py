from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import migrate_model_proxy_usage
from interface import token_usage_store


def _record_usage(path: Path, *, input_tokens: int) -> str:
    return token_usage_store.record_usage_request(
        mapping_username="alice",
        endpoint="responses",
        route_model="Main",
        upstream_model="gpt-5.4",
        provider="custom",
        status_code=200,
        streaming=False,
        started_at=100.0,
        completed_at=101.0,
        duration_ms=1000,
        input_tokens=input_tokens,
        usage_status="present",
        raw_usage={"input_tokens": input_tokens},
        db_path=path,
    )


def test_migration_copies_usage_and_quota_idempotently(tmp_path: Path) -> None:
    source = tmp_path / "interface.db"
    destination = tmp_path / "proxy" / "usage.db"
    request_id = _record_usage(source, input_tokens=12)
    sentinel = "sk-legacy-value-must-not-be-migrated"
    with sqlite3.connect(str(source)) as conn:
        conn.execute(
            "update model_proxy_usage_requests set raw_usage_json = ? where id = ?",
            (json.dumps({"authorization": sentinel, "prompt": "private chat"}), request_id),
        )
    token_usage_store.set_user_quota(
        "alice",
        total_token_limit=1000,
        period="monthly",
        enabled=True,
        db_path=source,
    )

    first = migrate_model_proxy_usage.migrate_usage_database(source, destination)
    token_usage_store.set_user_quota(
        "alice",
        total_token_limit=2000,
        enabled=True,
        db_path=destination,
    )
    second = migrate_model_proxy_usage.migrate_usage_database(source, destination)

    assert first == migrate_model_proxy_usage.MigrationResult(1, 1)
    assert second == first
    with sqlite3.connect(str(destination)) as conn:
        usage = conn.execute(
            "select id, mapping_username, input_tokens, raw_usage_json "
            "from model_proxy_usage_requests"
        ).fetchall()
        quota = conn.execute(
            "select mapping_username, total_token_limit, enabled "
            "from model_proxy_user_quotas"
        ).fetchall()
    assert usage == [
        (
            request_id,
            "alice",
            12,
            '{"input_tokens":12,"output_tokens":0,'
            '"cache_read_tokens":0,"cache_write_tokens":0}',
        )
    ]
    assert sentinel not in usage[0][3]
    assert "private chat" not in usage[0][3]
    assert quota == [("alice", 2000, 1)]
    assert destination.stat().st_mode & 0o777 == 0o600


def test_migration_allows_source_without_proxy_tables(tmp_path: Path) -> None:
    source = tmp_path / "interface.db"
    destination = tmp_path / "proxy" / "usage.db"
    with sqlite3.connect(str(source)) as conn:
        conn.execute("create table users (id text primary key)")

    result = migrate_model_proxy_usage.migrate_usage_database(source, destination)

    assert result == migrate_model_proxy_usage.MigrationResult(0, 0)
    with sqlite3.connect(str(destination)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
    assert migrate_model_proxy_usage.USAGE_TABLE in tables
    assert migrate_model_proxy_usage.QUOTA_TABLE in tables


def test_migration_rejects_same_database(tmp_path: Path) -> None:
    source = tmp_path / "usage.db"
    token_usage_store.ensure_token_usage_store(source)

    try:
        migrate_model_proxy_usage.migrate_usage_database(source, source)
    except migrate_model_proxy_usage.ModelProxyUsageMigrationError as exc:
        assert "different" in str(exc)
    else:
        raise AssertionError("same source and destination were accepted")

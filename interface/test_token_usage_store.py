from __future__ import annotations

import sqlite3
from pathlib import Path

from interface import token_usage_store


def _record(
    db_path: Path,
    *,
    mapping_username: str = "alice",
    started_at: float = 100.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> str:
    return token_usage_store.record_usage_request(
        mapping_username=mapping_username,
        endpoint="chat/completions",
        route_model="Main",
        upstream_model="gpt-5.4",
        provider="custom",
        api_mode="",
        status_code=200,
        streaming=False,
        started_at=started_at,
        completed_at=started_at + 1.0,
        duration_ms=1000,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        usage_status="present",
        raw_usage={"total_tokens": input_tokens + output_tokens},
        db_path=db_path,
    )


def test_ensure_token_usage_store_creates_tables_idempotently(tmp_path) -> None:
    db_path = tmp_path / "interface.db"

    token_usage_store.ensure_token_usage_store(db_path)
    token_usage_store.ensure_token_usage_store(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        usage_columns = {
            row[1]
            for row in conn.execute(
                "pragma table_info(model_proxy_usage_requests)"
            ).fetchall()
        }
        index_names = {
            row[1]
            for row in conn.execute(
                "pragma index_list(model_proxy_usage_requests)"
            ).fetchall()
        }

    assert "model_proxy_usage_requests" in tables
    assert "model_proxy_user_quotas" in tables
    assert {
        "mapping_username",
        "endpoint",
        "route_model",
        "upstream_model",
        "provider",
        "api_mode",
        "status_code",
        "streaming",
        "started_at",
        "completed_at",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "usage_status",
        "raw_usage_json",
    }.issubset(usage_columns)
    assert "idx_model_proxy_usage_user_started" in index_names
    assert "idx_model_proxy_usage_started" in index_names
    assert "idx_model_proxy_usage_route_model_started" in index_names


def test_get_user_usage_aggregates_single_user(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _record(
        db_path,
        input_tokens=10,
        output_tokens=4,
        cache_read_tokens=3,
        cache_write_tokens=2,
    )
    _record(db_path, started_at=110, input_tokens=5, output_tokens=7)

    usage = token_usage_store.get_user_usage("alice", db_path=db_path)

    assert usage == {
        "mapping_username": "alice",
        "request_count": 2,
        "input_tokens": 15,
        "output_tokens": 11,
        "cache_read_tokens": 3,
        "cache_write_tokens": 2,
        "total_tokens": 31,
    }


def test_get_user_usage_applies_inclusive_exclusive_time_window(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _record(db_path, started_at=100, input_tokens=1)
    _record(db_path, started_at=200, input_tokens=2)
    _record(db_path, started_at=300, input_tokens=4)

    usage = token_usage_store.get_user_usage(
        "alice", start_at=200, end_at=300, db_path=db_path
    )

    assert usage["request_count"] == 1
    assert usage["input_tokens"] == 2
    assert usage["total_tokens"] == 2


def test_get_usage_by_user_groups_multiple_users(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    _record(db_path, mapping_username="bob", input_tokens=4, output_tokens=1)
    _record(db_path, mapping_username="alice", input_tokens=2)
    _record(db_path, mapping_username="bob", input_tokens=3, cache_read_tokens=2)

    usage = token_usage_store.get_usage_by_user(db_path=db_path)

    assert usage == [
        {
            "mapping_username": "alice",
            "request_count": 1,
            "input_tokens": 2,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 2,
        },
        {
            "mapping_username": "bob",
            "request_count": 2,
            "input_tokens": 7,
            "output_tokens": 1,
            "cache_read_tokens": 2,
            "cache_write_tokens": 0,
            "total_tokens": 10,
        },
    ]


def test_quota_helpers_read_write_and_snapshot(tmp_path) -> None:
    db_path = tmp_path / "interface.db"

    assert token_usage_store.get_user_quota("alice", db_path=db_path) is None

    alice_quota = token_usage_store.set_user_quota(
        "alice",
        input_token_limit=100,
        output_token_limit=200,
        cache_read_token_limit=300,
        cache_write_token_limit=400,
        total_token_limit=1000,
        period="daily",
        enabled=True,
        db_path=db_path,
    )
    bob_quota = token_usage_store.set_user_quota(
        "bob", total_token_limit=50, db_path=db_path
    )

    assert alice_quota["enabled"] is True
    assert alice_quota["period"] == "daily"
    assert alice_quota["input_token_limit"] == 100
    assert alice_quota["output_token_limit"] == 200
    assert alice_quota["cache_read_token_limit"] == 300
    assert alice_quota["cache_write_token_limit"] == 400
    assert alice_quota["total_token_limit"] == 1000
    assert bob_quota["enabled"] is False

    snapshot = token_usage_store.get_quota_snapshot(db_path=db_path)

    assert [quota["mapping_username"] for quota in snapshot] == ["alice", "bob"]
    assert token_usage_store.get_user_quota("alice", db_path=db_path) == alice_quota

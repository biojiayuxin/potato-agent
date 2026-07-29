from __future__ import annotations

import sqlite3
import stat
from datetime import datetime, timezone
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
    assert "users" not in tables
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
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


def test_usage_store_discards_arbitrary_raw_upstream_fields(tmp_path) -> None:
    db_path = tmp_path / "usage.db"
    sentinel = "sk-must-not-be-persisted"

    request_id = token_usage_store.record_usage_request(
        mapping_username="alice",
        endpoint="responses",
        route_model="Main",
        upstream_model="gpt-5.4",
        provider="custom",
        status_code=200,
        streaming=False,
        started_at=1,
        completed_at=2,
        duration_ms=1000,
        input_tokens=3,
        output_tokens=4,
        usage_status="present",
        raw_usage={"authorization": sentinel, "prompt": "private chat"},
        db_path=db_path,
    )

    with sqlite3.connect(str(db_path)) as conn:
        stored = conn.execute(
            "select raw_usage_json from model_proxy_usage_requests where id = ?",
            (request_id,),
        ).fetchone()[0]

    assert sentinel not in stored
    assert "private chat" not in stored
    assert stored == (
        '{"input_tokens":3,"output_tokens":4,'
        '"cache_read_tokens":0,"cache_write_tokens":0}'
    )


def test_default_usage_db_is_independent_from_interface_auth_db(
    monkeypatch, tmp_path
) -> None:
    usage_path = tmp_path / "proxy" / "usage.db"
    monkeypatch.setenv("POTATO_MODEL_PROXY_USAGE_DB", str(usage_path))
    monkeypatch.setenv("INTERFACE_AUTH_DB", str(tmp_path / "auth" / "interface.db"))

    assert token_usage_store.ensure_token_usage_store() == usage_path
    assert usage_path.exists()
    assert not (tmp_path / "auth" / "interface.db").exists()


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


def test_quota_status_uses_current_utc_period_and_reports_boundary(tmp_path) -> None:
    db_path = tmp_path / "interface.db"
    before_period = datetime(2026, 7, 28, 23, 59, tzinfo=timezone.utc).timestamp()
    in_period = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc).timestamp()
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc).timestamp()
    _record(db_path, started_at=before_period, input_tokens=100)
    _record(db_path, started_at=in_period, input_tokens=10)
    token_usage_store.set_user_quota(
        "alice",
        input_token_limit=10,
        period="daily",
        enabled=True,
        db_path=db_path,
    )

    status = token_usage_store.get_user_quota_status(
        "alice", now=now, db_path=db_path
    )

    assert status["allowed"] is False
    assert status["usage"]["input_tokens"] == 10
    assert status["exceeded"] == ["input_tokens"]
    assert status["reset_at"] == datetime(
        2026, 7, 30, tzinfo=timezone.utc
    ).timestamp()


def test_set_user_quota_rejects_invalid_period_and_negative_limit(tmp_path) -> None:
    db_path = tmp_path / "interface.db"

    try:
        token_usage_store.set_user_quota(
            "alice", period="yearly", enabled=True, db_path=db_path
        )
    except ValueError as exc:
        assert "period must be one of" in str(exc)
    else:
        raise AssertionError("invalid quota period was accepted")

    try:
        token_usage_store.set_user_quota(
            "alice", total_token_limit=-1, enabled=True, db_path=db_path
        )
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative quota was accepted")

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from interface import admin_store, admin_usage_client, auth_db, dashboard_api, model_proxy, token_usage_store
from interface.admin_usage_client import AdminUsageUnavailable
from interface.test_admin_proxy_usage import _record


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


@pytest.fixture
def source(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.db"
    usage_path = tmp_path / "usage.db"
    auth_db.ensure_auth_db(auth_path)
    token_usage_store.ensure_token_usage_store(usage_path)
    monkeypatch.setattr(dashboard_api, "AUTH_DB_PATH", auth_path)
    monkeypatch.setattr(dashboard_api, "_now", lambda: _instant("2026-09-06T16:00:00"))

    async def fetch(**kwargs):
        return token_usage_store.get_admin_usage_daily(**kwargs, db_path=usage_path)

    monkeypatch.setattr(dashboard_api, "fetch_usage_daily", fetch)
    return auth_path, usage_path


def _user(path, name, *, temporary=False):
    create = auth_db.create_temporary_user if temporary else auth_db.upsert_user
    return create(
        username=name, email=f"{name}@example.test", password="Password1!",
        mapping_username=name, db_path=path,
    )


def test_daily_sql_matches_admin_and_beijing_midnight(source):
    _, path = source
    start = _instant("2026-08-07T16:00:00").timestamp()
    midnight = _instant("2026-09-05T16:00:00").timestamp()
    end = _instant("2026-09-06T16:00:00").timestamp()
    _record(path, username="alice", started_at=start - .001, input_tokens=999)
    _record(path, username="alice", started_at=start, input_tokens=2)
    _record(path, username="alice", started_at=midnight - .0001, input_tokens=3)
    _record(path, username="alice", started_at=midnight, input_tokens=5,
            output_tokens=7, cache_read_tokens=11, cache_write_tokens=13)
    _record(path, username="bob", started_at=midnight, input_tokens=17)
    _record(path, username="alice", started_at=midnight + 10, status_code=500, input_tokens=999)
    _record(path, username="alice", started_at=midnight + 20, usage_status="missing")
    _record(path, username="alice", started_at=end, input_tokens=999)

    rows = token_usage_store.get_admin_usage_daily(start_at=start, end_at=end, db_path=path)
    assert [(row["date"], row["mapping_username"], row["total_tokens"]) for row in rows] == [
        ("2026-08-08", "alice", 2), ("2026-09-05", "alice", 3),
        ("2026-09-06", "alice", 36), ("2026-09-06", "bob", 17),
    ]
    aggregate = token_usage_store.get_admin_usage_aggregate(start_at=start, end_at=end, db_path=path)
    for user in aggregate:
        for field in (*dashboard_api.TOKEN_FIELDS, "total_tokens"):
            assert sum(row[field] for row in rows if row["mapping_username"] == user["mapping_username"]) == user[field]


@pytest.mark.asyncio
async def test_public_totals_include_current_and_retired_temporary_users(source):
    auth_path, path = source
    _user(auth_path, "alice")
    _user(auth_path, "current_temp", temporary=True)
    retired = _user(auth_path, "retired_temp", temporary=True)
    auth_db.delete_user_by_id(retired.id, db_path=auth_path)
    legacy = "temp_1234567890_abcdef12"
    admin_store.reconcile_principal_catalog([
        {"mapping_username": legacy, "first_used_at": 1, "last_used_at": 2},
        {"mapping_username": "daily-updates-service", "first_used_at": 1},
    ], db_path=auth_path)
    start = _instant("2026-08-07T16:00:00").timestamp()
    for index in range(30):
        _record(path, username="alice", started_at=start + index * 86400, input_tokens=1,
                output_tokens=2, cache_read_tokens=3, cache_write_tokens=4)
    for name, count in (("current_temp", 20), ("retired_temp", 30), (legacy, 40),
                        ("daily-updates-service", 99999), ("unknown", 99999)):
        _record(path, username=name, started_at=start + 29 * 86400, input_tokens=count)
    _record(path, username="alice", started_at=start + 30 * 86400, input_tokens=99999)

    payload = await dashboard_api.DashboardCache().usage()
    assert set(payload) == {"status", "time_zone", "start_date", "through_date", "days"}
    assert payload["status"] == "available"
    assert payload["time_zone"] == "Asia/Shanghai"
    assert payload["start_date"] == "2026-08-08"
    assert payload["through_date"] == "2026-09-06"
    assert len(payload["days"]) == 30
    assert sum(day["total_tokens"] for day in payload["days"]) == 390
    assert sum(day["total_tokens"] for day in payload["days"][-7:]) == 160
    for field, total in (("input_tokens", 120), ("output_tokens", 60),
                         ("cache_read_tokens", 90), ("cache_write_tokens", 120)):
        assert sum(day[field] for day in payload["days"]) == total
    assert all(set(day) == {"date", "total_tokens", *dashboard_api.TOKEN_FIELDS} for day in payload["days"])
    assert "alice" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_usage_cache_coalesces_and_expires_at_beijing_midnight(source, monkeypatch):
    now = [_instant("2026-09-06T15:59:59")]
    calls = []

    async def fetch(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0)
        return []

    monkeypatch.setattr(dashboard_api, "_now", lambda: now[0])
    monkeypatch.setattr(dashboard_api, "fetch_usage_daily", fetch)
    cache = dashboard_api.DashboardCache()
    results = await asyncio.gather(*(cache.usage() for _ in range(20)))
    assert len(calls) == 1
    assert all(result == results[0] for result in results)
    assert results[0]["through_date"] == "2026-09-05"
    assert results[0]["days"][-1]["total_tokens"] == 0
    assert calls[0]["end_at"] == _instant("2026-09-05T16:00:00").timestamp()
    now[0] += timedelta(seconds=1)
    assert (await cache.usage())["through_date"] == "2026-09-06"
    assert len(calls) == 2
    assert calls[1]["start_at"] == _instant("2026-08-07T16:00:00").timestamp()


@pytest.mark.asyncio
async def test_failure_cooldown_recovery_and_rollover_do_not_publish_stale_zeros(source, monkeypatch):
    clock = [100.0]
    now = [_instant("2026-09-06T15:59:59")]
    fail = [False]
    calls = []

    async def fetch(**kwargs):
        calls.append(kwargs)
        if fail[0]:
            raise AdminUsageUnavailable("private source error")
        return []

    monkeypatch.setattr(dashboard_api, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(dashboard_api, "_now", lambda: now[0])
    monkeypatch.setattr(dashboard_api, "fetch_usage_daily", fetch)
    cache = dashboard_api.DashboardCache()
    assert (await cache.usage())["status"] == "available"
    now[0] += timedelta(seconds=1)
    fail[0] = True
    results = await asyncio.gather(*(cache.usage() for _ in range(10)))
    assert len(calls) == 2
    assert all(result["status"] == "unavailable" and result["days"] is None for result in results)
    clock[0] += 59
    assert (await cache.usage())["days"] is None
    assert len(calls) == 2
    clock[0] += 1
    fail[0] = False
    assert (await cache.usage())["status"] == "available"
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_source", "missing_directory", "directory", "invalid_count", "invalid_date"])
async def test_missing_and_invalid_sources_are_unavailable(source, monkeypatch, failure):
    auth_path, usage_path = source
    _user(auth_path, "alice")
    if failure == "missing_source":
        usage_path.unlink()
    elif failure == "missing_directory":
        auth_path.unlink()
    elif failure == "directory":
        def broken(**kwargs):
            raise sqlite3.OperationalError("private database path")
        monkeypatch.setattr(dashboard_api, "list_overview_entities", broken)
    else:
        async def invalid(**kwargs):
            return [{"mapping_username": "alice", "date": "2026-09-07" if failure == "invalid_date" else "2026-09-06",
                     **dict.fromkeys(dashboard_api.TOKEN_FIELDS, "invalid")}]
        monkeypatch.setattr(dashboard_api, "fetch_usage_daily", invalid)
    result = await dashboard_api.DashboardCache().usage()
    assert result["status"] == "unavailable"
    assert result["days"] is None
    assert "private" not in json.dumps(result)
    if failure == "missing_source":
        assert not usage_path.exists()
    elif failure == "missing_directory":
        assert not auth_path.exists()


@pytest.mark.asyncio
async def test_resource_adapters_sanitize_and_cache_independent_failures(monkeypatch):
    clock = [100.0]
    calls = []

    def manifest():
        calls.append(1)
        return {"assemblies": [{"sample": "a"}, {"sample": "b"}, None], "path": "private"}

    def broken():
        raise RuntimeError("private database path")

    monkeypatch.setattr(dashboard_api, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(dashboard_api.genome_browser, "load_manifest", manifest)
    monkeypatch.setattr(dashboard_api.gene_catalog, "load_catalog", broken)
    monkeypatch.setattr(dashboard_api.bulk_rnaseq_viewer, "load_status", lambda: {"counts": {"samples": 0}, "databasePath": "private"})
    cache = dashboard_api.DashboardCache()
    results = await asyncio.gather(*(cache.resources() for _ in range(10)))
    assert len(calls) == 1
    items = results[0]["items"]
    assert [item["count"] for item in items] == [2, None, 0]
    assert [item["status"] for item in items] == ["available", "unavailable", "available"]
    assert all(set(item) == {"id", "label", "href", "count", "status"} for item in items)
    assert "private" not in json.dumps(results)
    clock[0] += 299
    await cache.resources()
    assert len(calls) == 1
    clock[0] += 1
    monkeypatch.setattr(dashboard_api.gene_catalog, "load_catalog", lambda: {"counts": {"genes": 42}})
    assert (await cache.resources())["items"][1]["count"] == 42
    assert len(calls) == 2


def test_dashboard_routes_are_public_with_fixed_fields_and_no_filters(source, monkeypatch):
    from interface import app as app_module

    monkeypatch.setattr(dashboard_api, "_resource_counts", lambda: {"items": []})
    client = TestClient(app_module.app)
    try:
        assert client.get("/dashboard").status_code == 200
        assert client.get("/static/dashboard/app.js").status_code == 200
        assert client.get("/static/dashboard/potato-omics-updates.json").status_code == 200
        usage = client.get("/api/dashboard/usage")
        assert usage.status_code == 200
        assert usage.headers["cache-control"] == "no-store"
        assert set(usage.json()) == {"status", "time_zone", "start_date", "through_date", "days"}
        assert client.get("/api/dashboard/usage?username=private&start_at=0&window=7").json() == usage.json()
        assert client.get("/api/dashboard/resources").json() == {"items": []}
        assert client.get("/admin/api/overview").status_code == 401
        assert client.get("/admin/api/system").status_code == 401
    finally:
        client.close()


def test_internal_daily_requires_dedicated_auth_and_valid_window(source, monkeypatch):
    _, path = source
    token = "a" * 40
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "test")
    monkeypatch.setenv("POTATO_ADMIN_USAGE_TOKEN", token)
    monkeypatch.setenv("POTATO_MODEL_PROXY_USAGE_DB", str(path))
    monkeypatch.setattr(model_proxy.MappingStore, "load_targets", lambda _: [])
    monkeypatch.setattr(model_proxy, "_load_daily_updates_service_token", lambda: None)
    endpoint = "/internal/admin/usage/daily"
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(model_proxy.app, client=("127.0.0.1", 50000)) as client:
        assert client.get(endpoint, params={"start_at": 1, "end_at": 2}).status_code == 401
        assert client.get(endpoint, params={"start_at": 1, "end_at": 2}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        result = client.get(endpoint, params={"start_at": 1, "end_at": 2}, headers=headers)
        assert result.json() == {"time_zone": "Asia/Shanghai", "items": []}
        assert result.headers["cache-control"] == "no-store"
        for start, end in ((0, 2678401), (1, 1), (2, 1), ("nan", 2), (0, "inf")):
            assert client.get(endpoint, params={"start_at": start, "end_at": end}, headers=headers).status_code == 400
        path.unlink()
        missing = client.get(endpoint, params={"start_at": 1, "end_at": 2}, headers=headers)
        assert missing.status_code == 503
        assert str(path) not in missing.text
    with TestClient(model_proxy.app, client=("198.51.100.1", 50000)) as client:
        assert client.get(endpoint, params={"start_at": 1, "end_at": 2}, headers=headers).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"items": [{}]}, {"items": "invalid"}, [], None])
async def test_daily_client_validates_transport_and_auth(monkeypatch, payload):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(admin_usage_client, "_load_token", lambda: "test-token")
    monkeypatch.setattr(admin_usage_client.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    if payload == {"items": [{}]}:
        assert await admin_usage_client.fetch_usage_daily(start_at=1, end_at=2) == [{}]
    else:
        with pytest.raises(AdminUsageUnavailable):
            await admin_usage_client.fetch_usage_daily(start_at=1, end_at=2)
    assert requests[0].url.path == "/internal/admin/usage/daily"
    assert dict(requests[0].url.params) == {"start_at": "1", "end_at": "2"}
    assert requests[0].headers["authorization"] == "Bearer test-token"


def test_omics_updates_have_source_history_and_english_content():
    payload = json.loads((dashboard_api.STATIC_DIR / "potato-omics-updates.json").read_text())
    updates = payload["updates"]
    assert [entry["source_commits"] for entry in updates if entry["source_commits"]] == [["b132ae9"], ["e1f4d15"], ["4c6b802"], ["1e4af19"]]
    assert len({entry["version"] for entry in updates}) == len(updates)
    for entry in updates:
        assert all(key in entry for key in ("version", "title", "date", "summary", "items"))
        assert (entry["title"] + entry["summary"] + "".join(entry["items"])).isascii()

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from interface import model_proxy, token_usage_store


def _record(
    db_path,
    *,
    username: str,
    started_at: float,
    status_code: int = 200,
    usage_status: str = "present",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    token_usage_store.record_usage_request(
        mapping_username=username,
        endpoint="responses",
        route_model="main",
        upstream_model="model",
        provider="provider",
        status_code=status_code,
        streaming=True,
        started_at=started_at,
        completed_at=started_at + 1,
        duration_ms=1000,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        usage_status=usage_status,
        db_path=db_path,
    )


def test_admin_aggregate_uses_half_open_boundaries_and_usage_status(tmp_path) -> None:
    db_path = tmp_path / "usage.db"
    _record(
        db_path,
        username="alice",
        started_at=100,
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=30,
        cache_write_tokens=40,
    )
    _record(
        db_path,
        username="alice",
        started_at=150,
        usage_status="missing",
    )
    _record(db_path, username="alice", started_at=160, status_code=500, input_tokens=999)
    _record(db_path, username="alice", started_at=200, input_tokens=888)

    rows = token_usage_store.get_admin_usage_aggregate(
        start_at=100, end_at=200, db_path=db_path
    )

    assert rows == [
        {
            "mapping_username": "alice",
            "request_count": 2,
            "usage_request_count": 1,
            "missing_usage_request_count": 1,
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 30,
            "cache_write_tokens": 40,
            "total_tokens": 100,
        }
    ]


def test_principal_catalog_is_paginated_across_all_historical_records(tmp_path) -> None:
    db_path = tmp_path / "usage.db"
    _record(db_path, username="alice", started_at=20)
    _record(db_path, username="alice", started_at=10, status_code=500)
    _record(db_path, username="bob", started_at=30)

    first = token_usage_store.get_principal_catalog_page(
        page=1, page_size=1, db_path=db_path
    )
    second = token_usage_store.get_principal_catalog_page(
        page=2, page_size=1, db_path=db_path
    )
    assert first["total"] == 2
    assert first["items"] == [
        {
            "mapping_username": "alice",
            "first_used_at": 10.0,
            "last_used_at": 20.0,
            "request_count": 2,
        }
    ]
    assert second["items"][0]["mapping_username"] == "bob"


def test_internal_usage_endpoints_require_loopback_and_dedicated_credential(
    tmp_path, monkeypatch
) -> None:
    usage_db = tmp_path / "usage.db"
    token = "pmp_" + "a" * 40
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "test")
    monkeypatch.setenv("POTATO_ADMIN_USAGE_TOKEN", token)
    monkeypatch.setenv("POTATO_MODEL_PROXY_USAGE_DB", str(usage_db))
    monkeypatch.setattr(model_proxy.MappingStore, "load_targets", lambda _self: [])
    _record(usage_db, username="alice", started_at=100, input_tokens=5)

    with TestClient(
        model_proxy.app,
        client=("127.0.0.1", 50000),
    ) as client:
        missing = client.get(
            "/internal/admin/usage/aggregate?start_at=90&end_at=110"
        )
        assert missing.status_code == 401
        response = client.get(
            "/internal/admin/usage/aggregate?start_at=90&end_at=110",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["items"][0]["total_tokens"] == 5
        too_long = client.get(
            "/internal/admin/usage/aggregate?start_at=0&end_at=2678401",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert too_long.status_code == 400

        monkeypatch.setattr(
            model_proxy.MappingStore,
            "load_targets",
            lambda _self: [
                SimpleNamespace(username="alice", model_proxy_token=token)
            ],
        )
        collision = client.get(
            "/internal/admin/usage/principals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert collision.status_code == 503

    with TestClient(
        model_proxy.app,
        client=("198.51.100.10", 50000),
    ) as external_client:
        forbidden = external_client.get(
            "/internal/admin/usage/principals",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert forbidden.status_code == 403

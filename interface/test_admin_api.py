from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from interface import admin_api, admin_store, auth_db
from interface.admin_usage_client import AdminUsageUnavailable


def _build_client(tmp_path, monkeypatch) -> tuple[TestClient, object]:
    db_path = tmp_path / "interface.db"
    auth_db.ensure_auth_db(db_path)
    monkeypatch.setattr(admin_api, "ADMIN_AUTH_DB_PATH", db_path)
    monkeypatch.setattr(admin_api, "ADMIN_SESSION_SECRET", "admin-test-secret")
    monkeypatch.setattr(admin_api, "ADMIN_COOKIE_SECURE", False)
    app = FastAPI()
    app.include_router(admin_api.router)
    app.state.system_resource_sampler = SimpleNamespace(
        snapshot=lambda: {
            "status": "ok",
            "sampled_at": 100.0,
            "cpu_percent": 12.5,
            "memory": {"used_bytes": 1, "available_bytes": 2, "total_bytes": 3},
            "page_cache_bytes": 1,
            "swap_bytes": None,
            "load": {"one": 0.1, "five": 0.2, "fifteen": 0.3},
        }
    )
    return TestClient(app, client=("127.0.0.1", 50000)), db_path


def _create_user(db_path, username: str, *, admin: bool = False):
    user = auth_db.upsert_user(
        username=username,
        email=f"{username}@example.com",
        password="Password1!",
        mapping_username=username,
        name=username.title(),
        db_path=db_path,
    )
    return auth_db.set_user_role(username, "admin", db_path=db_path) if admin else user


def _signin(client: TestClient, login: str = "admin", password: str = "Password1!"):
    return client.post(
        "/admin/api/auth/signin",
        headers={"Origin": "http://testserver"},
        json={"login": login, "password": password},
    )


def test_admin_cookie_is_independent_strict_and_session_is_revocable(
    tmp_path, monkeypatch
) -> None:
    client, db_path = _build_client(tmp_path, monkeypatch)
    admin = _create_user(db_path, "admin", admin=True)
    response = _signin(client)
    assert response.status_code == 200, response.text
    set_cookie = response.headers["set-cookie"]
    assert "potato_admin_token=" in set_cookie
    assert "potato_interface_token=" not in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/admin" in set_cookie

    session = client.get("/admin/api/auth/session")
    assert session.json()["authenticated"] is True
    auth_db.update_user_password(admin.id, "NewPassword1!", db_path=db_path)
    revoked = client.get("/admin/api/auth/session")
    assert revoked.json() == {"authenticated": False}


def test_non_admin_temporary_and_unknown_logins_share_fuzzy_error(
    tmp_path, monkeypatch
) -> None:
    client, db_path = _build_client(tmp_path, monkeypatch)
    _create_user(db_path, "ordinary")
    temporary = auth_db.create_temporary_user(
        username="temp_1234567890_0123abcd",
        email="temp@temporary.example",
        password="Password1!",
        mapping_username="temp_1234567890_0123abcd",
        db_path=db_path,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("update users set role = 'admin' where id = ?", (temporary.id,))
        conn.commit()

    details = []
    for login in ("ordinary", temporary.username, "missing"):
        response = _signin(client, login=login)
        assert response.status_code == 401
        details.append(response.json()["detail"])
    assert details == ["Invalid credentials"] * 3


def test_admin_signin_locks_sixth_combination_attempt(tmp_path, monkeypatch) -> None:
    client, db_path = _build_client(tmp_path, monkeypatch)
    _create_user(db_path, "admin", admin=True)
    for _ in range(5):
        assert _signin(client, password="wrong").status_code == 401
    locked = _signin(client, password="wrong")
    assert locked.status_code == 429
    assert 1 <= int(locked.headers["retry-after"]) <= 900


def test_admin_post_rejects_cross_origin(tmp_path, monkeypatch) -> None:
    client, db_path = _build_client(tmp_path, monkeypatch)
    _create_user(db_path, "admin", admin=True)
    response = client.post(
        "/admin/api/auth/signin",
        headers={"Origin": "https://attacker.example"},
        json={"login": "admin", "password": "Password1!"},
    )
    assert response.status_code == 403


def _request(peer: str, forwarded: str = "") -> StarletteRequest:
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode("ascii")))
    return StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/admin",
            "raw_path": b"/admin",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 1234),
            "server": ("testserver", 80),
        }
    )


def test_forwarded_ip_is_used_only_through_trusted_proxy(monkeypatch) -> None:
    monkeypatch.setenv("INTERFACE_ADMIN_TRUSTED_PROXIES", "10.0.0.0/8")
    assert admin_api.trusted_client_ip(
        _request("198.51.100.1", "203.0.113.7")
    ) == "198.51.100.1"
    assert admin_api.trusted_client_ip(
        _request("10.0.0.2", "203.0.113.7, 10.0.0.3")
    ) == "203.0.113.7"
    assert admin_api.trusted_client_ip(
        _request("127.0.0.1", "203.0.113.8")
    ) == "203.0.113.8"


def test_overview_merges_retired_temp_and_hides_service_principal(
    tmp_path, monkeypatch
) -> None:
    client, db_path = _build_client(tmp_path, monkeypatch)
    _create_user(db_path, "admin", admin=True)
    _create_user(db_path, "alice")
    retired_name = "temp_1234567890_0123abcd"
    retired = auth_db.create_temporary_user(
        username=retired_name,
        email="retired@temporary.example",
        password="Password1!",
        mapping_username=retired_name,
        db_path=db_path,
    )
    auth_db.delete_user_by_id(retired.id, db_path=db_path)
    admin_store.reconcile_principal_catalog(
        [
            {
                "mapping_username": "daily-updates-service",
                "first_used_at": 1,
                "last_used_at": 2,
            }
        ],
        db_path=db_path,
    )

    async def usage(**_kwargs):
        return [
            {
                "mapping_username": "alice",
                "request_count": 1,
                "usage_request_count": 1,
                "missing_usage_request_count": 0,
                "input_tokens": 2,
                "output_tokens": 3,
                "cache_read_tokens": 4,
                "cache_write_tokens": 5,
                "total_tokens": 14,
            },
            {
                "mapping_username": retired_name,
                "request_count": 1,
                "usage_request_count": 0,
                "missing_usage_request_count": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "total_tokens": 0,
            },
            {
                "mapping_username": "daily-updates-service",
                "request_count": 99,
                "total_tokens": 999,
            },
        ]

    monkeypatch.setattr(admin_api, "fetch_usage_aggregate", usage)
    assert _signin(client).status_code == 200
    response = client.get("/admin/api/overview?window=24h&type=all&page=1")
    assert response.status_code == 200, response.text
    payload = response.json()
    by_mapping = {row["mapping_username"]: row for row in payload["users"]}
    assert set(by_mapping) == {"admin", "alice", retired_name}
    retired_row = by_mapping[retired_name]
    assert retired_row["lifecycle"] == "retired"
    assert retired_row["email"] is None
    assert retired_row["storage"] is None
    assert payload["totals"]["usage"]["total_tokens"] == 14


def test_overview_marks_usage_unavailable_without_zeroing_current_users(
    tmp_path, monkeypatch
) -> None:
    client, db_path = _build_client(tmp_path, monkeypatch)
    _create_user(db_path, "admin", admin=True)

    async def unavailable(**_kwargs):
        raise AdminUsageUnavailable("offline")

    monkeypatch.setattr(admin_api, "fetch_usage_aggregate", unavailable)
    assert _signin(client).status_code == 200
    payload = client.get("/admin/api/overview").json()
    assert payload["data_sources"]["usage"]["status"] == "unavailable"
    assert payload["totals"]["usage"]["total_tokens"] is None
    assert payload["users"][0]["usage"]["status"] == "unavailable"


def test_overview_defaults_to_type_groups_then_newest_registration_and_sorts_columns(
    tmp_path, monkeypatch
) -> None:
    client, db_path = _build_client(tmp_path, monkeypatch)
    _create_user(db_path, "admin", admin=True)
    _create_user(db_path, "alice")
    active_temp_name = "temp_1234567890_11111111"
    auth_db.create_temporary_user(
        username=active_temp_name,
        email="active@temporary.example",
        password="Password1!",
        mapping_username=active_temp_name,
        db_path=db_path,
    )
    retired_name = "temp_1234567890_22222222"
    retired = auth_db.create_temporary_user(
        username=retired_name,
        email="retired@temporary.example",
        password="Password1!",
        mapping_username=retired_name,
        db_path=db_path,
    )
    auth_db.delete_user_by_id(retired.id, db_path=db_path)
    with auth_db.connect_auth_db(db_path) as conn:
        conn.execute("update users set created_at = 100 where username = 'admin'")
        conn.execute("update users set created_at = 300 where username = 'alice'")
        conn.execute(
            "update users set created_at = 400 where username = ?",
            (active_temp_name,),
        )
        conn.execute(
            "update user_usage_identities set created_at = 500 "
            "where mapping_username = ?",
            (retired_name,),
        )
        conn.commit()

    totals = {
        "admin": 200,
        "alice": 400,
        active_temp_name: 300,
        retired_name: 100,
    }

    async def usage(**_kwargs):
        return [
            {
                "mapping_username": mapping_username,
                "request_count": index + 1,
                "usage_request_count": index + 1,
                "missing_usage_request_count": index,
                "input_tokens": total // 2,
                "output_tokens": total // 4,
                "cache_read_tokens": total // 4,
                "cache_write_tokens": 0,
                "total_tokens": total,
            }
            for index, (mapping_username, total) in enumerate(totals.items())
        ]

    monkeypatch.setattr(admin_api, "fetch_usage_aggregate", usage)
    assert _signin(client).status_code == 200

    default_payload = client.get("/admin/api/overview").json()
    assert [row["mapping_username"] for row in default_payload["users"]] == [
        "alice",
        "admin",
        active_temp_name,
        retired_name,
    ]
    assert default_payload["sorting"] == {"sort": "default", "direction": "desc"}
    assert all(row["created_at"] for row in default_payload["users"])

    newest = client.get(
        "/admin/api/overview?sort=created_at&direction=desc"
    ).json()
    assert [row["mapping_username"] for row in newest["users"]] == [
        retired_name,
        active_temp_name,
        "alice",
        "admin",
    ]

    monkeypatch.setattr(admin_api, "OVERVIEW_PAGE_SIZE", 2)
    token_desc_page_one = client.get(
        "/admin/api/overview?sort=total_tokens&direction=desc"
    ).json()
    token_desc_page_two = client.get(
        "/admin/api/overview?sort=total_tokens&direction=desc&page=2"
    ).json()
    assert [
        row["usage"]["total_tokens"] for row in token_desc_page_one["users"]
    ] == [
        400,
        300,
    ]
    assert [
        row["usage"]["total_tokens"] for row in token_desc_page_two["users"]
    ] == [
        200,
        100,
    ]

    for sort_by in admin_api.VALID_OVERVIEW_SORTS - {"default"}:
        assert client.get(
            f"/admin/api/overview?sort={sort_by}&direction=asc"
        ).status_code == 200
    assert client.get(
        "/admin/api/overview?sort=missing_usage_request_count"
    ).status_code == 400
    assert client.get("/admin/api/overview?sort=invalid").status_code == 400
    assert client.get("/admin/api/overview?direction=sideways").status_code == 400


def test_admin_table_has_separate_sortable_columns_without_type_column() -> None:
    html = (admin_api.ADMIN_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (admin_api.ADMIN_STATIC_DIR / "app.js").read_text(encoding="utf-8")
    stylesheet = (admin_api.ADMIN_STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "监控面板" in html
    assert "管理员检测面板" not in html
    assert "管理员观测" not in html
    assert html.count('class="sort-heading"') == 8
    assert "sort-button" not in html
    assert "data-direction" not in html
    for label in (
        "注册时间",
        "输入 Token",
        "输出 Token",
        "缓存读",
        "Token 合计",
        "请求",
        "目录空间",
    ):
        assert f">{label}</button>" in html
    assert "<th>类型</th>" not in html
    assert "Token 分类" not in html
    assert "Usage 缺失" not in html
    assert 'data-sort="missing_usage_request_count"' not in html
    assert 'sort: "default"' in javascript
    assert 'data-label="类型"' not in javascript
    assert 'data-label="Usage 缺失"' not in javascript
    for redundant_text in (
        "整机资源",
        "账号与消费",
        "Token 数据可用",
        "仅统计完整消费的成功代理响应，不作为账单数据。",
    ):
        assert redundant_text not in html
        assert redundant_text not in javascript
    assert "usage-source" not in html
    assert "usage-source" not in javascript
    assert "row.storage.sampled_at" not in javascript
    assert "storage-time" not in stylesheet
    assert "system-status" not in html
    assert "system-status" not in javascript
    assert "source-status" not in stylesheet
    assert "采样于" not in javascript
    assert "padding-left: 15px" in stylesheet
    assert "Linux 页缓存" not in html
    assert "page-cache" not in html
    assert "page-cache" not in javascript
    assert "cache-metric" not in stylesheet
    assert 'classList.toggle("with-swap", hasSwap)' in javascript
    assert "repeat(4, minmax(0, 1fr))" in stylesheet
    assert 'closest(".sort-heading[data-sort]")' in javascript
    assert 'state.direction === "asc" ? "desc" : "asc"' in javascript
    assert "border-bottom: 1px dashed" in stylesheet
    assert "text-align: center" in stylesheet
    assert "--filter-control-height: 42px" in stylesheet


def test_admin_page_response_is_no_store(monkeypatch) -> None:
    from interface import app as app_module

    marked = []
    monkeypatch.setattr(app_module, "mark_foreground_activity", lambda *_args: marked.append(1))
    client = TestClient(app_module.app)
    response = client.get("/admin")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert marked == []

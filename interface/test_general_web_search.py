from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from interface.test_model_proxy import (
    _client,
    _configure_daily_updates_credential,
)


USER_TOKEN = "pmp_alice_0123456789abcdefghijklmnopqrstuvwxyz"
TAVILY_FIXTURE_KEY = "tvly-fixture-never-log-this-value"
SERVICE_TOKEN = "svc_0123456789abcdefghijklmnopqrstuvwxyz"


def _headers() -> dict[str, str]:
    return {"authorization": f"Bearer {USER_TOKEN}"}


def _success_payload(*, url: str = "https://example.org/page") -> dict:
    return {
        "results": [
            {
                "title": "Example title",
                "url": url,
                "content": "Example snippet",
                "score": 0.81,
                "published_date": "2026-08-01",
                "raw_content": "must not escape",
            }
        ],
        "answer": "must not escape",
        "usage": {"credits": 1},
    }


def _search_client(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("TAVILY_API_KEY", TAVILY_FIXTURE_KEY)
    client, model_proxy = _client(tmp_path, monkeypatch)
    model_proxy.app.state.web_search_transport = httpx.MockTransport(handler)
    model_proxy.app.state.web_search_limiter = model_proxy.web_search.SearchRateLimiter()
    return client, model_proxy


def test_search_sends_only_fixed_validated_tavily_payload_and_cleans_response(
    tmp_path, monkeypatch
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_success_payload(),
            headers={"x-request-id": "tavily-request-1"},
        )

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    response = client.post(
        "/v1/search",
        headers=_headers(),
        json={
            "query": "  recent\n potato   news ",
            "max_results": 3,
            "topic": "news",
            "time_range": "week",
            "include_domains": ["Example.ORG."],
            "exclude_domains": ["blocked.example"],
        },
    )

    assert response.status_code == 200, response.text
    assert captured == {
        "url": "https://api.tavily.com/search",
        "authorization": f"Bearer {TAVILY_FIXTURE_KEY}",
        "body": {
            "query": "recent potato news",
            "search_depth": "basic",
            "max_results": 3,
            "topic": "news",
            "chunks_per_source": 2,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
            "include_usage": True,
            "time_range": "week",
            "include_domains": ["example.org"],
            "exclude_domains": ["blocked.example"],
        },
    }
    assert response.json() == {
        "success": True,
        "results": [
            {
                "title": "Example title",
                "url": "https://example.org/page",
                "content": "Example snippet",
                "score": 0.81,
                "published_date": "2026-08-01",
            }
        ],
        "meta": {
            "provider": "tavily",
            "topic": "news",
            "search_depth": "basic",
            "result_count": 1,
            "credits_used": 1,
        },
    }
    assert "raw_content" not in response.text
    assert "must not escape" not in response.text

    with sqlite3.connect(tmp_path / "usage.db") as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("select * from web_search_usage_requests").fetchone())
    assert row["mapping_username"] == "alice"
    assert row["topic"] == "news"
    assert row["status_code"] == 200
    assert row["result_count"] == 1
    assert row["credits_used"] == 1
    assert row["provider_request_id"] == "tavily-request-1"
    assert "query" not in row
    assert TAVILY_FIXTURE_KEY not in json.dumps(row)
    assert USER_TOKEN not in json.dumps(row)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": True},
        {"query": "x", "unknown": 1},
        {"query": "x", "max_results": True},
        {"query": "x", "max_results": "5"},
        {"query": "x", "topic": "science"},
        {"query": "x", "time_range": "hour"},
        {"query": "x", "include_domains": "example.org"},
        {"query": "x", "include_domains": ["https://example.org"]},
        {"query": "x", "include_domains": ["localhost"]},
        {"query": "x", "include_domains": ["127.0.0.1"]},
        {
            "query": "x",
            "include_domains": ["example.org"],
            "exclude_domains": ["EXAMPLE.ORG"],
        },
        {"query": "a\x00b"},
        {"query": "a\x01b"},
    ],
)
def test_search_rejects_invalid_requests_before_provider(
    tmp_path, monkeypatch, payload
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success_payload())

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    response = client.post("/v1/search", headers=_headers(), json=payload)

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "error": "Invalid web search request",
        "error_code": "invalid_request",
        "retryable": False,
    }
    assert calls == 0


def test_search_enforces_actual_body_limit_with_stable_error(
    tmp_path, monkeypatch
) -> None:
    client, _ = _search_client(
        tmp_path,
        monkeypatch,
        lambda _request: httpx.Response(200, json=_success_payload()),
    )
    response = client.post(
        "/v1/search",
        headers={**_headers(), "content-type": "application/json"},
        content=b"{" + b"x" * (16 * 1024),
    )
    assert response.status_code == 413
    assert response.json()["error_code"] == "request_too_large"


@pytest.mark.parametrize(
    "body",
    [
        b'{"query":"first","query":"second"}',
        b'{"query":"x","max_results":NaN}',
        b'{"query":"x","max_results":Infinity}',
    ],
)
def test_search_rejects_ambiguous_or_nonstandard_json(
    tmp_path, monkeypatch, body
) -> None:
    client, _ = _search_client(
        tmp_path,
        monkeypatch,
        lambda _request: httpx.Response(200, json=_success_payload()),
    )
    response = client.post(
        "/v1/search",
        headers={**_headers(), "content-type": "application/json"},
        content=body,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"


def test_search_authenticates_and_rejects_service_principal_before_provider(
    tmp_path, monkeypatch
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success_payload())

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    assert client.post("/v1/search", json={"query": "x"}).json()[
        "error_code"
    ] == "unauthorized"
    _configure_daily_updates_credential(tmp_path, monkeypatch, SERVICE_TOKEN)
    service_response = client.post(
        "/v1/search",
        headers={"authorization": f"Bearer {SERVICE_TOKEN}"},
        json={"query": "x"},
    )
    assert service_response.status_code == 403
    assert service_response.json()["error_code"] == "search_forbidden"
    assert calls == 0


@pytest.mark.parametrize(
    ("provider_status", "proxy_status", "error_code"),
    [
        (401, 503, "provider_auth"),
        (429, 429, "provider_rate_limited"),
        (432, 503, "provider_budget_exhausted"),
        (433, 503, "provider_budget_exhausted"),
        (302, 502, "provider_error"),
        (500, 502, "provider_error"),
    ],
)
def test_search_maps_provider_status_without_returning_body(
    tmp_path, monkeypatch, provider_status, proxy_status, error_code
) -> None:
    sentinel = "provider-secret-body"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            provider_status,
            text=sentinel,
            headers={"retry-after": "999999"},
        )

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    response = client.post(
        "/v1/search", headers=_headers(), json={"query": "x"}
    )
    assert response.status_code == proxy_status
    assert response.json()["error_code"] == error_code
    assert sentinel not in response.text
    if provider_status == 429:
        assert response.json()["retry_after_seconds"] == 60


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/page",
        "http://127.0.0.1/page",
        "http://169.254.169.254/latest",
        "https://user:password@example.org/page",
        "https://example.org/\x01bad",
        "https://bad_label.example/page",
        "https://service.internal/page",
    ],
)
def test_search_rejects_unsafe_provider_urls(tmp_path, monkeypatch, url) -> None:
    client, _ = _search_client(
        tmp_path,
        monkeypatch,
        lambda _request: httpx.Response(200, json=_success_payload(url=url)),
    )
    response = client.post(
        "/v1/search", headers=_headers(), json={"query": "x"}
    )
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"


def test_search_accepts_public_ipv6_result_url(tmp_path, monkeypatch) -> None:
    url = "https://[2606:4700:4700::1111]/page"
    client, _ = _search_client(
        tmp_path,
        monkeypatch,
        lambda _request: httpx.Response(200, json=_success_payload(url=url)),
    )
    response = client.post(
        "/v1/search", headers=_headers(), json={"query": "x"}
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["url"] == url


def test_search_rejects_invalid_json_nan_and_oversized_provider_body(
    tmp_path, monkeypatch
) -> None:
    responses = iter(
        [
            httpx.Response(200, content=b"not-json"),
            httpx.Response(
                200,
                content=b'{"results":[{"title":"x","url":"https://example.org","score":NaN}]}',
            ),
            httpx.Response(200, content=b"x" * (512 * 1024 + 1)),
        ]
    )
    client, _ = _search_client(
        tmp_path, monkeypatch, lambda _request: next(responses)
    )
    for _ in range(3):
        response = client.post(
            "/v1/search", headers=_headers(), json={"query": "x"}
        )
        assert response.status_code == 502
        assert response.json()["error_code"] == "provider_error"


def test_search_rejects_provider_result_count_above_requested_limit(
    tmp_path, monkeypatch
) -> None:
    payload = _success_payload()
    payload["results"] = payload["results"] * 2
    client, _ = _search_client(
        tmp_path,
        monkeypatch,
        lambda _request: httpx.Response(200, json=payload),
    )
    response = client.post(
        "/v1/search",
        headers=_headers(),
        json={"query": "x", "max_results": 1},
    )
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"


def test_search_rejects_oversized_declared_provider_body_before_read(
    tmp_path, monkeypatch
) -> None:
    client, _ = _search_client(
        tmp_path,
        monkeypatch,
        lambda _request: httpx.Response(
            200,
            content=b"{}",
            headers={"content-length": str(512 * 1024 + 1)},
        ),
    )
    response = client.post(
        "/v1/search", headers=_headers(), json={"query": "x"}
    )
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"


def test_tavily_systemd_drop_in_uses_credential_without_literal_secret() -> None:
    drop_in = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "packaging"
        / "systemd"
        / "potato-model-proxy-tavily.conf"
    ).read_text(encoding="utf-8")
    assert drop_in == (
        "[Service]\n"
        "LoadCredential=tavily-api-key:"
        "/etc/potato-agent/credentials/tavily-api-key\n"
    )
    assert "Environment=TAVILY_API_KEY" not in drop_in


def test_search_missing_secret_and_production_literal_secret_are_safe(
    tmp_path, monkeypatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    missing = client.post(
        "/v1/search", headers=_headers(), json={"query": "private query"}
    )
    assert missing.status_code == 503
    assert missing.json()["error_code"] == "search_unavailable"
    assert "private query" not in missing.text

    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("TAVILY_API_KEY", TAVILY_FIXTURE_KEY)
    rejected = client.post(
        "/v1/search", headers=_headers(), json={"query": "x"}
    )
    assert rejected.status_code == 503
    assert TAVILY_FIXTURE_KEY not in rejected.text


@pytest.mark.asyncio
async def test_search_limiter_enforces_rate_and_concurrency_and_releases() -> None:
    from interface.web_search import SearchFailure, SearchRateLimiter

    limiter = SearchRateLimiter(
        per_user_requests=2, global_requests=3, window_seconds=60, concurrency=1
    )
    lease = await limiter.acquire("alice")
    with pytest.raises(SearchFailure) as concurrent:
        await limiter.acquire("bob")
    assert concurrent.value.error_code == "rate_limited"
    await lease.release()
    second = await limiter.acquire("alice")
    await second.release()
    with pytest.raises(SearchFailure):
        await limiter.acquire("alice")


def test_search_truncates_fields_and_drops_tail_to_stay_under_response_limit(
    tmp_path, monkeypatch
) -> None:
    item = {
        "title": "t" * 2000,
        "url": "https://example.org/page",
        "content": "内" * 9000,
        "score": 1,
        "published_date": "d" * 200,
    }
    payload = {"results": [item] * 10, "usage": {"credits": 1}}
    client, _ = _search_client(
        tmp_path,
        monkeypatch,
        lambda _request: httpx.Response(200, json=payload),
    )
    response = client.post(
        "/v1/search", headers=_headers(), json={"query": "x", "max_results": 10}
    )
    assert response.status_code == 200
    assert len(response.content) <= 64 * 1024
    body = response.json()
    assert 1 <= body["meta"]["result_count"] < 10
    assert len(body["results"][0]["title"]) == 1000
    assert len(body["results"][0]["content"]) == 8000
    assert len(body["results"][0]["published_date"]) == 128

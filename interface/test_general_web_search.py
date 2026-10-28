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
DEEPSEEK_FIXTURE_KEY = "sk-fixture-never-log-this-value"
SERVICE_TOKEN = "svc_0123456789abcdefghijklmnopqrstuvwxyz"


def _headers() -> dict[str, str]:
    return {"authorization": f"Bearer {USER_TOKEN}"}


def _success_payload(*, url: str = "https://example.org/page") -> dict:
    return {
        "results": [
            {
                "title": "Example title",
                "url": url,
                "summary": "Example summary",
                "snippet": "Example snippet",
                "content": "# Example\n\nFull page.",
                "private_debug": "must not escape",
                "rerank_score": 2.4,
                "date": "2026-08-01T00:00:00+00:00",
                "site_name": "Example",
                "site_icon": "https://example.org/favicon.ico",
                "authority": 3,
            }
        ],
        "answer": "must not escape",
        "usage": {"credits": "must not be used"},
    }


def _search_client(tmp_path, monkeypatch, handler):
    monkeypatch.delenv("DEEPSEEK_SEARCH_API_KEY_FILE", raising=False)
    monkeypatch.setenv("DEEPSEEK_SEARCH_API_KEY", DEEPSEEK_FIXTURE_KEY)
    client, model_proxy = _client(tmp_path, monkeypatch)
    model_proxy.app.state.web_search_transport = httpx.MockTransport(handler)
    model_proxy.app.state.web_search_limiter = model_proxy.web_search.SearchRateLimiter()
    return client, model_proxy


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ConnectError])
def test_search_recovers_after_two_connection_failures(
    tmp_path, monkeypatch, failure
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) <= 2:
            raise failure("private connection details", request=request)
        return httpx.Response(200, json=_success_payload())

    client, model_proxy = _search_client(tmp_path, monkeypatch, handler)
    monkeypatch.setattr(model_proxy.web_search, "SEARCH_CONNECT_RETRY_DELAY_SECONDS", 0)

    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})

    assert response.status_code == 200
    assert len(calls) == 3
    assert all(request.extensions["timeout"]["connect"] == 10 for request in calls)
    assert model_proxy.app.state.web_search_limiter._active == 0
    with sqlite3.connect(tmp_path / "usage.db") as conn:
        rows = conn.execute(
            "select status_code, result_count from web_search_usage_requests"
        ).fetchall()
    assert rows == [(200, 1)]


@pytest.mark.parametrize(
    ("failure", "status", "error_code"),
    [
        (httpx.ConnectTimeout, 504, "provider_timeout"),
        (httpx.ConnectError, 502, "provider_error"),
    ],
)
def test_search_stops_after_two_connection_retries_and_logs_safely(
    tmp_path, monkeypatch, caplog, failure, status, error_code
) -> None:
    calls = 0
    query = "private query sentinel"
    details = f"private connection details {DEEPSEEK_FIXTURE_KEY} {query}"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise failure(details, request=request)

    client, model_proxy = _search_client(tmp_path, monkeypatch, handler)
    monkeypatch.setattr(model_proxy.web_search, "SEARCH_CONNECT_RETRY_DELAY_SECONDS", 0)

    response = client.post("/v1/search", headers=_headers(), json={"query": query})

    assert response.status_code == status
    assert response.json()["error_code"] == error_code
    assert calls == 3
    assert model_proxy.app.state.web_search_limiter._active == 0
    assert f"type={failure.__name__}" in caplog.text
    assert "duration_ms=" in caplog.text
    for secret in (query, DEEPSEEK_FIXTURE_KEY, USER_TOKEN, details):
        assert secret not in caplog.text
        assert secret not in response.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError],
)
def test_search_does_not_retry_errors_after_connecting(
    tmp_path, monkeypatch, failure
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise failure("private request details", request=request)

    client, _ = _search_client(tmp_path, monkeypatch, handler)

    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})

    assert response.status_code in {502, 504}
    assert calls == 1
    assert "private request details" not in response.text


def test_search_body_timeout_closes_response_without_retrying(
    tmp_path, monkeypatch
) -> None:
    class SlowBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'{"results":'
            raise httpx.ReadTimeout("private body details")

        async def aclose(self):
            self.closed = True

    body = SlowBody()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=body)

    client, model_proxy = _search_client(tmp_path, monkeypatch, handler)

    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})

    assert response.status_code == 504
    assert response.json()["error_code"] == "provider_timeout"
    assert calls == 1
    assert body.closed
    assert model_proxy.app.state.web_search_limiter._active == 0


def test_search_total_deadline_includes_connection_retry_backoff(
    tmp_path, monkeypatch, caplog
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("private connection details", request=request)

    client, model_proxy = _search_client(tmp_path, monkeypatch, handler)
    monkeypatch.setattr(model_proxy.web_search, "SEARCH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(model_proxy.web_search, "SEARCH_CONNECT_RETRY_DELAY_SECONDS", 1)

    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})

    assert response.status_code == 504
    assert response.json()["error_code"] == "provider_timeout"
    assert calls == 1
    assert "type=TimeoutError" in caplog.text
    assert model_proxy.app.state.web_search_limiter._active == 0
    with sqlite3.connect(tmp_path / "usage.db") as conn:
        rows = conn.execute(
            "select status_code, error_code from web_search_usage_requests"
        ).fetchall()
    assert rows == [(504, "provider_timeout")]


def test_search_sends_native_deepseek_payload_and_returns_native_results(
    tmp_path, monkeypatch
) -> None:
    captured = {}
    payload = {
        "query": '  potato "late  blight" site:example.org after:2026-10-01',
        "count": 3,
        "ori_question": "Which potato genes confer late blight resistance?",
        "localization": "8.8.8.8",
        "need_content": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(url=str(request.url), authorization=request.headers["authorization"],
                        body=json.loads(request.content))
        return httpx.Response(200, json=_success_payload(),
                              headers={"x-request-id": "deepseek-request-1"})

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    response = client.post("/v1/search", headers=_headers(), json=payload)
    assert response.status_code == 200
    assert captured == {
        "url": "https://api.deepseek.com/v1/tools/search",
        "authorization": f"Bearer {DEEPSEEK_FIXTURE_KEY}",
        "body": payload,
    }
    assert response.json() == {"results": [{
        "title": "Example title", "url": "https://example.org/page",
        "snippet": "Example snippet", "summary": "Example summary",
        "content": "# Example\n\nFull page.", "rerank_score": 2.4,
        "site_name": "Example", "site_icon": "https://example.org/favicon.ico",
        "date": "2026-08-01T00:00:00+00:00", "authority": 3,
    }]}
    assert "must not escape" not in response.text
    with sqlite3.connect(tmp_path / "usage.db") as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("select * from web_search_usage_requests").fetchone())
    assert row["mapping_username"] == "alice"
    assert row["status_code"] == 200
    assert row["result_count"] == 1
    assert row["provider_request_id"] == "deepseek-request-1"
    assert not {"query", "ori_question", "localization"}.intersection(row)
    for value in [DEEPSEEK_FIXTURE_KEY, USER_TOKEN, payload["query"], payload["ori_question"]]:
        assert value not in json.dumps(row)


@pytest.mark.parametrize(
    "payload",
    [
        {}, {"query": True}, {"query": "   "}, {"query": "x", "unknown": 1},
        *({"query": "x", "count": count} for count in [True, "5", None, 0, 51]),
        *({"query": "x", "need_content": value} for value in [None, 1, "true"]),
        *({"query": "x", "ori_question": value} for value in [True, "", " " * 5, "x" * 129]),
        *({"query": "x", "localization": value} for value in [None, True, "example.org", "8.8.8.8/32", "fe80::1%eth0"]),
        *({"query": "x", field: value} for field, value in [
            ("max_results", 5), ("topic", "general"), ("time_range", "week"),
            ("include_domains", ["example.org"]), ("exclude_domains", []),
        ]),
        {"query": "a\x00b"}, {"query": "a\x01b"},
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
        b'{"query":"x","count":NaN}',
        b'{"query":"x","count":Infinity}',
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
        (402, 503, "provider_budget_exhausted"),
        (400, 502, "provider_rejected"),
        (403, 502, "provider_rejected"),
        (422, 502, "provider_rejected"),
        (408, 504, "provider_timeout"),
        (504, 504, "provider_timeout"),
        (302, 502, "provider_error"),
        (500, 502, "provider_error"),
    ],
)
def test_search_maps_provider_status_without_returning_body(
    tmp_path, monkeypatch, provider_status, proxy_status, error_code
) -> None:
    sentinel = "provider-secret-body"
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
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
    assert calls == 1
    assert response.json()["retryable"] == (provider_status in {302, 408, 429, 500, 504})
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
                content=b'{"results":[{"title":"x","url":"https://example.org","rerank_score":NaN}]}',
            ),
            httpx.Response(200, content=b"x" * (8 * 1024 * 1024 + 1)),
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
        json={"query": "x", "count": 1},
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
            headers={"content-length": str(8 * 1024 * 1024 + 1)},
        ),
    )
    response = client.post(
        "/v1/search", headers=_headers(), json={"query": "x"}
    )
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"


def test_deepseek_systemd_drop_in_uses_credential_without_literal_secret() -> None:
    drop_in = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "packaging"
        / "systemd"
        / "potato-model-proxy-deepseek-search.conf"
    ).read_text(encoding="utf-8")
    assert drop_in == (
        "[Service]\n"
        "LoadCredential=deepseek-search-api-key:"
        "/etc/potato-agent/credentials/deepseek-search-api-key\n"
    )
    assert "Environment=DEEPSEEK_SEARCH_API_KEY" not in drop_in


def test_search_missing_secret_and_production_literal_secret_are_safe(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_SEARCH_API_KEY_FILE", raising=False)
    client, _ = _client(tmp_path, monkeypatch)
    missing = client.post(
        "/v1/search", headers=_headers(), json={"query": "private query"}
    )
    assert missing.status_code == 503
    assert missing.json()["error_code"] == "search_unavailable"
    assert "private query" not in missing.text

    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    monkeypatch.setenv("DEEPSEEK_SEARCH_API_KEY", DEEPSEEK_FIXTURE_KEY)
    rejected = client.post(
        "/v1/search", headers=_headers(), json={"query": "x"}
    )
    assert rejected.status_code == 503
    assert DEEPSEEK_FIXTURE_KEY not in rejected.text


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


def test_search_rejects_oversized_output_without_returning_partial_results(
    tmp_path, monkeypatch
) -> None:
    payload = _success_payload()
    payload["results"][0]["content"] = "x" * (4 * 1024 * 1024)
    client, _ = _search_client(
        tmp_path, monkeypatch, lambda _: httpx.Response(200, json=payload)
    )
    response = client.post("/v1/search", headers=_headers(), json={
        "query": "x", "need_content": True,
    })
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"


@pytest.mark.parametrize("query", ["马" * 128, "🥔" * 128, 'potato "late blight" lang:en'])
def test_search_preserves_query_and_unicode_character_limit(tmp_path, monkeypatch, query):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    response = client.post("/v1/search", headers=_headers(), json={"query": query})
    assert response.status_code == 200
    assert bodies == [{"query": query, "count": 10, "need_content": False}]
    assert response.json()["results"] == []
    assert response.json() == {"results": []}


@pytest.mark.parametrize("query", ["马" * 129, " " + "x" * 128])
def test_search_rejects_overlong_query_before_credentials_and_limiter(
    tmp_path, monkeypatch, query
) -> None:
    def handler(_request):
        pytest.fail("Invalid query reached the provider")

    client, model_proxy = _search_client(tmp_path, monkeypatch, handler)
    monkeypatch.delenv("DEEPSEEK_SEARCH_API_KEY")
    response = client.post("/v1/search", headers=_headers(), json={"query": query})
    assert response.status_code == 400
    assert response.json()["error_code"] == "query_too_long"
    assert response.json()["retryable"] is False
    assert not model_proxy.app.state.web_search_limiter._global


@pytest.mark.parametrize("count", [1, 50])
def test_search_supports_native_count_range_and_default_empty_content(
    tmp_path, monkeypatch, count
) -> None:
    payload = _success_payload()
    payload["results"] *= count
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    response = client.post("/v1/search", headers=_headers(), json={"query": "x", "count": count})
    assert response.status_code == 200
    assert len(response.json()["results"]) == count
    assert all(item["content"] == "" for item in response.json()["results"])
    assert bodies == [{"query": "x", "count": count, "need_content": False}]


@pytest.mark.parametrize("localization", ["", "2001:4860:4860::8888"])
def test_search_accepts_optional_native_parameters(tmp_path, monkeypatch, localization):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    response = client.post("/v1/search", headers=_headers(), json={
        "query": "x", "ori_question": None, "localization": localization,
    })
    assert response.status_code == 200
    expected = {"query": "x", "count": 10, "need_content": False}
    if localization:
        expected["localization"] = localization
    assert bodies == [expected]


def test_search_keeps_empty_summary_and_nullable_result_fields(tmp_path, monkeypatch):
    payload = _success_payload()
    item = payload["results"][0]
    item.update(summary="", site_name=None, site_icon=None, date=None, authority=-1)
    client, _ = _search_client(tmp_path, monkeypatch, lambda _: httpx.Response(200, json=payload))
    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["summary"] == ""
    assert result["snippet"] == "Example snippet"
    assert result["site_name"] is result["site_icon"] is result["date"] is None
    assert result["authority"] == -1


@pytest.mark.parametrize(("field", "value"), [
    ("authority", True), ("authority", -2), ("authority", 5), ("authority", 1.5),
    ("summary", None), ("snippet", []), ("site_icon", "file:///etc/passwd"),
])
def test_search_rejects_malformed_native_result_fields(tmp_path, monkeypatch, field, value):
    payload = _success_payload()
    payload["results"][0][field] = value
    client, _ = _search_client(tmp_path, monkeypatch, lambda _: httpx.Response(200, json=payload))
    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"


@pytest.mark.parametrize("score", [-0.01, 3.01, True, "2.4", 10**400])
def test_search_rejects_invalid_deepseek_scores(tmp_path, monkeypatch, score):
    payload = _success_payload()
    payload["results"][0]["rerank_score"] = score
    client, _ = _search_client(
        tmp_path, monkeypatch, lambda _: httpx.Response(200, json=payload)
    )
    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_error"


@pytest.mark.parametrize("source", ["file", "credential"])
def test_search_loads_private_deepseek_key_in_production(tmp_path, monkeypatch, source):
    def handler(request):
        assert request.headers["authorization"] == f"Bearer {DEEPSEEK_FIXTURE_KEY}"
        return httpx.Response(200, json=_success_payload())

    client, _ = _search_client(tmp_path, monkeypatch, handler)
    monkeypatch.delenv("DEEPSEEK_SEARCH_API_KEY")
    monkeypatch.setenv("INTERFACE_ENVIRONMENT", "production")
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir(mode=0o700)
    secret = credential_dir / "deepseek-search-api-key"
    secret.write_text(DEEPSEEK_FIXTURE_KEY + "\n")
    secret.chmod(0o600)
    if source == "file":
        monkeypatch.setenv("DEEPSEEK_SEARCH_API_KEY_FILE", str(secret))
    else:
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
    response = client.post("/v1/search", headers=_headers(), json={"query": "x"})
    assert response.status_code == 200
    assert DEEPSEEK_FIXTURE_KEY not in response.text

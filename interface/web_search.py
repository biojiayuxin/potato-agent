from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import time
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import Response

from interface import token_usage_store
from interface.secret_config import SecretConfigurationError, load_secret


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_API_KEY_ENVIRONMENT = "TAVILY_API_KEY"
TAVILY_API_KEY_CREDENTIAL = "tavily-api-key"
SEARCH_REQUEST_LIMIT_BYTES = 16 * 1024
TAVILY_RESPONSE_LIMIT_BYTES = 512 * 1024
SEARCH_RESPONSE_LIMIT_BYTES = 64 * 1024
SEARCH_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 3600
TITLE_LIMIT = 1000
URL_LIMIT = 4096
CONTENT_LIMIT = 8000
DATE_LIMIT = 128
PROVIDER_REQUEST_ID_LIMIT = 128
VALID_TOPICS = frozenset({"general", "news", "finance"})
VALID_TIME_RANGES = frozenset({"day", "week", "month", "year"})
ALLOWED_REQUEST_FIELDS = frozenset(
    {
        "query",
        "max_results",
        "topic",
        "time_range",
        "include_domains",
        "exclude_domains",
    }
)
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    max_results: int
    topic: str
    time_range: str | None
    include_domains: tuple[str, ...]
    exclude_domains: tuple[str, ...]


@dataclass
class SearchFailure(Exception):
    status_code: int
    error_code: str
    message: str
    retryable: bool
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class SearchOutcome:
    content: dict[str, Any]
    result_count: int
    credits_used: float | int | None
    provider_request_id: str | None


@dataclass(frozen=True)
class _LimiterLease:
    limiter: "SearchRateLimiter"

    async def release(self) -> None:
        await self.limiter.release()


class SearchRateLimiter:
    """Single-process rolling limiter.

    The production model proxy currently has one worker. This must move to a
    shared SQLite/Redis limiter before deploying multiple workers or replicas.
    """

    def __init__(
        self,
        *,
        per_user_requests: int = 20,
        global_requests: int = 60,
        window_seconds: float = 60.0,
        concurrency: int = 8,
    ) -> None:
        self.per_user_requests = per_user_requests
        self.global_requests = global_requests
        self.window_seconds = window_seconds
        self.concurrency = concurrency
        self._lock = asyncio.Lock()
        self._per_user: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
        self._active = 0

    @staticmethod
    def _prune(values: deque[float], cutoff: float) -> None:
        while values and values[0] <= cutoff:
            values.popleft()

    @staticmethod
    def _retry_after(values: deque[float], now: float, window: float) -> int:
        if not values:
            return 1
        return max(1, min(60, math.ceil(values[0] + window - now)))

    async def acquire(self, username: str) -> _LimiterLease:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            user_values = self._per_user[username]
            self._prune(user_values, cutoff)
            self._prune(self._global, cutoff)
            if len(user_values) >= self.per_user_requests:
                raise SearchFailure(
                    429,
                    "rate_limited",
                    "Web search is temporarily rate limited",
                    True,
                    self._retry_after(user_values, now, self.window_seconds),
                )
            if len(self._global) >= self.global_requests:
                raise SearchFailure(
                    429,
                    "rate_limited",
                    "Web search is temporarily rate limited",
                    True,
                    self._retry_after(self._global, now, self.window_seconds),
                )
            if self._active >= self.concurrency:
                raise SearchFailure(
                    429,
                    "rate_limited",
                    "Web search is temporarily rate limited",
                    True,
                    1,
                )
            user_values.append(now)
            self._global.append(now)
            self._active += 1
        return _LimiterLease(self)

    async def release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)


DEFAULT_SEARCH_LIMITER = SearchRateLimiter()


def error_payload(failure: SearchFailure) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error": failure.message,
        "error_code": failure.error_code,
        "retryable": failure.retryable,
    }
    if failure.retry_after_seconds is not None:
        payload["retry_after_seconds"] = failure.retry_after_seconds
    return payload


def error_response(failure: SearchFailure) -> Response:
    return _json_response(error_payload(failure), status_code=failure.status_code)


def request_limit_error(status_code: int) -> dict[str, Any]:
    if status_code == 413:
        return error_payload(
            SearchFailure(
                413,
                "request_too_large",
                "Web search request is too large",
                False,
            )
        )
    return error_payload(
        SearchFailure(400, "invalid_request", "Invalid web search request", False)
    )


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_response(value: Any, *, status_code: int) -> Response:
    body = _json_bytes(value)
    return Response(
        body,
        status_code=status_code,
        media_type="application/json",
        headers={"content-length": str(len(body))},
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _strict_json_loads(data: str) -> Any:
    return json.loads(
        data,
        object_pairs_hook=_unique_json_object,
        parse_constant=_invalid_json_constant,
    )


def _invalid_request() -> SearchFailure:
    return SearchFailure(400, "invalid_request", "Invalid web search request", False)


def _normalize_query(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid_request()
    if "\x00" in value:
        raise _invalid_request()
    for character in value:
        if unicodedata.category(character) == "Cc" and character not in "\t\r\n":
            raise _invalid_request()
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 2000:
        raise _invalid_request()
    return normalized


def _strict_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_request()
    if not minimum <= value <= maximum:
        raise _invalid_request()
    return value


def _hostname(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise _invalid_request()
    if value != value.strip() or any(
        unicodedata.category(character) == "Cc" for character in value
    ):
        raise _invalid_request()
    if any(character in value for character in "/:@?#*[]"):
        raise _invalid_request()
    candidate = value[:-1] if value.endswith(".") else value
    try:
        normalized = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise _invalid_request() from exc
    if len(normalized) > 253 or "." not in normalized or normalized == "localhost":
        raise _invalid_request()
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise _invalid_request()
    labels = normalized.split(".")
    if any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise _invalid_request()
    return normalized


def _domain_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _invalid_request()
    normalized = tuple(_hostname(item) for item in value)
    if len(set(normalized)) != len(normalized):
        raise _invalid_request()
    return normalized


def parse_search_request(body: bytes, content_type: str | None) -> SearchRequest:
    if len(body) > SEARCH_REQUEST_LIMIT_BYTES:
        raise SearchFailure(
            413, "request_too_large", "Web search request is too large", False
        )
    media_type = (content_type or "").partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise _invalid_request()
    try:
        payload = _strict_json_loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _invalid_request() from exc
    if not isinstance(payload, dict) or set(payload) - ALLOWED_REQUEST_FIELDS:
        raise _invalid_request()
    query = _normalize_query(payload.get("query"))
    max_results = _strict_int(
        payload.get("max_results"), default=5, minimum=1, maximum=10
    )
    topic = payload.get("topic", "general")
    if not isinstance(topic, str) or topic not in VALID_TOPICS:
        raise _invalid_request()
    time_range = payload.get("time_range")
    if time_range is not None and (
        not isinstance(time_range, str) or time_range not in VALID_TIME_RANGES
    ):
        raise _invalid_request()
    include_domains = _domain_list(payload.get("include_domains"))
    exclude_domains = _domain_list(payload.get("exclude_domains"))
    if len(include_domains) + len(exclude_domains) > 20:
        raise _invalid_request()
    if set(include_domains) & set(exclude_domains):
        raise _invalid_request()
    return SearchRequest(
        query=query,
        max_results=max_results,
        topic=topic,
        time_range=time_range,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )


def _provider_payload(search: SearchRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": search.query,
        "search_depth": "basic",
        "max_results": search.max_results,
        "topic": search.topic,
        "chunks_per_source": 2,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
        "include_usage": True,
    }
    if search.time_range is not None:
        payload["time_range"] = search.time_range
    if search.include_domains:
        payload["include_domains"] = list(search.include_domains)
    if search.exclude_domains:
        payload["exclude_domains"] = list(search.exclude_domains)
    return payload


def _retry_after(value: str | None) -> int:
    if value:
        stripped = value.strip()
        if stripped.isdecimal():
            parsed = int(stripped)
            if 1 <= parsed <= MAX_RETRY_AFTER_SECONDS:
                return parsed
        else:
            try:
                date_value = parsedate_to_datetime(stripped)
                seconds = math.ceil(date_value.timestamp() - time.time())
                if 1 <= seconds <= MAX_RETRY_AFTER_SECONDS:
                    return seconds
            except (TypeError, ValueError, OverflowError):
                pass
    return DEFAULT_RETRY_AFTER_SECONDS


def _safe_request_id(value: str | None) -> str | None:
    if not value or len(value) > PROVIDER_REQUEST_ID_LIMIT:
        return None
    if any(unicodedata.category(character) == "Cc" for character in value):
        return None
    return value


def _provider_status_failure(response: httpx.Response) -> SearchFailure:
    status = response.status_code
    if status == 401:
        return SearchFailure(
            503, "provider_auth", "Web search provider authentication failed", False
        )
    if status == 429:
        return SearchFailure(
            429,
            "provider_rate_limited",
            "Web search is temporarily rate limited",
            True,
            _retry_after(response.headers.get("retry-after")),
        )
    if status in {432, 433}:
        return SearchFailure(
            503,
            "provider_budget_exhausted",
            "Web search provider budget is exhausted",
            False,
        )
    return SearchFailure(
        502, "provider_error", "Web search provider returned an invalid response", True
    )


def _provider_error() -> SearchFailure:
    return SearchFailure(
        502, "provider_error", "Web search provider returned an invalid response", True
    )


def _public_result_url(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > URL_LIMIT:
        raise _provider_error()
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise _provider_error()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _provider_error() from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None and not 1 <= port <= 65535
    ):
        raise _provider_error()
    hostname = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            normalized_hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise _provider_error() from exc
        if (
            normalized_hostname == "localhost"
            or "." not in normalized_hostname
            or normalized_hostname.endswith(
                (".localhost", ".local", ".internal", ".home", ".lan")
            )
            or any(
                not _HOST_LABEL_RE.fullmatch(label)
                for label in normalized_hostname.split(".")
            )
        ):
            raise _provider_error()
        return value
    if not address.is_global:
        raise _provider_error()
    return value


def _safe_optional_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _provider_error()
    cleaned = "".join(
        " " if unicodedata.category(character) == "Cc" else character
        for character in value
    )
    return cleaned[:limit]


def _safe_score(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _provider_error()
    if not math.isfinite(float(value)) or not 0 <= value <= 1:
        raise _provider_error()
    return value


def _credits_used(payload: dict[str, Any]) -> float | int | None:
    usage = payload.get("usage")
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise _provider_error()
    credits = usage.get("credits")
    if credits is None:
        return None
    if (
        isinstance(credits, bool)
        or not isinstance(credits, (int, float))
        or not math.isfinite(float(credits))
        or not 0 <= credits <= 1_000_000_000
    ):
        raise _provider_error()
    return credits


def _clean_provider_response(
    payload: Any,
    *,
    topic: str,
    max_results: int,
    provider_request_id: str | None,
) -> SearchOutcome:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise _provider_error()
    if len(payload["results"]) > max_results:
        raise _provider_error()
    cleaned_results: list[dict[str, Any]] = []
    for raw_result in payload["results"]:
        if not isinstance(raw_result, dict):
            raise _provider_error()
        result = {
            "title": _safe_optional_text(raw_result.get("title"), TITLE_LIMIT),
            "url": _public_result_url(raw_result.get("url")),
            "content": _safe_optional_text(raw_result.get("content"), CONTENT_LIMIT),
            "score": _safe_score(raw_result.get("score")),
            "published_date": _safe_optional_text(
                raw_result.get("published_date"), DATE_LIMIT
            ),
        }
        candidate_results = [*cleaned_results, result]
        candidate = {
            "success": True,
            "results": candidate_results,
            "meta": {
                "provider": "tavily",
                "topic": topic,
                "search_depth": "basic",
                "result_count": len(candidate_results),
                "credits_used": _credits_used(payload),
            },
        }
        if len(_json_bytes(candidate)) > SEARCH_RESPONSE_LIMIT_BYTES:
            break
        cleaned_results.append(result)
    credits = _credits_used(payload)
    content = {
        "success": True,
        "results": cleaned_results,
        "meta": {
            "provider": "tavily",
            "topic": topic,
            "search_depth": "basic",
            "result_count": len(cleaned_results),
            "credits_used": credits,
        },
    }
    return SearchOutcome(
        content=content,
        result_count=len(cleaned_results),
        credits_used=credits,
        provider_request_id=provider_request_id,
    )


async def _call_tavily(
    search: SearchRequest,
    api_key: str,
    *,
    transport: httpx.AsyncBaseTransport | None,
) -> SearchOutcome:
    timeout = httpx.Timeout(20.0, connect=5.0, write=10.0, pool=5.0)
    try:
        async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=transport,
            ) as client:
                async with client.stream(
                    "POST",
                    TAVILY_SEARCH_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=_provider_payload(search),
                ) as response:
                    request_id = _safe_request_id(
                        response.headers.get("x-request-id")
                        or response.headers.get("request-id")
                    )
                    if not 200 <= response.status_code < 300:
                        raise _provider_status_failure(response)
                    declared_length = response.headers.get("content-length")
                    if declared_length is not None:
                        try:
                            declared_bytes = int(declared_length)
                        except ValueError as exc:
                            raise _provider_error() from exc
                        if (
                            declared_bytes < 0
                            or declared_bytes > TAVILY_RESPONSE_LIMIT_BYTES
                        ):
                            raise _provider_error()
                    chunks: list[bytes] = []
                    received = 0
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > TAVILY_RESPONSE_LIMIT_BYTES:
                            raise _provider_error()
                        chunks.append(chunk)
    except SearchFailure:
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise SearchFailure(
            504, "provider_timeout", "Web search provider timed out", True
        ) from exc
    except httpx.HTTPError as exc:
        raise SearchFailure(
            502,
            "provider_error",
            "Web search provider returned an invalid response",
            True,
        ) from exc
    try:
        payload = _strict_json_loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SearchFailure(
            502,
            "provider_error",
            "Web search provider returned an invalid response",
            True,
        ) from exc
    return _clean_provider_response(
        payload,
        topic=search.topic,
        max_results=search.max_results,
        provider_request_id=request_id,
    )


def _load_tavily_api_key() -> str:
    try:
        api_key = load_secret(
            TAVILY_API_KEY_ENVIRONMENT,
            credential_name=TAVILY_API_KEY_CREDENTIAL,
        )
    except SecretConfigurationError as exc:
        raise SearchFailure(
            503, "search_unavailable", "Web search is unavailable", False
        ) from exc
    if (
        not api_key
        or len(api_key) > 4096
        or any(character.isspace() for character in api_key)
        or any(unicodedata.category(character) == "Cc" for character in api_key)
    ):
        raise SearchFailure(
            503, "search_unavailable", "Web search is unavailable", False
        )
    return api_key


def _record_usage(
    *,
    username: str,
    topic: str,
    status_code: int,
    started_at: float,
    result_count: int,
    credits_used: float | int | None,
    error_code: str | None,
    provider_request_id: str | None,
) -> None:
    completed_at = time.time()
    try:
        token_usage_store.record_web_search_usage(
            mapping_username=username,
            topic=topic,
            status_code=status_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, int((completed_at - started_at) * 1000)),
            result_count=result_count,
            credits_used=credits_used,
            error_code=error_code,
            provider_request_id=provider_request_id,
        )
    except Exception:
        # Usage is intentionally fail-open and contains no query or result data.
        pass


async def execute_search(request: Request, *, username: str) -> Response:
    started_at = time.time()
    topic = ""
    status_code = 500
    result_count = 0
    credits_used: float | int | None = None
    error_code: str | None = None
    provider_request_id: str | None = None
    lease: _LimiterLease | None = None
    try:
        search = parse_search_request(
            await request.body(), request.headers.get("content-type")
        )
        topic = search.topic
        api_key = _load_tavily_api_key()
        limiter = getattr(request.app.state, "web_search_limiter", DEFAULT_SEARCH_LIMITER)
        lease = await limiter.acquire(username)
        transport = getattr(request.app.state, "web_search_transport", None)
        outcome = await _call_tavily(search, api_key, transport=transport)
        status_code = 200
        result_count = outcome.result_count
        credits_used = outcome.credits_used
        provider_request_id = outcome.provider_request_id
        return _json_response(outcome.content, status_code=200)
    except SearchFailure as failure:
        status_code = failure.status_code
        error_code = failure.error_code
        return error_response(failure)
    finally:
        if lease is not None:
            await lease.release()
        _record_usage(
            username=username,
            topic=topic,
            status_code=status_code,
            started_at=started_at,
            result_count=result_count,
            credits_used=credits_used,
            error_code=error_code,
            provider_request_id=provider_request_id,
        )

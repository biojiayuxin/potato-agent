from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
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


logger = logging.getLogger(__name__)

DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
DEEPSEEK_API_KEY_ENVIRONMENT = "DEEPSEEK_SEARCH_API_KEY"
DEEPSEEK_API_KEY_CREDENTIAL = "deepseek-search-api-key"
SEARCH_REQUEST_LIMIT_BYTES = 16 * 1024
PROVIDER_RESPONSE_LIMIT_BYTES = 8 * 1024 * 1024
SEARCH_RESPONSE_LIMIT_BYTES = 4 * 1024 * 1024
SEARCH_TIMEOUT_SECONDS = 50.0
SEARCH_CONNECT_TIMEOUT_SECONDS = 10.0
SEARCH_CONNECT_RETRIES = 2
SEARCH_CONNECT_RETRY_DELAY_SECONDS = 0.5
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 3600
URL_LIMIT = 8192
PROVIDER_REQUEST_ID_LIMIT = 128
QUERY_CHARACTER_LIMIT = 128
ALLOWED_REQUEST_FIELDS = frozenset(
    {"query", "count", "ori_question", "localization", "need_content"}
)
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    count: int
    ori_question: str | None
    localization: str
    need_content: bool


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


def _query_too_long() -> SearchFailure:
    return SearchFailure(
        400,
        "query_too_long",
        "Web search query must be at most 128 characters",
        False,
    )


def _search_text(value: Any, *, query: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request()
    if any(
        unicodedata.category(character) == "Cc" and character not in "\t\r\n"
        for character in value
    ):
        raise _invalid_request()
    if len(value) > QUERY_CHARACTER_LIMIT:
        raise _query_too_long() if query else _invalid_request()
    return value


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
    query = _search_text(payload.get("query"), query=True)
    count = payload.get("count", 10)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 50:
        raise _invalid_request()
    ori_question = payload.get("ori_question")
    if ori_question is not None:
        ori_question = _search_text(ori_question)
    localization = payload.get("localization", "")
    if not isinstance(localization, str):
        raise _invalid_request()
    if localization:
        try:
            if "%" in localization:
                raise ValueError("scoped address")
            ipaddress.ip_address(localization)
        except ValueError as exc:
            raise _invalid_request() from exc
    need_content = payload.get("need_content", False)
    if not isinstance(need_content, bool):
        raise _invalid_request()
    return SearchRequest(
        query=query,
        count=count,
        ori_question=ori_question,
        localization=localization,
        need_content=need_content,
    )


def _provider_payload(search: SearchRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": search.query,
        "count": search.count,
        "need_content": search.need_content,
    }
    if search.ori_question is not None:
        payload["ori_question"] = search.ori_question
    if search.localization:
        payload["localization"] = search.localization
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
    if status == 402:
        return SearchFailure(
            503,
            "provider_budget_exhausted",
            "Web search provider budget is exhausted",
            False,
        )
    if status in {408, 504}:
        return SearchFailure(
            504, "provider_timeout", "Web search provider timed out", True
        )
    if 400 <= status < 500:
        return SearchFailure(
            502, "provider_rejected", "Web search provider rejected the request", False
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


def _result_text(value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise _provider_error()
    return "".join(
        " " if unicodedata.category(character) == "Cc" and character not in "\t\r\n"
        else character
        for character in value
    )


def _safe_score(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _provider_error()
    if not 0 <= value <= 3:
        raise _provider_error()
    return value


def _safe_authority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= 4:
        raise _provider_error()
    return value


def _clean_provider_response(
    payload: Any,
    *,
    count: int,
    need_content: bool,
    provider_request_id: str | None,
) -> SearchOutcome:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise _provider_error()
    if len(payload["results"]) > count:
        raise _provider_error()
    cleaned_results: list[dict[str, Any]] = []
    for raw_result in payload["results"]:
        if not isinstance(raw_result, dict):
            raise _provider_error()
        site_icon = raw_result.get("site_icon")
        result = {
            "title": _result_text(raw_result.get("title")),
            "url": _public_result_url(raw_result.get("url")),
            "snippet": _result_text(raw_result.get("snippet")),
            "summary": _result_text(raw_result.get("summary")),
            "content": _result_text(raw_result.get("content")) if need_content else "",
            "rerank_score": _safe_score(raw_result.get("rerank_score")),
            "site_name": _result_text(raw_result.get("site_name"), nullable=True),
            "site_icon": _public_result_url(site_icon) if site_icon is not None else None,
            "date": _result_text(raw_result.get("date"), nullable=True),
            "authority": _safe_authority(raw_result.get("authority")),
        }
        cleaned_results.append(result)
    content = {"results": cleaned_results}
    if len(_json_bytes(content)) > SEARCH_RESPONSE_LIMIT_BYTES:
        raise _provider_error()
    return SearchOutcome(
        content=content,
        result_count=len(cleaned_results),
        provider_request_id=provider_request_id,
    )


async def _call_deepseek(
    search: SearchRequest,
    api_key: str,
    *,
    transport: httpx.AsyncBaseTransport | None,
) -> SearchOutcome:
    started_at = time.monotonic()
    timeout = httpx.Timeout(
        20.0, connect=SEARCH_CONNECT_TIMEOUT_SECONDS, write=10.0, pool=5.0
    )
    try:
        async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=transport,
            ) as client:
                request = client.build_request(
                    "POST",
                    DEEPSEEK_SEARCH_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=_provider_payload(search),
                )
                # Only retry before a connection is established, not a sent search.
                for attempt in range(SEARCH_CONNECT_RETRIES + 1):
                    try:
                        response = await client.send(request, stream=True)
                        break
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        if attempt == SEARCH_CONNECT_RETRIES:
                            raise
                        await asyncio.sleep(
                            SEARCH_CONNECT_RETRY_DELAY_SECONDS * 2**attempt
                        )
                try:
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
                            or declared_bytes > PROVIDER_RESPONSE_LIMIT_BYTES
                        ):
                            raise _provider_error()
                    chunks: list[bytes] = []
                    received = 0
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > PROVIDER_RESPONSE_LIMIT_BYTES:
                            raise _provider_error()
                        chunks.append(chunk)
                finally:
                    await response.aclose()
    except SearchFailure:
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        logger.warning(
            "Web search provider timed out (type=%s, duration_ms=%d)",
            type(exc).__name__,
            int((time.monotonic() - started_at) * 1000),
        )
        raise SearchFailure(
            504, "provider_timeout", "Web search provider timed out", True
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "Web search provider request failed (type=%s, duration_ms=%d)",
            type(exc).__name__,
            int((time.monotonic() - started_at) * 1000),
        )
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
        count=search.count,
        need_content=search.need_content,
        provider_request_id=request_id,
    )


def _load_deepseek_api_key() -> str:
    try:
        api_key = load_secret(
            DEEPSEEK_API_KEY_ENVIRONMENT,
            credential_name=DEEPSEEK_API_KEY_CREDENTIAL,
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
    status_code: int,
    started_at: float,
    result_count: int,
    error_code: str | None,
    provider_request_id: str | None,
) -> None:
    completed_at = time.time()
    try:
        token_usage_store.record_web_search_usage(
            mapping_username=username,
            status_code=status_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, int((completed_at - started_at) * 1000)),
            result_count=result_count,
            error_code=error_code,
            provider_request_id=provider_request_id,
        )
    except Exception:
        # Usage is intentionally fail-open and contains no query or result data.
        pass


async def execute_search(request: Request, *, username: str) -> Response:
    started_at = time.time()
    status_code = 500
    result_count = 0
    error_code: str | None = None
    provider_request_id: str | None = None
    lease: _LimiterLease | None = None
    try:
        search = parse_search_request(
            await request.body(), request.headers.get("content-type")
        )
        api_key = _load_deepseek_api_key()
        limiter = getattr(request.app.state, "web_search_limiter", DEFAULT_SEARCH_LIMITER)
        lease = await limiter.acquire(username)
        transport = getattr(request.app.state, "web_search_transport", None)
        outcome = await _call_deepseek(search, api_key, transport=transport)
        status_code = 200
        result_count = outcome.result_count
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
            status_code=status_code,
            started_at=started_at,
            result_count=result_count,
            error_code=error_code,
            provider_request_id=provider_request_id,
        )

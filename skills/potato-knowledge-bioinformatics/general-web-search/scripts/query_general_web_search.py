#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

import httpx
import yaml


MAX_CONFIG_BYTES = 1024 * 1024
MAX_PROXY_RESPONSE_BYTES = 64 * 1024
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 30.0
VALID_TOPICS = ("general", "news", "finance")
VALID_TIME_RANGES = ("day", "week", "month", "year")
ERROR_MESSAGES = {
    "unauthorized": "Web search authorization failed",
    "search_forbidden": "This principal cannot use web search",
    "invalid_request": "Invalid web search request",
    "request_too_large": "Web search request is too large",
    "rate_limited": "Web search is temporarily rate limited",
    "provider_auth": "Web search provider authentication failed",
    "provider_rate_limited": "Web search is temporarily rate limited",
    "provider_budget_exhausted": "Web search provider budget is exhausted",
    "provider_timeout": "Web search provider timed out",
    "provider_error": "Web search provider returned an invalid response",
    "search_unavailable": "Web search is unavailable",
}


@dataclass
class ClientFailure(Exception):
    error_code: str
    message: str
    retryable: bool = False
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class ProxyConfig:
    token: str
    search_url: str


def _safe_failure(code: str, *, retry_after_seconds: int | None = None) -> ClientFailure:
    known_code = code if code in ERROR_MESSAGES else "proxy_error"
    message = ERROR_MESSAGES.get(known_code, "Web search request failed")
    retryable = known_code in {
        "rate_limited",
        "provider_rate_limited",
        "provider_timeout",
        "provider_error",
    }
    return ClientFailure(known_code, message, retryable, retry_after_seconds)


def _load_config() -> ProxyConfig:
    raw_home = os.getenv("HERMES_HOME")
    if not raw_home:
        raise ClientFailure("client_config", "Web search client is not configured")
    hermes_home = Path(raw_home)
    if not hermes_home.is_absolute():
        raise ClientFailure("client_config", "Web search client is not configured")
    config_path = hermes_home / "config.yaml"
    try:
        if config_path.stat().st_size > MAX_CONFIG_BYTES:
            raise ClientFailure("client_config", "Web search client is not configured")
        raw_config = config_path.read_bytes()
    except (OSError, ValueError) as exc:
        raise ClientFailure(
            "client_config", "Web search client is not configured"
        ) from exc
    if len(raw_config) > MAX_CONFIG_BYTES:
        raise ClientFailure("client_config", "Web search client is not configured")
    try:
        config = yaml.safe_load(raw_config)
    except yaml.YAMLError as exc:
        raise ClientFailure(
            "client_config", "Web search client is not configured"
        ) from exc
    if not isinstance(config, dict) or not isinstance(config.get("model"), dict):
        raise ClientFailure("client_config", "Web search client is not configured")
    model = config["model"]
    token = model.get("api_key")
    base_url = model.get("base_url")
    if (
        not isinstance(token, str)
        or not token.startswith("pmp_")
        or not token.strip()
        or any(character.isspace() for character in token)
        or not isinstance(base_url, str)
    ):
        raise ClientFailure("client_config", "Web search client is not configured")
    return ProxyConfig(token=token, search_url=_search_url(base_url))


def _search_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise ClientFailure(
            "client_config", "Web search client is not configured"
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1"
        or not parsed.hostname
        or port is None
        or not 1 <= port <= 65535
    ):
        raise ClientFailure("client_config", "Web search client is not configured")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ClientFailure(
            "client_config", "Web search client is not configured"
        ) from exc
    if not address.is_loopback:
        raise ClientFailure("client_config", "Web search client is not configured")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"{parsed.scheme}://{host}:{port}/v1/search"


def _timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if (
        not math.isfinite(parsed)
        or not MIN_TIMEOUT_SECONDS <= parsed <= MAX_TIMEOUT_SECONDS
    ):
        raise argparse.ArgumentTypeError(
            f"timeout must be between {MIN_TIMEOUT_SECONDS:g} and {MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search public web pages.")
    parser.add_argument("query", metavar="QUERY")
    parser.add_argument("--max-results", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--topic", choices=VALID_TOPICS, default="general")
    parser.add_argument("--time-range", choices=VALID_TIME_RANGES)
    parser.add_argument("--include-domain", action="append", default=[])
    parser.add_argument("--exclude-domain", action="append", default=[])
    parser.add_argument("--timeout", type=_timeout, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def _request_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": args.query,
        "max_results": args.max_results,
        "topic": args.topic,
        "include_domains": args.include_domain,
        "exclude_domains": args.exclude_domain,
    }
    if args.time_range is not None:
        payload["time_range"] = args.time_range
    return payload


def _read_proxy_response(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes():
        received += len(chunk)
        if received > MAX_PROXY_RESPONSE_BYTES:
            raise ClientFailure("proxy_error", "Web search proxy returned too much data")
        chunks.append(chunk)
    return b"".join(chunks)


def _proxy_error(payload: Any) -> ClientFailure:
    if not isinstance(payload, dict) or payload.get("success") is not False:
        return ClientFailure("proxy_error", "Web search proxy returned an invalid response")
    code = payload.get("error_code")
    if not isinstance(code, str):
        return ClientFailure("proxy_error", "Web search proxy returned an invalid response")
    retry_after = payload.get("retry_after_seconds")
    if (
        isinstance(retry_after, bool)
        or retry_after is not None
        and (not isinstance(retry_after, int) or not 1 <= retry_after <= 3600)
    ):
        retry_after = None
    return _safe_failure(code, retry_after_seconds=retry_after)


def _validated_success(payload: Any) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or not isinstance(payload.get("results"), list)
        or not isinstance(payload.get("meta"), dict)
        or len(payload["results"]) > 10
    ):
        raise ClientFailure("proxy_error", "Web search proxy returned an invalid response")
    expected_fields = {"title", "url", "content", "score", "published_date"}
    for result in payload["results"]:
        if not isinstance(result, dict) or set(result) != expected_fields:
            raise ClientFailure(
                "proxy_error", "Web search proxy returned an invalid response"
            )
        if not isinstance(result["url"], str):
            raise ClientFailure(
                "proxy_error", "Web search proxy returned an invalid response"
            )
        for field in ("title", "content", "published_date"):
            if result[field] is not None and not isinstance(result[field], str):
                raise ClientFailure(
                    "proxy_error", "Web search proxy returned an invalid response"
                )
        score = result["score"]
        if score is not None and (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
        ):
            raise ClientFailure(
                "proxy_error", "Web search proxy returned an invalid response"
            )
    meta = payload["meta"]
    if (
        meta.get("provider") != "tavily"
        or meta.get("search_depth") != "basic"
        or meta.get("topic") not in VALID_TOPICS
        or isinstance(meta.get("result_count"), bool)
        or meta.get("result_count") != len(payload["results"])
    ):
        raise ClientFailure("proxy_error", "Web search proxy returned an invalid response")
    credits = meta.get("credits_used")
    if credits is not None and (
        isinstance(credits, bool)
        or not isinstance(credits, (int, float))
        or not math.isfinite(float(credits))
        or credits < 0
    ):
        raise ClientFailure("proxy_error", "Web search proxy returned an invalid response")
    return payload


def query_proxy(config: ProxyConfig, args: argparse.Namespace) -> dict[str, Any]:
    timeout = httpx.Timeout(args.timeout, connect=min(5.0, args.timeout))
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            with client.stream(
                "POST",
                config.search_url,
                headers={"Authorization": f"Bearer {config.token}"},
                json=_request_payload(args),
            ) as response:
                raw_body = _read_proxy_response(response)
    except ClientFailure:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ClientFailure(
            "proxy_unavailable", "Web search proxy is unavailable", True
        ) from exc
    except httpx.HTTPError as exc:
        raise ClientFailure(
            "proxy_error", "Web search proxy request failed", True
        ) from exc
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientFailure(
            "proxy_error", "Web search proxy returned an invalid response"
        ) from exc
    if not 200 <= response.status_code < 300:
        raise _proxy_error(payload)
    return _validated_success(payload)


def _failure_payload(failure: ClientFailure) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error": failure.message,
        "error_code": failure.error_code,
        "retryable": failure.retryable,
    }
    if failure.retry_after_seconds is not None:
        payload["retry_after_seconds"] = failure.retry_after_seconds
    return payload


def emit_envelope(payload: dict[str, Any]) -> None:
    rendered = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    rendered = (
        rendered.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    sys.stdout.write(
        "<untrusted_web_search_results>\n"
        f"{rendered}\n"
        "</untrusted_web_search_results>\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = _load_config()
        result = query_proxy(config, args)
    except ClientFailure as failure:
        emit_envelope(_failure_payload(failure))
        return 1
    emit_envelope(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

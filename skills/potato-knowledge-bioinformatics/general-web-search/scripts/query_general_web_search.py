#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit


MAX_CONFIG_BYTES = 1024 * 1024
MAX_PROXY_RESPONSE_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
VALID_TOPICS = ("general", "news", "finance")
VALID_TIME_RANGES = ("day", "week", "month", "year")
ERROR_MESSAGES = {
    "client_config": "Web search client is not configured",
    "client_dependency": "Run with a Python environment containing PyYAML",
    "proxy_error": "Web search proxy returned an invalid response",
    "proxy_unavailable": "Web search proxy is unavailable",
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
    retryable: bool = False
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class ProxyConfig:
    token: str
    search_url: str


def _load_config() -> ProxyConfig:
    try:
        import yaml
    except ImportError as exc:
        raise ClientFailure("client_dependency") from exc

    hermes_home = Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes")
    if not hermes_home.is_absolute():
        raise ClientFailure("client_config")
    try:
        with (hermes_home / "config.yaml").open("rb") as config_file:
            raw_config = config_file.read(MAX_CONFIG_BYTES + 1)
        if len(raw_config) > MAX_CONFIG_BYTES:
            raise ValueError("config too large")
        config = yaml.safe_load(raw_config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ClientFailure("client_config") from exc
    if not isinstance(config, dict) or not isinstance(config.get("model"), dict):
        raise ClientFailure("client_config")
    model = config["model"]
    token = model.get("api_key")
    base_url = model.get("base_url")
    if (
        not isinstance(token, str)
        or not token.startswith("pmp_")
        or any(character.isspace() for character in token)
        or not isinstance(base_url, str)
    ):
        raise ClientFailure("client_config")
    return ProxyConfig(token=token, search_url=_search_url(base_url))


def _search_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise ClientFailure("client_config") from exc
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
        raise ClientFailure("client_config")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ClientFailure("client_config") from exc
    if not address.is_loopback:
        raise ClientFailure("client_config")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"{parsed.scheme}://{host}:{port}/v1/search"


def _timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not 1 <= parsed <= 60:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 60 seconds")
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


def _proxy_error(payload: Any) -> ClientFailure:
    if not isinstance(payload, dict) or payload.get("success") is not False:
        return ClientFailure("proxy_error")
    code = payload.get("error_code")
    if not isinstance(code, str):
        return ClientFailure("proxy_error")
    retry_after = payload.get("retry_after_seconds")
    if (
        isinstance(retry_after, bool)
        or retry_after is not None
        and (not isinstance(retry_after, int) or not 1 <= retry_after <= 3600)
    ):
        retry_after = None
    code = code if code in ERROR_MESSAGES else "proxy_error"
    retryable = code in {
        "rate_limited", "provider_rate_limited", "provider_timeout", "provider_error"
    }
    return ClientFailure(code, retryable, retry_after)


def _validated_success(payload: Any) -> dict[str, Any]:
    # The local proxy validates and normalizes result fields and provider metadata.
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or not isinstance(payload.get("results"), list)
        or not isinstance(payload.get("meta"), dict)
        or len(payload["results"]) > 10
    ):
        raise ClientFailure("proxy_error")
    if any(not isinstance(result, dict) for result in payload["results"]):
        raise ClientFailure("proxy_error")
    return payload


def query_proxy(config: ProxyConfig, args: argparse.Namespace) -> dict[str, Any]:
    url = urlsplit(config.search_url)
    connection_type = HTTPSConnection if url.scheme == "https" else HTTPConnection
    # Connect directly to the validated loopback address, without proxies or redirects.
    connection = connection_type(url.hostname, url.port, timeout=args.timeout)
    try:
        connection.request(
            "POST",
            url.path,
            body=json.dumps(_request_payload(args)).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.token}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        raw_body = response.read(MAX_PROXY_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise ClientFailure("proxy_unavailable", retryable=True) from exc
    except HTTPException as exc:
        raise ClientFailure("proxy_error", retryable=True) from exc
    finally:
        connection.close()
    if len(raw_body) > MAX_PROXY_RESPONSE_BYTES:
        raise ClientFailure("proxy_error")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientFailure("proxy_error") from exc
    if not 200 <= response.status < 300:
        raise _proxy_error(payload)
    return _validated_success(payload)


def _failure_payload(failure: ClientFailure) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error": ERROR_MESSAGES[failure.error_code],
        "error_code": failure.error_code,
        "retryable": failure.retryable,
    }
    if failure.retry_after_seconds is not None:
        payload["retry_after_seconds"] = failure.retry_after_seconds
    return payload


def emit_envelope(payload: dict[str, Any]) -> None:
    try:
        rendered = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
    except ValueError as exc:
        raise ClientFailure("proxy_error") from exc
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
        emit_envelope(query_proxy(config, args))
    except ClientFailure as failure:
        emit_envelope(_failure_payload(failure))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

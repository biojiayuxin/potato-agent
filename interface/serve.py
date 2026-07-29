from __future__ import annotations

import argparse
import ipaddress
import os

import uvicorn

from .secret_config import (
    SecretConfigurationError,
    boolean_environment,
    is_production_environment,
    load_session_cookie_secure,
)


UVICORN_WEBSOCKET_MAX_BYTES = 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Potato Interface")
    parser.add_argument(
        "--host",
        default=os.getenv("INTERFACE_BIND_HOST", "127.0.0.1"),
    )
    parser.add_argument("--port", type=int, default=3000)
    return parser


def _is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_specific_non_loopback_ipv4(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.version == 4
        and not address.is_loopback
        and not address.is_unspecified
        and not address.is_multicast
    )


def _validate_listener(host: str) -> None:
    if not is_production_environment():
        return
    load_session_cookie_secure()
    allow_insecure_http = boolean_environment(
        "INTERFACE_ALLOW_INSECURE_HTTP",
        default=False,
    )
    loopback = _is_loopback_host(host)
    if allow_insecure_http and not _is_specific_non_loopback_ipv4(host):
        raise SecretConfigurationError(
            "INTERFACE_ALLOW_INSECURE_HTTP=true requires a specific "
            "non-loopback IPv4 listener"
        )
    if not allow_insecure_http and not loopback:
        raise SecretConfigurationError(
            "a non-loopback production listener requires "
            "INTERFACE_ALLOW_INSECURE_HTTP=true"
        )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    previous_environment = os.environ.get("INTERFACE_ENVIRONMENT")
    os.environ.setdefault("INTERFACE_ENVIRONMENT", "production")
    try:
        _validate_listener(args.host)
        uvicorn.run(
            "interface.app:app",
            host=args.host,
            port=args.port,
            ws_max_size=UVICORN_WEBSOCKET_MAX_BYTES,
        )
    finally:
        if previous_environment is None:
            os.environ.pop("INTERFACE_ENVIRONMENT", None)
        else:
            os.environ["INTERFACE_ENVIRONMENT"] = previous_environment


if __name__ == "__main__":
    main()

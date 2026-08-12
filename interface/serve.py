from __future__ import annotations

import argparse
import ipaddress
import os
import socket

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


def _parse_bind_hosts(host: str) -> tuple[str, ...]:
    hosts = tuple(value.strip() for value in host.split(","))
    if not hosts or any(not value for value in hosts):
        raise SecretConfigurationError(
            "INTERFACE_BIND_HOST must contain one or more comma-separated hosts"
        )
    if len(hosts) != len(set(hosts)):
        raise SecretConfigurationError("INTERFACE_BIND_HOST contains duplicate hosts")
    return hosts


def _validate_listener(host: str) -> tuple[str, ...]:
    hosts = _parse_bind_hosts(host)
    if not is_production_environment():
        return hosts
    load_session_cookie_secure()
    allow_insecure_http = boolean_environment(
        "INTERFACE_ALLOW_INSECURE_HTTP",
        default=False,
    )
    if allow_insecure_http and not all(
        _is_specific_non_loopback_ipv4(value) for value in hosts
    ):
        raise SecretConfigurationError(
            "INTERFACE_ALLOW_INSECURE_HTTP=true requires a specific "
            "non-loopback IPv4 listener"
        )
    if not allow_insecure_http and not all(_is_loopback_host(value) for value in hosts):
        raise SecretConfigurationError(
            "a non-loopback production listener requires "
            "INTERFACE_ALLOW_INSECURE_HTTP=true"
        )
    return hosts


def _run_server(hosts: tuple[str, ...], port: int) -> None:
    if len(hosts) == 1:
        uvicorn.run(
            "interface.app:app",
            host=hosts[0],
            port=port,
            ws_max_size=UVICORN_WEBSOCKET_MAX_BYTES,
        )
        return

    config = uvicorn.Config(
        "interface.app:app",
        host=hosts[0],
        port=port,
        ws_max_size=UVICORN_WEBSOCKET_MAX_BYTES,
    )
    sockets: list[socket.socket] = []
    try:
        for host in hosts:
            config.host = host
            sockets.append(config.bind_socket())
        config.host = ",".join(hosts)
        server = uvicorn.Server(config=config)
        server.run(sockets=sockets)
        if not server.started:
            raise SystemExit(1)
    finally:
        for bound_socket in sockets:
            bound_socket.close()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    previous_environment = os.environ.get("INTERFACE_ENVIRONMENT")
    os.environ.setdefault("INTERFACE_ENVIRONMENT", "production")
    try:
        hosts = _validate_listener(args.host)
        _run_server(hosts, args.port)
    finally:
        if previous_environment is None:
            os.environ.pop("INTERFACE_ENVIRONMENT", None)
        else:
            os.environ["INTERFACE_ENVIRONMENT"] = previous_environment


if __name__ == "__main__":
    main()

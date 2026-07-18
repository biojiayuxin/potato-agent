"""Minimal command line accepted by Potato's systemd units."""

from __future__ import annotations

import argparse

from potato_hermes_lite.runtime_guard import run_gateway_guard


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes")
    commands = parser.add_subparsers(dest="command", required=True)
    gateway = commands.add_parser("gateway")
    gateway_commands = gateway.add_subparsers(dest="gateway_command", required=True)
    run = gateway_commands.add_parser("run")
    run.add_argument("--replace", action="store_true")
    run.add_argument("-v", "--verbose", action="count", default=0)
    run.add_argument("-q", "--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "gateway" or args.gateway_command != "run":
        _parser().error("Potato Hermes Lite only supports 'gateway run'")
    return run_gateway_guard(
        replace=bool(args.replace),
        verbosity=None if args.quiet else int(args.verbose),
    )


if __name__ == "__main__":
    raise SystemExit(main())

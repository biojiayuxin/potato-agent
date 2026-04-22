#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from interface.auth_db import DEFAULT_AUTH_DB_PATH, upsert_user
from interface.hermes_service import (
    install_user_files,
    require_binary,
    require_root,
)
from interface.mapping import (
    DEFAULT_MAPPING_PATH,
    MappingStore,
    load_mapping,
    upsert_user_mapping_entry,
    write_mapping,
)


class ProvisionError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create one interface user and bind it to one Linux-backed Hermes instance."
    )
    parser.add_argument("username", help="Short username, e.g. alice or user_test")
    parser.add_argument("email", help="Interface login email")
    parser.add_argument("password", help="Interface login password")
    parser.add_argument(
        "--display-name",
        help="Optional display name shown in the interface. Defaults to username.",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING_PATH,
        help=f"Path to users_mapping.yaml (default: {DEFAULT_MAPPING_PATH})",
    )
    parser.add_argument(
        "--auth-db",
        type=Path,
        default=DEFAULT_AUTH_DB_PATH,
        help=f"Path to interface auth DB (default: {DEFAULT_AUTH_DB_PATH})",
    )
    parser.add_argument(
        "--api-key",
        help="Optional per-user Hermes API key override. Defaults to the shared placeholder or a generated key.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    require_root()
    require_binary("systemctl")
    require_binary("useradd")

    config = load_mapping(args.mapping, resolve_env=False)
    display_name = args.display_name or args.username

    upsert_user_mapping_entry(
        config,
        username=args.username,
        email=args.email,
        display_name=display_name,
        api_key=args.api_key,
    )
    write_mapping(args.mapping, config)

    resolved_config = load_mapping(args.mapping, resolve_env=True)

    target = MappingStore(args.mapping).get_target_by_username(args.username)
    if target is None:
        raise ProvisionError(f"Failed to resolve mapping target for {args.username!r}")

    install_user_files(resolved_config, target)
    upsert_user(
        username=args.username,
        email=args.email,
        password=args.password,
        mapping_username=args.username,
        name=display_name,
        db_path=args.auth_db,
    )

    print(f"Interface user: {args.username}")
    print(f"Email: {args.email}")
    print(f"Auth DB: {args.auth_db}")
    print(f"Linux user: {target.linux_user}")
    print(f"Hermes home: {target.hermes_home}")
    print(f"Hermes service: {target.systemd_service}")
    print(f"Hermes endpoint: {target.connection_url}")
    print("Hermes runtime start: deferred until the user enters the workspace")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

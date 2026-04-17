#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from interface.auth_db import DEFAULT_AUTH_DB_PATH, delete_user_by_mapping_username
from interface.hermes_service import (
    remove_linux_user,
    require_binary,
    require_root,
    stop_and_remove_service,
)
from interface.mapping import (
    DEFAULT_MAPPING_PATH,
    load_mapping,
    remove_user_mapping_entry,
    MappingStore,
    write_mapping,
)


class DeprovisionError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete one interface user and unbind its Hermes/Linux resources."
    )
    parser.add_argument(
        "username", help="Username from users_mapping.yaml, e.g. user_test"
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
        "--delete-home",
        action="store_true",
        help="Also delete /home/<linux_user> by removing the Linux user with userdel -r.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    require_root()
    require_binary("systemctl")

    config = load_mapping(args.mapping, resolve_env=False)
    target = MappingStore(args.mapping).get_target_by_username(args.username)
    if target is None:
        raise DeprovisionError(
            f"User {args.username!r} not found in users_mapping.yaml."
        )

    stop_and_remove_service(target.systemd_service)
    remove_linux_user(target.linux_user, delete_home=args.delete_home)
    delete_user_by_mapping_username(args.username, db_path=args.auth_db)
    remove_user_mapping_entry(config, args.username)
    write_mapping(args.mapping, config)

    print(f"Removed interface user: {args.username}")
    print(f"Removed Hermes service: {target.systemd_service}")
    print(f"Removed Linux user: {target.linux_user}")
    print(f"Updated auth DB: {args.auth_db}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

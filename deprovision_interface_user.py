#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from interface.auth_db import (
    DEFAULT_AUTH_DB_PATH,
    delete_user_by_mapping_username,
    is_temporary_user,
    list_users,
)
from interface.chat_share_store import (
    DEFAULT_CHAT_SHARE_DB_PATH,
    invalidate_user_chat_share_data,
)
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
from interface.user_lifecycle_lock import mapping_lifecycle_lock, user_lifecycle_lock


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
        "--share-db",
        type=Path,
        default=DEFAULT_CHAT_SHARE_DB_PATH,
        help=f"Path to chat share DB (default: {DEFAULT_CHAT_SHARE_DB_PATH})",
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

    mapping_lock = (
        mapping_lifecycle_lock(exclusive=True)
        if os.geteuid() == 0
        else contextlib.nullcontext()
    )
    user_lock = (
        user_lifecycle_lock(args.username, exclusive=True, publish_marker=True)
        if os.geteuid() == 0
        else contextlib.nullcontext()
    )
    with mapping_lock:
        with user_lock:
            return _deprovision_locked(args)


def _deprovision_locked(args: argparse.Namespace) -> int:
    config = load_mapping(args.mapping, resolve_env=False)
    target = MappingStore(args.mapping).get_target_by_username(args.username)
    if target is None:
        raise DeprovisionError(
            f"User {args.username!r} not found in users_mapping.yaml."
        )

    auth_user = next(
        (
            user
            for user in list_users(db_path=args.auth_db)
            if user.mapping_username == args.username
        ),
        None,
    )
    if auth_user is None:
        raise DeprovisionError(
            f"User {args.username!r} not found in the interface auth DB; "
            "chat share ownership cannot be cleaned safely."
        )

    account_namespace = (
        "temporary"
        if is_temporary_user(auth_user.id, db_path=args.auth_db)
        else "formal"
    )
    invalidate_user_chat_share_data(
        auth_user.id,
        recipient_user_id=f"{account_namespace}:{auth_user.id}",
        db_path=args.share_db,
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

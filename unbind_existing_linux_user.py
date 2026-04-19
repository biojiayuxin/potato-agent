#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from interface.auth_db import connect_auth_db
from interface.hermes_service import require_binary, require_root, stop_and_remove_service
from interface.mapping import (
    MappingStore,
    load_mapping,
    remove_user_mapping_entry,
    write_mapping,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MAPPING_PATH = PROJECT_ROOT / "users_mapping.yaml"
DEFAULT_AUTH_DB_PATH = PROJECT_ROOT / "interface" / "data" / "interface.db"


class UnbindExistingUserError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely unbind an existing Linux user from one interface/web account."
    )
    parser.add_argument(
        "username",
        help="Interface/web username from users_mapping.yaml, e.g. alice",
    )
    return parser


def _delete_interface_state(username: str, email: str, auth_db: Path) -> tuple[int, int]:
    with connect_auth_db(auth_db) as conn:
        rows = conn.execute(
            "select id from users where mapping_username = ? or username = ?",
            (username, username),
        ).fetchall()
        user_ids = [str(row["id"]) for row in rows]

        deleted_transcripts = 0
        if user_ids:
            placeholders = ",".join("?" for _ in user_ids)
            cursor = conn.execute(
                f"delete from session_display_transcripts where user_id in ({placeholders})",
                user_ids,
            )
            deleted_transcripts = cursor.rowcount

        deleted_users = conn.execute(
            "delete from users where mapping_username = ? or username = ?",
            (username, username),
        ).rowcount

        conn.execute(
            "delete from signup_jobs where username = ? or lower(email) = lower(?)",
            (username, email),
        )
        conn.commit()
    return deleted_users, deleted_transcripts


def main() -> int:
    args = build_parser().parse_args()
    require_root()
    require_binary("systemctl")
    mapping_path = DEFAULT_MAPPING_PATH
    auth_db_path = DEFAULT_AUTH_DB_PATH

    config = load_mapping(mapping_path, resolve_env=False)
    target = MappingStore(mapping_path).get_target_by_username(args.username)
    if target is None:
        raise UnbindExistingUserError(
            f"User {args.username!r} not found in users_mapping.yaml."
        )

    stop_and_remove_service(target.systemd_service)
    deleted_users, deleted_transcripts = _delete_interface_state(
        args.username, target.email, auth_db_path
    )
    removed = remove_user_mapping_entry(config, args.username)
    if removed:
        write_mapping(mapping_path, config)

    print(f"Unbound interface user: {args.username}")
    print(f"Removed Hermes service: {target.systemd_service}")
    print(f"Removed interface auth rows: {deleted_users}")
    print(f"Removed display transcripts: {deleted_transcripts}")
    print(f"Mapping file: {mapping_path}")
    print(f"Auth DB: {auth_db_path}")
    print(f"Preserved Linux user: {target.linux_user}")
    print(f"Preserved home directory: {target.home_dir}")
    print(f"Preserved Hermes home: {target.hermes_home}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

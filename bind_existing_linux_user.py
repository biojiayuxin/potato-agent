#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from interface.auth_db import (
    DEFAULT_AUTH_DB_PATH,
    email_exists,
    upsert_user,
    username_exists,
)
from interface.cli_secrets import add_password_source_arguments, read_password
from interface.hermes_service import (
    get_linux_user_info,
    install_user_files,
    require_binary,
    require_root,
)
from interface.mapping import (
    DEFAULT_MAPPING_PATH,
    DEFAULT_MODEL_NAME,
    MappingStore,
    build_targets_from_config,
    load_mapping,
    select_next_port,
    slugify_username,
    write_mapping,
)
from interface.password_policy import validate_password_complexity
from interface.model_proxy_config import generate_model_proxy_token
from interface.user_lifecycle_lock import mapping_lifecycle_lock, user_lifecycle_lock


class BindExistingUserError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind an existing Linux user to a new interface/web account and Hermes service."
    )
    parser.add_argument("username", help="Interface/web username, e.g. alice")
    parser.add_argument("email", help="Interface login email")
    add_password_source_arguments(parser)
    parser.add_argument(
        "--linux-user",
        required=True,
        help="Existing Linux user to bind, e.g. alice",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    password = read_password(args)
    validate_password_complexity(password)
    require_root()
    require_binary("systemctl")

    username = args.username.strip()
    email = args.email.strip().lower()
    display_name = username
    linux_user = args.linux_user.strip()
    mapping_path = DEFAULT_MAPPING_PATH
    auth_db_path = DEFAULT_AUTH_DB_PATH

    linux_info = get_linux_user_info(linux_user)

    mapping_lock = (
        mapping_lifecycle_lock(exclusive=True)
        if os.geteuid() == 0
        else contextlib.nullcontext()
    )
    user_lock = (
        user_lifecycle_lock(username, exclusive=True)
        if os.geteuid() == 0
        else contextlib.nullcontext()
    )
    with mapping_lock:
        with user_lock:
            return _bind_user_locked(
                username=username,
                email=email,
                display_name=display_name,
                linux_user=linux_user,
                linux_info=linux_info,
                password=password,
                mapping_path=mapping_path,
                auth_db_path=auth_db_path,
            )


def _bind_user_locked(
    *,
    username: str,
    email: str,
    display_name: str,
    linux_user: str,
    linux_info: dict[str, Any],
    password: str,
    mapping_path: Path,
    auth_db_path: Path,
) -> int:
    if username_exists(username, db_path=auth_db_path):
        raise BindExistingUserError(
            f"Interface username {username!r} is already in use."
        )
    if email_exists(email, db_path=auth_db_path):
        raise BindExistingUserError(f"Interface email {email!r} is already in use.")

    config = load_mapping(mapping_path, resolve_env=False)
    users = config.setdefault("users", [])
    if not isinstance(users, list):
        raise BindExistingUserError("users_mapping.yaml has invalid users structure.")

    if any(
        isinstance(item, dict) and item.get("linux_user") == linux_user
        for item in users
    ):
        raise BindExistingUserError(
            f"Linux user {linux_user!r} is already bound in users_mapping.yaml."
        )

    mapping_entry = {
        "username": username,
        "email": email,
        "display_name": display_name,
        "linux_user": linux_user,
        "home_dir": str(linux_info["home_dir"]),
        "hermes_home": str(linux_info["home_dir"] / ".hermes"),
        "workdir": str(linux_info["home_dir"]),
        "api_port": None,
        "retention_identity_nonce": secrets.token_hex(16),
    }

    mapping_entry["api_port"] = select_next_port(config)
    mapping_entry["api_server_model_name"] = DEFAULT_MODEL_NAME
    mapping_entry["systemd_service"] = f"hermes-{slugify_username(username)}.service"
    existing_api_keys = {
        str(item.get("api_key") or "").strip()
        for item in users
        if isinstance(item, dict)
    }
    mapping_entry["api_key"] = secrets.token_urlsafe(32)
    while mapping_entry["api_key"] in existing_api_keys:
        mapping_entry["api_key"] = secrets.token_urlsafe(32)
    mapping_entry["model_proxy_token"] = generate_model_proxy_token()

    users.append(mapping_entry)
    build_targets_from_config(config)
    write_mapping(mapping_path, config)

    resolved_config = load_mapping(mapping_path, resolve_env=True)
    target = MappingStore(mapping_path).get_target_by_username(username)
    if target is None:
        raise BindExistingUserError(
            f"Failed to resolve mapping target for {username!r}"
        )

    install_user_files(resolved_config, target)
    upsert_user(
        username=username,
        email=email,
        password=password,
        mapping_username=username,
        name=display_name,
        db_path=auth_db_path,
    )

    print(f"Bound existing Linux user: {linux_user}")
    print(f"Interface user: {username}")
    print(f"Email: {email}")
    print(f"Mapping file: {mapping_path}")
    print(f"Auth DB: {auth_db_path}")
    print(f"Home dir: {linux_info['home_dir']}")
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

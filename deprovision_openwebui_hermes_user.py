#!/usr/bin/env python3
"""Remove one Open WebUI user and unbind its Linux-backed Hermes instance.

This script reverses the provisioning flow created by
`provision_openwebui_hermes_user.py`:
- removes the user's private wrapper model and access grants from Open WebUI
- removes the user's Open WebUI auth/user/api_key/group memberships
- removes the user's OpenAI connection entry from Open WebUI config
- stops and disables the per-user Hermes systemd service
- removes the systemd unit and the optional Linux user/home
- removes the user's entry from users_mapping.yaml
- restarts Open WebUI and verifies the user can no longer sign in
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from generate_multiuser_bundle import (
    ConfigError,
    build_user_specs,
    load_mapping,
    resolve_env_placeholders,
)
from provision_openwebui_hermes_user import (
    DEFAULT_MAPPING_PATH,
    DEFAULT_OPENWEBUI_DB,
    DEFAULT_OPENWEBUI_SERVICE,
    ProvisionError,
    fetchone,
    get_config_row,
    require_root,
    restart_openwebui,
    run_command,
    wait_for_openwebui,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete one Open WebUI user and unbind its Hermes/Linux resources."
    )
    parser.add_argument(
        "username", help="Username from users_mapping.yaml, e.g. user_test"
    )
    parser.add_argument(
        "password",
        help="Current Open WebUI password for sign-in rejection verification.",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING_PATH,
        help=f"Path to users_mapping.yaml (default: {DEFAULT_MAPPING_PATH})",
    )
    parser.add_argument(
        "--openwebui-db",
        type=Path,
        default=DEFAULT_OPENWEBUI_DB,
        help=f"Path to Open WebUI sqlite DB (default: {DEFAULT_OPENWEBUI_DB})",
    )
    parser.add_argument(
        "--openwebui-service",
        default=DEFAULT_OPENWEBUI_SERVICE,
        help=f"systemd service name for Open WebUI (default: {DEFAULT_OPENWEBUI_SERVICE})",
    )
    parser.add_argument(
        "--delete-home",
        action="store_true",
        help="Also delete /home/<linux_user> by removing the Linux user with userdel -r.",
    )
    parser.add_argument(
        "--keep-openwebui-user",
        action="store_true",
        help="Keep the Open WebUI auth/user rows and only unbind Hermes/model resources.",
    )
    return parser


def require_paths(mapping: Path, db_path: Path) -> None:
    if not mapping.exists():
        raise ProvisionError(f"Mapping file not found: {mapping}")
    if not db_path.exists():
        raise ProvisionError(f"Open WebUI database not found: {db_path}")
    if not shutil.which("systemctl"):
        raise ProvisionError("systemctl is required.")


def resolve_user(config: dict[str, Any], username: str):
    specs = build_user_specs(config)
    spec = next((item for item in specs if item.username == username), None)
    if spec is None:
        raise ProvisionError(f"User {username!r} not found in users_mapping.yaml.")
    return spec


def compact_openai_config(persisted: dict[str, Any]) -> dict[str, Any]:
    openai_cfg = persisted.setdefault("openai", {})
    base_urls = list(openai_cfg.get("api_base_urls") or [])
    api_keys = list(openai_cfg.get("api_keys") or [])
    api_configs = dict(openai_cfg.get("api_configs") or {})

    normalized: list[tuple[str, str, dict[str, Any]]] = []
    max_len = max(len(base_urls), len(api_keys))
    for index in range(max_len):
        url = base_urls[index] if index < len(base_urls) else ""
        key = api_keys[index] if index < len(api_keys) else ""
        cfg = api_configs.get(str(index), {})
        if not url:
            continue
        normalized.append((url, key, cfg if isinstance(cfg, dict) else {}))

    openai_cfg["api_base_urls"] = [item[0] for item in normalized]
    openai_cfg["api_keys"] = [item[1] for item in normalized]
    openai_cfg["api_configs"] = {
        str(index): item[2] for index, item in enumerate(normalized)
    }
    return persisted


def remove_openwebui_binding(
    conn: sqlite3.Connection, spec, *, keep_user: bool
) -> None:
    config_id, persisted = get_config_row(conn)
    openai_cfg = persisted.setdefault("openai", {})
    base_urls = list(openai_cfg.get("api_base_urls") or [])
    api_keys = list(openai_cfg.get("api_keys") or [])
    api_configs = dict(openai_cfg.get("api_configs") or {})

    indexes_to_remove: set[int] = set()
    for raw_index, cfg in api_configs.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("prefix_id") == spec.connection_prefix:
            indexes_to_remove.add(int(raw_index))

    for index, url in enumerate(base_urls):
        if url == spec.connection_url:
            indexes_to_remove.add(index)

    for index in sorted(indexes_to_remove, reverse=True):
        if index < len(base_urls):
            del base_urls[index]
        if index < len(api_keys):
            del api_keys[index]
        api_configs.pop(str(index), None)

    openai_cfg["api_base_urls"] = base_urls
    openai_cfg["api_keys"] = api_keys
    openai_cfg["api_configs"] = api_configs
    compact_openai_config(persisted)
    conn.execute(
        "update config set data=?, updated_at=CURRENT_TIMESTAMP where id=?",
        (json.dumps(persisted), config_id),
    )

    conn.execute(
        "delete from access_grant where resource_type='model' and resource_id=?",
        (spec.model_id,),
    )
    conn.execute("delete from model where id=?", (spec.model_id,))

    if not keep_user:
        conn.execute(
            "delete from chat_file where user_id=? or chat_id in (select id from chat where user_id=?)",
            (spec.openwebui_user_id, spec.openwebui_user_id),
        )
        conn.execute(
            "delete from chat_message where chat_id in (select id from chat where user_id=?)",
            (spec.openwebui_user_id,),
        )
        conn.execute("delete from chat where user_id=?", (spec.openwebui_user_id,))
        conn.execute("delete from folder where user_id=?", (spec.openwebui_user_id,))
        conn.execute(
            "delete from channel_member where user_id=?", (spec.openwebui_user_id,)
        )
        conn.execute("delete from api_key where user_id=?", (spec.openwebui_user_id,))
        conn.execute(
            "delete from group_member where user_id=?", (spec.openwebui_user_id,)
        )
        conn.execute("delete from auth where id=?", (spec.openwebui_user_id,))
        conn.execute("delete from user where id=?", (spec.openwebui_user_id,))

    conn.commit()


def stop_and_remove_service(service_name: str) -> None:
    subprocess.run(
        ["systemctl", "disable", "--now", service_name], capture_output=True, text=True
    )
    service_path = Path("/etc/systemd/system") / service_name
    if service_path.exists():
        service_path.unlink()
    run_command(["systemctl", "daemon-reload"])


def remove_linux_user(linux_user: str, *, delete_home: bool) -> None:
    result = subprocess.run(["id", "-u", linux_user], capture_output=True, text=True)
    if result.returncode != 0:
        return
    command = ["userdel"]
    if delete_home:
        command.append("-r")
    command.append(linux_user)
    run_command(command)


def remove_mapping_entry(
    config: dict[str, Any], username: str, mapping_path: Path
) -> None:
    users = config.get("users") or []
    if not isinstance(users, list):
        raise ProvisionError("users_mapping.yaml has invalid users structure.")
    filtered = [
        item
        for item in users
        if not (isinstance(item, dict) and item.get("username") == username)
    ]
    config["users"] = filtered
    mapping_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=False), encoding="utf-8"
    )


def verify_signin_rejected(email: str, password: str) -> None:
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:3000/api/v1/auths/signin",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20):
            raise ProvisionError(
                "Unexpected successful sign-in during removal verification."
            )
    except urllib.error.HTTPError as exc:
        if exc.code not in {400, 401}:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProvisionError(
                f"Unexpected Open WebUI sign-in response: {detail}"
            ) from exc


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        require_root()
        require_paths(args.mapping, args.openwebui_db)
        raw_config = load_mapping(args.mapping)
        config = resolve_env_placeholders(raw_config, str(args.mapping))
        spec = resolve_user(config, args.username)

        with sqlite3.connect(args.openwebui_db) as conn:
            remove_openwebui_binding(conn, spec, keep_user=args.keep_openwebui_user)

        stop_and_remove_service(spec.systemd_service)
        remove_linux_user(spec.linux_user, delete_home=args.delete_home)
        remove_mapping_entry(raw_config, args.username, args.mapping)

        restart_openwebui(args.openwebui_service)
        wait_for_openwebui()
        if not args.keep_openwebui_user:
            verify_signin_rejected(spec.webui_user, args.password)

    except (ProvisionError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Deprovisioned username: {spec.username}")
    print(f"Removed Hermes service: {spec.systemd_service}")
    print(f"Removed wrapper model: {spec.model_id}")
    if args.keep_openwebui_user:
        print(f"Kept Open WebUI user: {spec.webui_user}")
    else:
        print(f"Removed Open WebUI user: {spec.webui_user}")
    print(f"Removed Linux user: {spec.linux_user}")
    print(f"Deleted home directory: {args.delete_home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

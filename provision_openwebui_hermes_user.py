#!/usr/bin/env python3
"""Provision one Open WebUI user bound to one Linux-backed Hermes instance.

This script turns the current multi-step process into a single command that:
- creates or updates the Open WebUI user from username/email/password
- appends or updates the corresponding entry in users_mapping.yaml
- creates the Linux user and Hermes home/workdir
- installs and starts the per-user Hermes systemd service
- updates Open WebUI's persisted OpenAI connection config
- imports the user's private wrapper model and access grant
- restarts Open WebUI and verifies sign-in
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import yaml

from generate_multiuser_bundle import (
    BUILD_WRAPPER_IMPORT_PAYLOAD,
    GENERATE_WRAPPER_PAYLOAD,
    DEFAULT_CONNECTION_TYPE,
    DEFAULT_START_PORT,
    ConfigError,
    build_config_data,
    build_env_content,
    build_systemd_unit,
    build_user_specs,
    build_wrapper_config,
    get_env_placeholder_name,
    load_mapping,
    resolve_env_placeholders,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_MAPPING_PATH = ROOT / "users_mapping.yaml"
DEFAULT_OPENWEBUI_DB = Path("/opt/open-webui-data/webui.db")
DEFAULT_OPENWEBUI_PYTHON = Path("/opt/open-webui-venv/bin/python")
DEFAULT_OPENWEBUI_SERVICE = "open-webui.service"
DEFAULT_HERMES_BIN = Path("/usr/local/bin/hermes")
DEFAULT_PROFILE_IMAGE_URL = "/user.png"


class ProvisionError(RuntimeError):
    pass


def require_root() -> None:
    if os.geteuid() != 0:
        raise ProvisionError("This script must be run as root.")


def run_command(command: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise ProvisionError(f"Command failed ({' '.join(command)}): {detail}")
    return result.stdout


def slugify_username(username: str) -> str:
    return username.replace("_", "-")


def select_next_port(config: dict[str, Any]) -> int:
    start_port = int(config.get("start_port") or DEFAULT_START_PORT)
    used_ports = {
        int(user.get("api_port"))
        for user in config.get("users", [])
        if isinstance(user, dict) and user.get("api_port") is not None
    }
    port = start_port
    while port in used_ports:
        port += 1
    return port


def hash_password(password: str, python_path: Path) -> str:
    script = (
        "import bcrypt, sys; "
        "print(bcrypt.hashpw(sys.argv[1].encode('utf-8'), bcrypt.gensalt()).decode('utf-8'))"
    )
    return run_command([str(python_path), "-c", script, password]).strip()


def fetchone(
    conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()
) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute(query, params).fetchone()


def get_config_row(conn: sqlite3.Connection) -> tuple[int, dict[str, Any]]:
    row = fetchone(conn, "select id, data from config order by id desc limit 1")
    if row is None:
        raise ProvisionError("Open WebUI config row not found.")
    return int(row["id"]), json.loads(row["data"])


def ensure_group_membership(
    conn: sqlite3.Connection, group_id: str | None, user_id: str
) -> None:
    if not group_id:
        return
    existing = fetchone(
        conn,
        "select id from group_member where group_id=? and user_id=?",
        (group_id, user_id),
    )
    if existing is not None:
        return
    now = int(time.time())
    conn.execute(
        "insert into group_member (id, group_id, user_id, created_at, updated_at) values (?,?,?,?,?)",
        (str(uuid.uuid4()), group_id, user_id, now, now),
    )


def ensure_openwebui_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    email: str,
    password_hash: str,
    role: str,
) -> str:
    email = email.lower()
    user_by_email = fetchone(
        conn, "select * from user where lower(email)=lower(?)", (email,)
    )
    user_by_username = fetchone(
        conn, "select * from user where username=?", (username,)
    )

    user_row = None
    if (
        user_by_email
        and user_by_username
        and user_by_email["id"] != user_by_username["id"]
    ):
        raise ProvisionError(
            f"Email {email!r} and username {username!r} already belong to different Open WebUI users."
        )
    if user_by_email is not None:
        user_row = user_by_email
    elif user_by_username is not None:
        user_row = user_by_username

    config_id, config = get_config_row(conn)
    _ = config_id
    default_group_id = ((config.get("ui") or {}).get("default_group_id")) or None
    now = int(time.time())

    if user_row is None:
        user_id = str(uuid.uuid4())
        conn.execute(
            "insert into auth (id, email, password, active) values (?,?,?,1)",
            (user_id, email, password_hash),
        )
        conn.execute(
            "insert into user (id, email, username, role, name, profile_image_url, last_active_at, updated_at, created_at) values (?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                email,
                username,
                role,
                username,
                DEFAULT_PROFILE_IMAGE_URL,
                now,
                now,
                now,
            ),
        )
    else:
        user_id = str(user_row["id"])
        conn.execute(
            "update auth set email=?, password=?, active=1 where id=?",
            (email, password_hash, user_id),
        )
        conn.execute(
            "update user set email=?, username=?, role=?, name=?, updated_at=? where id=?",
            (email, username, role, username, now, user_id),
        )

    ensure_group_membership(conn, default_group_id, user_id)
    conn.commit()
    return user_id


def ensure_user_mapping_entry(
    config: dict[str, Any],
    *,
    username: str,
    email: str,
    openwebui_user_id: str,
    api_key: str | None,
) -> dict[str, Any]:
    users = config.setdefault("users", [])
    if not isinstance(users, list):
        raise ProvisionError("users_mapping.yaml has invalid users structure.")

    entry = None
    for item in users:
        if isinstance(item, dict) and item.get("username") == username:
            entry = item
            break

    slug = slugify_username(username)
    shared_api_key_placeholder = infer_shared_api_key_placeholder(config)
    if entry is None:
        entry = {
            "username": username,
            "linux_user": f"hmx_{username}",
            "home_dir": f"/home/hmx_{username}",
            "hermes_home": f"/home/hmx_{username}/.hermes",
            "workdir": f"/home/hmx_{username}/work",
            "api_port": select_next_port(config),
            "api_server_model_name": "Hermes",
            "connection_prefix": f"hermes-{slug}",
            "model_id": f"hermes-{slug}",
            "model_name": "Hermes",
            "systemd_service": f"hermes-{slug}.service",
            "openwebui_tags": [username],
        }
        users.append(entry)

    entry["webui_user"] = email
    entry["webui_display_name"] = username
    entry["openwebui_user_id"] = openwebui_user_id
    entry.setdefault("api_server_model_name", "Hermes")
    entry.setdefault("model_name", "Hermes")
    entry.setdefault("linux_user", f"hmx_{username}")
    entry.setdefault("home_dir", f"/home/{entry['linux_user']}")
    entry.setdefault("hermes_home", f"{entry['home_dir']}/.hermes")
    entry.setdefault("workdir", f"{entry['home_dir']}/work")
    entry.setdefault("connection_prefix", f"hermes-{slug}")
    entry.setdefault("model_id", f"hermes-{slug}")
    entry.setdefault("systemd_service", f"hermes-{slug}.service")
    entry.setdefault("openwebui_tags", [username])
    if api_key:
        entry["api_key"] = api_key
    else:
        entry.setdefault(
            "api_key", shared_api_key_placeholder or secrets.token_urlsafe(24)
        )
    return entry


def infer_shared_api_key_placeholder(config: dict[str, Any]) -> str | None:
    candidates: list[Any] = []

    hermes_cfg = config.get("hermes")
    if isinstance(hermes_cfg, dict):
        model_cfg = hermes_cfg.get("model")
        if isinstance(model_cfg, dict):
            candidates.append(model_cfg.get("api_key"))

        extra_env = hermes_cfg.get("extra_env")
        if isinstance(extra_env, dict):
            candidates.append(extra_env.get("OPENAI_API_KEY"))

    users = config.get("users")
    if isinstance(users, list):
        for item in users:
            if isinstance(item, dict):
                candidates.append(item.get("api_key"))

    for candidate in candidates:
        if get_env_placeholder_name(candidate):
            return str(candidate).strip()
    return None


def write_mapping_file(config: dict[str, Any], path: Path) -> None:
    path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=False), encoding="utf-8"
    )


def ensure_linux_user(username: str) -> None:
    result = subprocess.run(["id", "-u", username], capture_output=True, text=True)
    if result.returncode == 0:
        return
    run_command(["useradd", "-m", "-s", "/bin/bash", username])


def set_owner_and_mode(path: Path, uid: int, gid: int, mode: int) -> None:
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def install_user_files(config: dict[str, Any], spec) -> None:
    import grp
    import pwd

    ensure_linux_user(spec.linux_user)
    pw = pwd.getpwnam(spec.linux_user)
    gid = grp.getgrnam(spec.linux_user).gr_gid

    for directory in [
        spec.home_dir,
        spec.workdir,
        spec.hermes_home,
        spec.hermes_home / "home",
    ]:
        directory.mkdir(parents=True, exist_ok=True)
        set_owner_and_mode(directory, pw.pw_uid, gid, 0o700)

    env_path = spec.hermes_home / ".env"
    env_path.write_text(build_env_content(spec), encoding="utf-8")
    set_owner_and_mode(env_path, pw.pw_uid, gid, 0o600)

    config_path = spec.hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            build_config_data(config, spec), sort_keys=False, allow_unicode=False
        ),
        encoding="utf-8",
    )
    set_owner_and_mode(config_path, pw.pw_uid, gid, 0o600)

    service_path = Path("/etc/systemd/system") / spec.systemd_service
    service_path.write_text(build_systemd_unit(config, spec), encoding="utf-8")
    os.chmod(service_path, 0o644)

    run_command(["systemctl", "daemon-reload"])
    run_command(["systemctl", "enable", "--now", spec.systemd_service])


def ensure_openwebui_connection(
    conn: sqlite3.Connection, config: dict[str, Any], spec
) -> None:
    config_id, persisted = get_config_row(conn)
    openai_cfg = persisted.setdefault("openai", {})
    base_urls = list(openai_cfg.get("api_base_urls") or [])
    api_keys = list(openai_cfg.get("api_keys") or [])
    api_configs = dict(openai_cfg.get("api_configs") or {})

    target_index = None
    for key, value in api_configs.items():
        if not isinstance(value, dict):
            continue
        if value.get("prefix_id") == spec.connection_prefix:
            target_index = int(key)
            break

    if target_index is None:
        for index, url in enumerate(base_urls):
            if url == spec.connection_url:
                target_index = index
                break

    if target_index is None:
        target_index = len(base_urls)
        base_urls.append(spec.connection_url)
        api_keys.append(spec.api_key)
    else:
        while len(base_urls) <= target_index:
            base_urls.append("")
        while len(api_keys) <= target_index:
            api_keys.append("")
        base_urls[target_index] = spec.connection_url
        api_keys[target_index] = spec.api_key

    openwebui_cfg = config.get("open_webui") or {}
    api_configs[str(target_index)] = {
        "enable": True,
        "tags": spec.openwebui_tags,
        "prefix_id": spec.connection_prefix,
        "model_ids": [],
        "connection_type": str(
            openwebui_cfg.get("connection_type") or DEFAULT_CONNECTION_TYPE
        ),
        "auth_type": "bearer",
    }

    openai_cfg["enable"] = True
    openai_cfg["api_base_urls"] = base_urls
    openai_cfg["api_keys"] = api_keys
    openai_cfg["api_configs"] = api_configs

    conn.execute(
        "update config set data=?, updated_at=CURRENT_TIMESTAMP where id=?",
        (json.dumps(persisted), config_id),
    )
    conn.commit()


def ensure_wrapper_model(
    conn: sqlite3.Connection, config: dict[str, Any], spec
) -> None:
    wrapper_config = build_wrapper_config(config, [spec])
    wrapper_full = GENERATE_WRAPPER_PAYLOAD(wrapper_config)
    wrapper_import = BUILD_WRAPPER_IMPORT_PAYLOAD(wrapper_full)
    if not wrapper_import.get("models"):
        raise ProvisionError("Wrapper payload generation returned no models.")

    model = wrapper_import["models"][0]
    openwebui_cfg = config.get("open_webui") or {}
    owner_user_id = str(openwebui_cfg.get("wrapper_owner_user_id") or "system")
    now = int(time.time())

    conn.execute(
        "delete from access_grant where resource_type='model' and resource_id=?",
        (model["id"],),
    )
    existing = fetchone(conn, "select id from model where id=?", (model["id"],))
    meta_json = json.dumps(model.get("meta", {}), ensure_ascii=False)
    params_json = json.dumps(model.get("params", {}), ensure_ascii=False)
    is_active = 1 if model.get("is_active", True) else 0

    if existing is None:
        conn.execute(
            "insert into model (id,user_id,base_model_id,name,meta,params,created_at,updated_at,is_active) values (?,?,?,?,?,?,?,?,?)",
            (
                model["id"],
                owner_user_id,
                model.get("base_model_id"),
                model.get("name"),
                meta_json,
                params_json,
                now,
                now,
                is_active,
            ),
        )
    else:
        conn.execute(
            "update model set user_id=?, base_model_id=?, name=?, meta=?, params=?, is_active=?, updated_at=? where id=?",
            (
                owner_user_id,
                model.get("base_model_id"),
                model.get("name"),
                meta_json,
                params_json,
                is_active,
                now,
                model["id"],
            ),
        )

    for grant in model.get("access_grants", []):
        conn.execute(
            "insert into access_grant (id,resource_type,resource_id,principal_type,principal_id,permission,created_at) values (?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                "model",
                model["id"],
                grant["principal_type"],
                grant["principal_id"],
                grant["permission"],
                now,
            ),
        )
    conn.commit()


def restart_openwebui(service_name: str) -> None:
    run_command(["systemctl", "restart", service_name])


def wait_for_openwebui(timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    url = "http://127.0.0.1:3000/health"
    last_error = "unknown error"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("status") is True:
                    return
                last_error = f"unexpected health payload: {payload!r}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise ProvisionError(f"Open WebUI did not become healthy in time: {last_error}")


def verify_openwebui_signin(email: str, password: str) -> dict[str, Any]:
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:3000/api/v1/auths/signin",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProvisionError(
            f"Open WebUI sign-in verification failed: {detail}"
        ) from exc


def verify_hermes_models(api_key: str, port: int) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def require_paths(args: argparse.Namespace) -> None:
    if not args.mapping.exists():
        raise ProvisionError(f"Mapping file not found: {args.mapping}")
    if not args.openwebui_db.exists():
        raise ProvisionError(f"Open WebUI database not found: {args.openwebui_db}")
    if not args.openwebui_python.exists():
        raise ProvisionError(f"Open WebUI Python not found: {args.openwebui_python}")
    if not args.hermes_bin.exists():
        raise ProvisionError(f"Hermes binary not found: {args.hermes_bin}")
    if not shutil.which("systemctl"):
        raise ProvisionError("systemctl is required.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create one Open WebUI user and bind it to one Linux-backed Hermes instance."
    )
    parser.add_argument("username", help="Short username, e.g. alice or user_test")
    parser.add_argument("email", help="Open WebUI login email")
    parser.add_argument("password", help="Open WebUI login password")
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
        "--openwebui-python",
        type=Path,
        default=DEFAULT_OPENWEBUI_PYTHON,
        help=f"Path to Open WebUI Python interpreter (default: {DEFAULT_OPENWEBUI_PYTHON})",
    )
    parser.add_argument(
        "--hermes-bin",
        type=Path,
        default=DEFAULT_HERMES_BIN,
        help=f"Path to shared Hermes executable (default: {DEFAULT_HERMES_BIN})",
    )
    parser.add_argument(
        "--openwebui-service",
        default=DEFAULT_OPENWEBUI_SERVICE,
        help=f"systemd service name for Open WebUI (default: {DEFAULT_OPENWEBUI_SERVICE})",
    )
    parser.add_argument(
        "--role",
        default="user",
        choices=["user", "pending", "admin"],
        help="Open WebUI role to assign (default: user)",
    )
    parser.add_argument(
        "--api-key",
        help="Optional fixed Hermes API server key. Default is to reuse existing mapping value or generate a random key.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        require_root()
        require_paths(args)

        password_hash = hash_password(args.password, args.openwebui_python)

        with sqlite3.connect(args.openwebui_db) as conn:
            user_id = ensure_openwebui_user(
                conn,
                username=args.username,
                email=args.email,
                password_hash=password_hash,
                role=args.role,
            )

        raw_config = load_mapping(args.mapping)
        raw_config.setdefault("hermes", {})["executable"] = str(args.hermes_bin)
        ensure_user_mapping_entry(
            raw_config,
            username=args.username,
            email=args.email,
            openwebui_user_id=user_id,
            api_key=args.api_key,
        )
        config = resolve_env_placeholders(raw_config, str(args.mapping))

        specs = build_user_specs(config)
        spec = next((item for item in specs if item.username == args.username), None)
        if spec is None:
            raise ProvisionError(
                f"Failed to resolve generated spec for {args.username}."
            )

        write_mapping_file(raw_config, args.mapping)

        install_user_files(config, spec)

        with sqlite3.connect(args.openwebui_db) as conn:
            ensure_openwebui_connection(conn, config, spec)
            ensure_wrapper_model(conn, config, spec)

        restart_openwebui(args.openwebui_service)
        wait_for_openwebui()
        signin_response = verify_openwebui_signin(args.email, args.password)
        hermes_models = verify_hermes_models(spec.api_key, spec.api_port)

    except (ProvisionError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Provisioned Open WebUI user: {args.email}")
    print(f"Open WebUI user id: {user_id}")
    print(f"Linux user: {spec.linux_user}")
    print(f"Hermes service: {spec.systemd_service}")
    print(f"Hermes URL: {spec.connection_url}")
    print(f"Hermes API key: {spec.api_key}")
    print(f"Open WebUI wrapper model: {spec.model_id} ({spec.model_name})")
    print(f"Open WebUI sign-in verified for: {signin_response.get('email')}")
    print(
        f"Hermes advertised models: {[item.get('id') for item in hermes_models.get('data', [])]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

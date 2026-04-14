#!/usr/bin/env python3
"""Generate a Linux-user-based Hermes + Open WebUI deployment bundle.

The bundle is driven by a single users_mapping YAML file and produces:
- per-user Hermes `.env`
- per-user Hermes `config.yaml`
- per-user systemd units
- a root-only apply script that creates Linux users and installs the files
- Open WebUI connection planning data
- Open WebUI wrapper-model import payloads
- a deployment summary and verification checklist
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import secrets
import shlex
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
OPEN_WEBUI_HELPER = (
    ROOT
    / "open-webui"
    / "backend"
    / "open_webui"
    / "tools"
    / "hermes_model_wrapper_helper.py"
)


def _load_openwebui_helper() -> tuple[Any, Any]:
    spec = importlib.util.spec_from_file_location(
        "hermes_model_wrapper_helper",
        OPEN_WEBUI_HELPER,
    )
    if spec is None or spec.loader is None:
        raise ConfigError(f"Could not load helper module from {OPEN_WEBUI_HELPER}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_payload, module.build_import_payload


GENERATE_WRAPPER_PAYLOAD, BUILD_WRAPPER_IMPORT_PAYLOAD = _load_openwebui_helper()

DEFAULT_API_SERVER_HOST = "127.0.0.1"
DEFAULT_API_SERVER_MODEL_NAME = "Hermes"
DEFAULT_MODEL_NAME = "Hermes"
DEFAULT_HERMES_BIN = "/usr/local/bin/hermes"
DEFAULT_START_PORT = 8643
DEFAULT_API_KEY_BYTES = 24
DEFAULT_TERMINAL_TIMEOUT = 180
DEFAULT_CONNECTION_TYPE = "external"
DEFAULT_SERVICE_RESTART = "always"
DEFAULT_SERVICE_RESTART_SEC = 5

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
LINUX_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class UserSpec:
    username: str
    webui_user: str
    webui_display_name: str
    openwebui_user_id: str
    linux_user: str
    home_dir: Path
    hermes_home: Path
    workdir: Path
    api_server_host: str
    api_port: int
    api_key: str
    api_server_model_name: str
    model_id: str
    model_name: str
    connection_prefix: str
    systemd_service: str
    extra_env: dict[str, str]
    config_overrides: dict[str, Any]
    openwebui_tags: list[str]

    @property
    def connection_url(self) -> str:
        return f"http://{self.api_server_host}:{self.api_port}/v1"

    @property
    def base_model_id(self) -> str:
        if self.connection_prefix:
            return f"{self.connection_prefix}.{self.api_server_model_name}"
        return self.api_server_model_name


def get_env_placeholder_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = ENV_PLACEHOLDER_RE.fullmatch(value.strip())
    if match is None:
        return None
    return match.group(1)


def resolve_env_placeholders(value: Any, field_name: str = "mapping") -> Any:
    if isinstance(value, dict):
        return {
            key: resolve_env_placeholders(subvalue, f"{field_name}.{key}")
            for key, subvalue in value.items()
        }
    if isinstance(value, list):
        return [
            resolve_env_placeholders(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]

    env_name = get_env_placeholder_name(value)
    if env_name is None:
        return value

    env_value = os.getenv(env_name)
    if not env_value:
        raise ConfigError(
            f"{field_name} references environment variable {env_name!r}, but it is not set."
        )
    return env_value


def load_mapping(path: Path, *, resolve_env: bool = False) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Mapping file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Top-level YAML structure must be a mapping/object.")
    if resolve_env:
        data = resolve_env_placeholders(data, str(path))
    return data


def require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ConfigError(f"{field_name} must be an object/mapping when provided.")


def require_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ConfigError(f"{field_name} must be a list when provided.")


def require_str(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} must be a string.")
    text = value.strip()
    if not allow_empty and not text:
        raise ConfigError(f"{field_name} must be a non-empty string.")
    return text


def require_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field_name} must be an integer.")
    return value


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


def shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def format_env_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]+", value):
        return value
    return json.dumps(value)


def validate_username(value: str, field_name: str) -> str:
    if not USERNAME_RE.fullmatch(value):
        raise ConfigError(
            f"{field_name} must match [a-z][a-z0-9_-]{{1,30}}; got {value!r}."
        )
    return value


def validate_linux_user(value: str, field_name: str) -> str:
    if not LINUX_USER_RE.fullmatch(value):
        raise ConfigError(
            f"{field_name} must be a valid Linux username; got {value!r}."
        )
    return value


def validate_prefix(value: str, field_name: str) -> str:
    if not PREFIX_RE.fullmatch(value):
        raise ConfigError(
            f"{field_name} must contain only letters, digits, '.', '_' or '-'; got {value!r}."
        )
    return value


def next_available_port(start_port: int, used_ports: set[int]) -> int:
    port = start_port
    while port in used_ports:
        port += 1
    return port


def build_user_specs(config: dict[str, Any]) -> list[UserSpec]:
    users = require_list(config.get("users"), "users")
    if not users:
        raise ConfigError("users must be a non-empty list.")

    hermes_cfg = require_mapping(config.get("hermes"), "hermes")
    openwebui_cfg = require_mapping(config.get("open_webui"), "open_webui")

    start_port = config.get("start_port", DEFAULT_START_PORT)
    if not isinstance(start_port, int):
        raise ConfigError("start_port must be an integer when provided.")

    used_ports = {
        require_int(user["api_port"], f"users[{index}].api_port")
        for index, user in enumerate(users, start=1)
        if isinstance(user, dict) and "api_port" in user
    }

    specs: list[UserSpec] = []
    seen_usernames: set[str] = set()
    seen_linux_users: set[str] = set()
    seen_model_ids: set[str] = set()
    seen_connection_prefixes: set[str] = set()
    seen_ports: set[int] = set()

    global_extra_env = {
        require_str(key, "hermes.extra_env key"): require_str(
            value, f"hermes.extra_env[{key}]"
        )
        for key, value in require_mapping(
            hermes_cfg.get("extra_env"), "hermes.extra_env"
        ).items()
    }
    global_tags = [
        require_str(tag, "open_webui.default_tags")
        for tag in require_list(
            openwebui_cfg.get("default_tags"), "open_webui.default_tags"
        )
    ]

    for index, raw_user in enumerate(users, start=1):
        if not isinstance(raw_user, dict):
            raise ConfigError(f"users[{index}] must be an object/mapping.")

        username = validate_username(
            require_str(raw_user.get("username"), f"users[{index}].username"),
            f"users[{index}].username",
        )
        if username in seen_usernames:
            raise ConfigError(f"Duplicate username in mapping: {username}")
        seen_usernames.add(username)

        linux_user = validate_linux_user(
            str(raw_user.get("linux_user") or f"hmx_{username}"),
            f"users[{index}].linux_user",
        )
        if linux_user in seen_linux_users:
            raise ConfigError(f"Duplicate linux_user in mapping: {linux_user}")
        seen_linux_users.add(linux_user)

        home_dir = Path(str(raw_user.get("home_dir") or f"/home/{linux_user}"))
        hermes_home = Path(str(raw_user.get("hermes_home") or (home_dir / ".hermes")))
        workdir = Path(str(raw_user.get("workdir") or (home_dir / "work")))

        port = raw_user.get("api_port")
        if port is None:
            port = next_available_port(start_port, seen_ports | used_ports)
        port = require_int(port, f"users[{index}].api_port")
        if port in seen_ports:
            raise ConfigError(f"Duplicate api_port in mapping: {port}")
        seen_ports.add(port)

        api_key = str(
            raw_user.get("api_key") or secrets.token_urlsafe(DEFAULT_API_KEY_BYTES)
        )
        api_server_host = str(
            raw_user.get("api_server_host")
            or hermes_cfg.get("api_server_host")
            or DEFAULT_API_SERVER_HOST
        )
        api_server_model_name = str(
            raw_user.get("api_server_model_name")
            or hermes_cfg.get("api_server_model_name")
            or DEFAULT_API_SERVER_MODEL_NAME
        )
        model_id = str(raw_user.get("model_id") or f"hermes-{username}")
        if model_id in seen_model_ids:
            raise ConfigError(f"Duplicate model_id in mapping: {model_id}")
        seen_model_ids.add(model_id)

        model_name = str(raw_user.get("model_name") or DEFAULT_MODEL_NAME)
        connection_prefix = validate_prefix(
            str(raw_user.get("connection_prefix") or model_id),
            f"users[{index}].connection_prefix",
        )
        if connection_prefix in seen_connection_prefixes:
            raise ConfigError(
                f"Duplicate connection_prefix in mapping: {connection_prefix}"
            )
        seen_connection_prefixes.add(connection_prefix)

        systemd_service = str(
            raw_user.get("systemd_service") or f"hermes-{username}.service"
        )
        webui_user = str(raw_user.get("webui_user") or "")
        webui_display_name = str(
            raw_user.get("webui_display_name") or username.capitalize()
        )
        openwebui_user_id = require_str(
            raw_user.get("openwebui_user_id"),
            f"users[{index}].openwebui_user_id",
        )

        user_extra_env = {
            require_str(key, f"users[{index}].extra_env key"): require_str(
                value, f"users[{index}].extra_env[{key}]"
            )
            for key, value in require_mapping(
                raw_user.get("extra_env"), f"users[{index}].extra_env"
            ).items()
        }
        config_overrides = require_mapping(
            raw_user.get("config_overrides"),
            f"users[{index}].config_overrides",
        )
        user_tags = [
            require_str(tag, f"users[{index}].openwebui_tags")
            for tag in require_list(
                raw_user.get("openwebui_tags"), f"users[{index}].openwebui_tags"
            )
        ]

        specs.append(
            UserSpec(
                username=username,
                webui_user=webui_user,
                webui_display_name=webui_display_name,
                openwebui_user_id=openwebui_user_id,
                linux_user=linux_user,
                home_dir=home_dir,
                hermes_home=hermes_home,
                workdir=workdir,
                api_server_host=api_server_host,
                api_port=port,
                api_key=api_key,
                api_server_model_name=api_server_model_name,
                model_id=model_id,
                model_name=model_name,
                connection_prefix=connection_prefix,
                systemd_service=systemd_service,
                extra_env={**global_extra_env, **user_extra_env},
                config_overrides=config_overrides,
                openwebui_tags=list(
                    dict.fromkeys([*global_tags, *user_tags, username])
                ),
            )
        )

    return specs


def build_env_content(user: UserSpec) -> str:
    lines = [
        f"# Open WebUI user: {user.webui_user or user.webui_display_name}",
        "API_SERVER_ENABLED=true",
        f"API_SERVER_HOST={format_env_value(user.api_server_host)}",
        f"API_SERVER_PORT={user.api_port}",
        f"API_SERVER_KEY={format_env_value(user.api_key)}",
        f"API_SERVER_MODEL_NAME={format_env_value(user.api_server_model_name)}",
    ]
    for key, value in user.extra_env.items():
        lines.append(f"{key}={format_env_value(value)}")
    return "\n".join(lines).rstrip() + "\n"


def build_config_data(config: dict[str, Any], user: UserSpec) -> dict[str, Any]:
    hermes_cfg = require_mapping(config.get("hermes"), "hermes")
    terminal_cfg = deepcopy(
        require_mapping(hermes_cfg.get("terminal"), "hermes.terminal")
    )
    model_cfg = deepcopy(require_mapping(hermes_cfg.get("model"), "hermes.model"))
    global_overrides = deepcopy(
        require_mapping(hermes_cfg.get("config_overrides"), "hermes.config_overrides")
    )

    data: dict[str, Any] = {}
    if model_cfg:
        data["model"] = model_cfg
    data["terminal"] = terminal_cfg
    data["agent"] = {"reasoning_effort": "high"}

    deep_merge(data, global_overrides)
    deep_merge(data, deepcopy(user.config_overrides))

    terminal = data.setdefault("terminal", {})
    if not isinstance(terminal, dict):
        raise ConfigError(f"terminal config for {user.username} must remain a mapping.")

    terminal.setdefault("backend", "local")
    terminal.setdefault("timeout", DEFAULT_TERMINAL_TIMEOUT)
    terminal["cwd"] = str(user.workdir)
    return data


def build_systemd_unit(config: dict[str, Any], user: UserSpec) -> str:
    hermes_cfg = require_mapping(config.get("hermes"), "hermes")
    service_cfg = require_mapping(hermes_cfg.get("service"), "hermes.service")

    description = str(
        service_cfg.get("description_template") or "Hermes Agent for {display_name}"
    )
    restart = str(service_cfg.get("restart") or DEFAULT_SERVICE_RESTART)
    restart_sec = int(service_cfg.get("restart_sec") or DEFAULT_SERVICE_RESTART_SEC)
    hermes_bin = str(hermes_cfg.get("executable") or DEFAULT_HERMES_BIN)

    rendered_description = description.format(
        username=user.username,
        display_name=user.webui_display_name,
        linux_user=user.linux_user,
    )

    return "\n".join(
        [
            "[Unit]",
            f"Description={rendered_description}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={user.linux_user}",
            f"Group={user.linux_user}",
            f"WorkingDirectory={user.home_dir}",
            f"Environment=HOME={user.home_dir}",
            f"Environment=HERMES_HOME={user.hermes_home}",
            f"ExecStart={hermes_bin} gateway run --replace",
            f"Restart={restart}",
            f"RestartSec={restart_sec}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def build_openwebui_connections(
    config: dict[str, Any], specs: list[UserSpec]
) -> dict[str, Any]:
    openwebui_cfg = require_mapping(config.get("open_webui"), "open_webui")
    connection_type = str(
        openwebui_cfg.get("connection_type") or DEFAULT_CONNECTION_TYPE
    )

    return {
        "connections": [
            {
                "label": f"Hermes for {user.webui_display_name}",
                "url": user.connection_url,
                "key": user.api_key,
                "config": {
                    "enable": True,
                    "connection_type": connection_type,
                    "prefix_id": user.connection_prefix,
                    "tags": user.openwebui_tags,
                },
                "advertised_model_name": user.api_server_model_name,
                "resolved_base_model_id": user.base_model_id,
                "wrapper_model_id": user.model_id,
                "wrapper_model_name": user.model_name,
            }
            for user in specs
        ]
    }


def build_wrapper_config(
    config: dict[str, Any], specs: list[UserSpec]
) -> dict[str, Any]:
    openwebui_cfg = require_mapping(config.get("open_webui"), "open_webui")
    meta_defaults = deepcopy(
        require_mapping(
            openwebui_cfg.get("wrapper_meta_defaults"),
            "open_webui.wrapper_meta_defaults",
        )
    )
    params_defaults = deepcopy(
        require_mapping(
            openwebui_cfg.get("wrapper_params_defaults"),
            "open_webui.wrapper_params_defaults",
        )
    )
    default_tag_names = {
        item.get("name")
        for item in require_list(
            meta_defaults.get("tags"), "open_webui.wrapper_meta_defaults.tags"
        )
        if isinstance(item, dict) and item.get("name")
    }

    return {
        "base_model_id": DEFAULT_API_SERVER_MODEL_NAME,
        "owner_user_id": str(openwebui_cfg.get("wrapper_owner_user_id") or "system"),
        "model_id_prefix": "hermes-",
        "model_name_template": DEFAULT_MODEL_NAME,
        "description_template": str(
            openwebui_cfg.get("wrapper_description_template")
            or "Private Hermes wrapper for {target_label}"
        ),
        "meta_defaults": meta_defaults,
        "params_defaults": params_defaults,
        "users": [
            {
                "user_id": user.openwebui_user_id,
                "label": user.webui_display_name,
                "model_id": user.model_id,
                "name": user.model_name,
                "base_model_id": user.base_model_id,
                "include_target_user_read": True,
                "include_target_user_write": False,
                "meta": {
                    "tags": [
                        {"name": tag}
                        for tag in user.openwebui_tags
                        if tag not in default_tag_names
                    ]
                },
            }
            for user in specs
        ],
    }


def build_summary(specs: list[UserSpec]) -> dict[str, Any]:
    return {
        "users": [
            {
                "username": user.username,
                "webui_user": user.webui_user,
                "webui_display_name": user.webui_display_name,
                "openwebui_user_id": user.openwebui_user_id,
                "linux_user": user.linux_user,
                "home_dir": str(user.home_dir),
                "hermes_home": str(user.hermes_home),
                "workdir": str(user.workdir),
                "api_server_host": user.api_server_host,
                "api_port": user.api_port,
                "api_key": user.api_key,
                "api_server_model_name": user.api_server_model_name,
                "connection_url": user.connection_url,
                "connection_prefix": user.connection_prefix,
                "base_model_id": user.base_model_id,
                "model_id": user.model_id,
                "model_name": user.model_name,
                "systemd_service": user.systemd_service,
            }
            for user in specs
        ]
    }


def build_checklist(specs: list[UserSpec]) -> str:
    lines = [
        "# Multi-user deployment checklist",
        "",
        "Run the generated `apply_host.sh` as root, then validate each user below.",
        "",
    ]
    for user in specs:
        lines.extend(
            [
                f"## {user.username}",
                "",
                f"- [ ] Linux user `{user.linux_user}` exists",
                f"- [ ] `{user.workdir}` exists and is owned by `{user.linux_user}`",
                f"- [ ] `{user.hermes_home}` exists and is owned by `{user.linux_user}`",
                f"- [ ] systemd unit `{user.systemd_service}` is installed and active",
                f'- [ ] `curl -H "Authorization: Bearer {user.api_key}" {user.connection_url}/models` succeeds',
                f"- [ ] Open WebUI connection is configured with `prefix_id={user.connection_prefix}`",
                f"- [ ] Open WebUI base model resolves as `{user.base_model_id}`",
                f"- [ ] Wrapper model `{user.model_id}` is imported with visible name `{user.model_name}`",
                f"- [ ] Wrapper model `{user.model_id}` is only granted to Open WebUI user `{user.openwebui_user_id}`",
                f"- [ ] Running `pwd` through Hermes lands in `{user.workdir}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def build_apply_script(specs: list[UserSpec]) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
        '[[ "$(id -u)" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }',
        'command -v useradd >/dev/null 2>&1 || { echo "Missing useradd" >&2; exit 1; }',
        'command -v install >/dev/null 2>&1 || { echo "Missing install" >&2; exit 1; }',
        'command -v systemctl >/dev/null 2>&1 || { echo "Missing systemctl" >&2; exit 1; }',
        "",
    ]

    for user in specs:
        bundle_env = Path("users") / user.username / ".hermes" / ".env"
        bundle_config = Path("users") / user.username / ".hermes" / "config.yaml"
        bundle_service = Path("systemd") / user.systemd_service
        lines.extend(
            [
                f"id -u {shell_quote(user.linux_user)} >/dev/null 2>&1 || useradd -m -s /bin/bash {shell_quote(user.linux_user)}",
                f"install -d -m 700 -o {shell_quote(user.linux_user)} -g {shell_quote(user.linux_user)} {shell_quote(user.home_dir)}",
                f"install -d -m 700 -o {shell_quote(user.linux_user)} -g {shell_quote(user.linux_user)} {shell_quote(user.workdir)}",
                f"install -d -m 700 -o {shell_quote(user.linux_user)} -g {shell_quote(user.linux_user)} {shell_quote(user.hermes_home)}",
                f"install -d -m 700 -o {shell_quote(user.linux_user)} -g {shell_quote(user.linux_user)} {shell_quote(user.hermes_home / 'home')}",
                f'install -m 600 -o {shell_quote(user.linux_user)} -g {shell_quote(user.linux_user)} "$SCRIPT_DIR/{bundle_env.as_posix()}" {shell_quote(user.hermes_home / ".env")}',
                f'install -m 600 -o {shell_quote(user.linux_user)} -g {shell_quote(user.linux_user)} "$SCRIPT_DIR/{bundle_config.as_posix()}" {shell_quote(user.hermes_home / "config.yaml")}',
                f'install -m 644 "$SCRIPT_DIR/{bundle_service.as_posix()}" {shell_quote(Path("/etc/systemd/system") / user.systemd_service)}',
                "",
            ]
        )

    lines.append("systemctl daemon-reload")
    for user in specs:
        lines.append(f"systemctl enable --now {shell_quote(user.systemd_service)}")
    lines.append("")
    return "\n".join(lines)


def render_summary(specs: list[UserSpec]) -> str:
    lines = ["Hermes + Open WebUI multi-user bundle plan", ""]
    for user in specs:
        lines.extend(
            [
                f"- {user.username}: linux_user={user.linux_user} port={user.api_port} workdir={user.workdir}",
                f"  connection={user.connection_url} prefix_id={user.connection_prefix}",
                f"  base_model_id={user.base_model_id} wrapper={user.model_id} name={user.model_name}",
            ]
        )
    return "\n".join(lines)


def write_bundle(
    output_dir: Path, config: dict[str, Any], specs: list[UserSpec]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = build_summary(specs)
    openwebui_connections = build_openwebui_connections(config, specs)
    wrapper_config = build_wrapper_config(config, specs)
    wrapper_full = GENERATE_WRAPPER_PAYLOAD(wrapper_config)
    wrapper_import = BUILD_WRAPPER_IMPORT_PAYLOAD(wrapper_full)

    for user in specs:
        user_dir = output_dir / "users" / user.username / ".hermes"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / ".env").write_text(build_env_content(user), encoding="utf-8")
        (user_dir / "config.yaml").write_text(
            yaml.safe_dump(
                build_config_data(config, user), sort_keys=False, allow_unicode=False
            ),
            encoding="utf-8",
        )

    systemd_dir = output_dir / "systemd"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    for user in specs:
        (systemd_dir / user.systemd_service).write_text(
            build_systemd_unit(config, user),
            encoding="utf-8",
        )

    openwebui_dir = output_dir / "openwebui"
    openwebui_dir.mkdir(parents=True, exist_ok=True)
    (openwebui_dir / "connections.json").write_text(
        json.dumps(openwebui_connections, indent=2) + "\n",
        encoding="utf-8",
    )
    (openwebui_dir / "wrapper_mapping.yaml").write_text(
        yaml.safe_dump(wrapper_config, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    (openwebui_dir / "wrappers.full.json").write_text(
        json.dumps(wrapper_full, indent=2) + "\n",
        encoding="utf-8",
    )
    (openwebui_dir / "wrappers.import.json").write_text(
        json.dumps(wrapper_import, indent=2) + "\n",
        encoding="utf-8",
    )

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "checklist.md").write_text(build_checklist(specs), encoding="utf-8")
    apply_script = output_dir / "apply_host.sh"
    apply_script.write_text(build_apply_script(specs), encoding="utf-8")
    apply_script.chmod(0o755)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a Linux-user-based Hermes + Open WebUI deployment bundle from users_mapping.yaml."
    )
    parser.add_argument("mapping", type=Path, help="Path to users_mapping.yaml")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write the generated bundle to this directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the normalized deployment summary as JSON.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_mapping(args.mapping, resolve_env=True)
        specs = build_user_specs(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output_dir:
        write_bundle(args.output_dir, config, specs)
        print(f"Wrote bundle to {args.output_dir}")
        print(render_summary(specs))
        return 0

    if args.json:
        print(json.dumps(build_summary(specs), indent=2))
    else:
        print(render_summary(specs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

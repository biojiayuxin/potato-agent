from __future__ import annotations

import os
import re
import secrets
import socket
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
DEFAULT_MAPPING_PATH = Path(
    os.getenv("POTATO_AGENT_MAPPING_PATH") or (REPO_ROOT / "users_mapping.yaml")
)
DEFAULT_START_PORT = 8643
DEFAULT_API_SERVER_HOST = "127.0.0.1"
DEFAULT_MODEL_NAME = "Hermes"
ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([^}]+)\}$")


@dataclass(frozen=True)
class HermesTarget:
    username: str
    email: str
    display_name: str
    linux_user: str
    home_dir: Path
    hermes_home: Path
    workdir: Path
    api_server_host: str
    api_port: int
    api_key: str
    api_server_model_name: str
    systemd_service: str
    extra_env: dict[str, str]
    config_overrides: dict[str, Any]

    @property
    def api_base_url(self) -> str:
        return f"http://{self.api_server_host}:{self.api_port}"

    @property
    def connection_url(self) -> str:
        return f"{self.api_base_url}/v1"

    @property
    def state_db_path(self) -> Path:
        return self.hermes_home / "state.db"


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
    if not isinstance(value, str):
        return value

    match = ENV_PLACEHOLDER_RE.fullmatch(value.strip())
    if match is None:
        return value

    env_name = match.group(1)
    env_value = os.getenv(env_name)
    if not env_value:
        raise RuntimeError(
            f"{field_name} references environment variable {env_name!r}, but it is not set."
        )
    return env_value


def load_mapping(
    path: Path = DEFAULT_MAPPING_PATH, *, resolve_env: bool = False
) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise RuntimeError(f"Mapping file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Top-level YAML structure must be a mapping/object.")
    if resolve_env:
        return resolve_env_placeholders(data, str(path))
    return data


def write_mapping(path: Path, config: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def slugify_username(username: str) -> str:
    return username.replace("_", "-")


def select_next_port(config: dict[str, Any]) -> int:
    start_port = int(config.get("start_port") or DEFAULT_START_PORT)
    used_ports = {
        int(user.get("api_port"))
        for user in (config.get("users") or [])
        if isinstance(user, dict) and user.get("api_port") is not None
    }
    port = start_port
    while port in used_ports or not _is_port_available(port):
        port += 1
    return port


def _is_port_available(port: int, host: str = DEFAULT_API_SERVER_HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def infer_shared_api_key_placeholder(config: dict[str, Any]) -> str | None:
    candidates: list[str] = []
    hermes_cfg = config.get("hermes") or {}
    model_cfg = hermes_cfg.get("model") or {}
    extra_env = hermes_cfg.get("extra_env") or {}

    if isinstance(model_cfg, dict):
        candidates.append(str(model_cfg.get("api_key") or ""))
    if isinstance(extra_env, dict):
        candidates.extend(str(value or "") for value in extra_env.values())

    for item in config.get("users") or []:
        if isinstance(item, dict):
            candidates.append(str(item.get("api_key") or ""))

    for candidate in candidates:
        match = ENV_PLACEHOLDER_RE.fullmatch(candidate.strip())
        if match is not None:
            return candidate.strip()
    return None


def _build_target(config: dict[str, Any], raw_user: dict[str, Any]) -> HermesTarget:
    hermes_cfg = config.get("hermes") or {}
    global_extra_env = hermes_cfg.get("extra_env") or {}
    if not isinstance(global_extra_env, dict):
        global_extra_env = {}

    user_extra_env = raw_user.get("extra_env") or {}
    if not isinstance(user_extra_env, dict):
        user_extra_env = {}

    config_overrides = raw_user.get("config_overrides") or {}
    if not isinstance(config_overrides, dict):
        config_overrides = {}

    username = str(raw_user.get("username") or "").strip()
    linux_user = str(raw_user.get("linux_user") or f"hmx_{username}").strip()
    home_dir = Path(str(raw_user.get("home_dir") or f"/home/{linux_user}")).resolve()
    hermes_home = Path(
        str(raw_user.get("hermes_home") or (home_dir / ".hermes"))
    ).resolve()
    workdir = Path(str(raw_user.get("workdir") or (home_dir / "work"))).resolve()
    email = (
        str(raw_user.get("email") or raw_user.get("webui_user") or "").strip().lower()
    )
    display_name = str(
        raw_user.get("display_name") or raw_user.get("webui_display_name") or username
    ).strip()
    api_server_host = str(
        raw_user.get("api_server_host")
        or hermes_cfg.get("api_server_host")
        or DEFAULT_API_SERVER_HOST
    ).strip()
    api_server_model_name = str(
        raw_user.get("api_server_model_name")
        or hermes_cfg.get("api_server_model_name")
        or DEFAULT_MODEL_NAME
    ).strip()
    systemd_service = str(
        raw_user.get("systemd_service")
        or f"hermes-{slugify_username(username)}.service"
    ).strip()

    return HermesTarget(
        username=username,
        email=email,
        display_name=display_name,
        linux_user=linux_user,
        home_dir=home_dir,
        hermes_home=hermes_home,
        workdir=workdir,
        api_server_host=api_server_host,
        api_port=int(raw_user.get("api_port")),
        api_key=str(raw_user.get("api_key") or "").strip(),
        api_server_model_name=api_server_model_name,
        systemd_service=systemd_service,
        extra_env={
            str(key): str(value)
            for key, value in {**global_extra_env, **user_extra_env}.items()
        },
        config_overrides=deepcopy(config_overrides),
    )


class MappingStore:
    def __init__(self, path: Path = DEFAULT_MAPPING_PATH):
        self.path = path
        self._mtime_ns: int | None = None
        self._targets: list[HermesTarget] = []

    def load_config(self, *, resolve_env: bool = True) -> dict[str, Any]:
        return load_mapping(self.path, resolve_env=resolve_env)

    def load_targets(self) -> list[HermesTarget]:
        try:
            stat = self.path.stat()
        except FileNotFoundError as exc:
            raise RuntimeError(f"Mapping file not found: {self.path}") from exc

        if self._mtime_ns == stat.st_mtime_ns and self._targets:
            return self._targets

        config = self.load_config(resolve_env=True)
        targets: list[HermesTarget] = []
        for raw_user in config.get("users") or []:
            if not isinstance(raw_user, dict):
                continue
            if not raw_user.get("username") or raw_user.get("api_port") is None:
                continue
            target = _build_target(config, raw_user)
            if target.username and target.api_key:
                targets.append(target)

        self._targets = targets
        self._mtime_ns = stat.st_mtime_ns
        return targets

    def get_target_by_username(self, username: str) -> HermesTarget | None:
        for target in self.load_targets():
            if target.username == username:
                return target
        return None

    def resolve_target(
        self,
        *,
        mapping_username: str | None = None,
        email: str | None = None,
        username: str | None = None,
    ) -> HermesTarget | None:
        normalized_email = (email or "").strip().lower()
        normalized_username = (username or "").strip()
        normalized_mapping_username = (mapping_username or "").strip()
        for target in self.load_targets():
            if (
                normalized_mapping_username
                and target.username == normalized_mapping_username
            ):
                return target
            if normalized_email and target.email and target.email == normalized_email:
                return target
            if normalized_username and target.username == normalized_username:
                return target
        return None


def upsert_user_mapping_entry(
    config: dict[str, Any],
    *,
    username: str,
    email: str,
    display_name: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    users = config.setdefault("users", [])
    if not isinstance(users, list):
        raise RuntimeError("users_mapping.yaml has invalid users structure.")

    entry = None
    for item in users:
        if isinstance(item, dict) and item.get("username") == username:
            entry = item
            break

    slug = slugify_username(username)
    if entry is None:
        entry = {
            "username": username,
            "email": email,
            "display_name": display_name,
            "linux_user": f"hmx_{username}",
            "home_dir": f"/home/hmx_{username}",
            "hermes_home": f"/home/hmx_{username}/.hermes",
            "workdir": f"/home/hmx_{username}/work",
            "api_port": select_next_port(config),
            "api_server_model_name": DEFAULT_MODEL_NAME,
            "systemd_service": f"hermes-{slug}.service",
        }
        users.append(entry)

    entry["email"] = email
    entry["display_name"] = display_name
    # Keep legacy keys in sync when present so old docs/scripts can still inspect the file.
    if (
        "webui_user" in entry
        or "openwebui_user_id" in entry
        or "webui_display_name" in entry
    ):
        entry["webui_user"] = email
        entry["webui_display_name"] = display_name

    entry.setdefault("linux_user", f"hmx_{username}")
    entry.setdefault("home_dir", f"/home/{entry['linux_user']}")
    entry.setdefault("hermes_home", f"{entry['home_dir']}/.hermes")
    entry.setdefault("workdir", f"{entry['home_dir']}/work")
    entry.setdefault("api_port", select_next_port(config))
    entry.setdefault("api_server_model_name", DEFAULT_MODEL_NAME)
    entry.setdefault("systemd_service", f"hermes-{slug}.service")

    if api_key:
        entry["api_key"] = api_key
    else:
        entry.setdefault(
            "api_key",
            infer_shared_api_key_placeholder(config) or secrets.token_urlsafe(24),
        )

    return entry


def remove_user_mapping_entry(config: dict[str, Any], username: str) -> bool:
    users = config.get("users") or []
    if not isinstance(users, list):
        raise RuntimeError("users_mapping.yaml has invalid users structure.")
    remaining = [
        item
        for item in users
        if not (isinstance(item, dict) and item.get("username") == username)
    ]
    removed = len(remaining) != len(users)
    config["users"] = remaining
    return removed

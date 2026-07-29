from __future__ import annotations

import os
import re
import secrets
import socket
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from interface.hermes_profile import (
    DEFAULT_ACTIVATION_RUNTIME_PROFILE_PATH,
    activation_runtime_profile_path,
    local_browser_cdp_url,
)
from interface.secure_paths import (
    DEFAULT_MAPPING_FILE_MODE,
    DEFAULT_STATE_DIR,
    ensure_private_directory,
    ensure_private_file,
)
from interface.model_proxy_config import (
    ModelProxyConfigError,
    generate_model_proxy_token,
    local_model_proxy_token,
)


ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
DEFAULT_MAPPING_PATH = Path(
    os.getenv("POTATO_AGENT_MAPPING_PATH") or (DEFAULT_STATE_DIR / "config" / "users_mapping.yaml")
)
DEFAULT_START_PORT = 8643
DEFAULT_API_SERVER_HOST = "127.0.0.1"
DEFAULT_MODEL_NAME = "Hermes"
ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([^}]+)\}$")
DEFAULT_USERADD_CONFIG_PATH = Path("/etc/default/useradd")


def _load_default_linux_home_base() -> Path:
    try:
        content = DEFAULT_USERADD_CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Path("/home")

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("HOME="):
            continue
        home_base = stripped.split("=", 1)[1].strip()
        if home_base:
            return Path(home_base)
    return Path("/home")


DEFAULT_LINUX_HOME_BASE = _load_default_linux_home_base()


def _default_home_dir(linux_user: str) -> Path:
    return (DEFAULT_LINUX_HOME_BASE / linux_user).resolve()


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
    runtime_profile_path: Path = DEFAULT_ACTIVATION_RUNTIME_PROFILE_PATH
    browser_cdp_url: str = ""
    model_proxy_token: str = field(default_factory=generate_model_proxy_token)

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
    ensure_private_directory(path.parent)
    body = yaml.safe_dump(config, sort_keys=False, allow_unicode=False)
    fd, raw_tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp_path = Path(raw_tmp_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        ensure_private_file(path, mode=DEFAULT_MAPPING_FILE_MODE)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def ensure_model_proxy_tokens(config: dict[str, Any]) -> int:
    users = config.get("users") or []
    if not isinstance(users, list):
        raise RuntimeError("users_mapping.yaml has invalid users structure.")

    valid_tokens: dict[str, list[int]] = {}
    rotate_indexes: set[int] = set()
    for index, raw_user in enumerate(users):
        if not isinstance(raw_user, dict):
            continue
        username = str(raw_user.get("username") or "").strip()
        if not username:
            continue
        token = str(raw_user.get("model_proxy_token") or "").strip()
        try:
            token = local_model_proxy_token(username, token)
        except ModelProxyConfigError:
            token = ""
        if not token:
            rotate_indexes.add(index)
            continue
        valid_tokens.setdefault(token, []).append(index)

    for indexes in valid_tokens.values():
        if len(indexes) > 1:
            rotate_indexes.update(indexes)

    reserved_tokens = {
        token
        for token, indexes in valid_tokens.items()
        if len(indexes) == 1 and indexes[0] not in rotate_indexes
    }
    for index in sorted(rotate_indexes):
        raw_user = users[index]
        if not isinstance(raw_user, dict):
            continue
        token = generate_model_proxy_token()
        while token in reserved_tokens:
            token = generate_model_proxy_token()
        raw_user["model_proxy_token"] = token
        reserved_tokens.add(token)
    return len(rotate_indexes)


def ensure_unique_user_api_keys(config: dict[str, Any]) -> int:
    users = config.get("users") or []
    if not isinstance(users, list):
        raise RuntimeError("users_mapping.yaml has invalid users structure.")

    literal_keys: dict[str, list[int]] = {}
    rotate_indexes: set[int] = set()
    for index, raw_user in enumerate(users):
        if not isinstance(raw_user, dict):
            continue
        api_key = str(raw_user.get("api_key") or "").strip()
        if not api_key or ENV_PLACEHOLDER_RE.fullmatch(api_key):
            rotate_indexes.add(index)
            continue
        literal_keys.setdefault(api_key, []).append(index)

    for indexes in literal_keys.values():
        if len(indexes) > 1:
            rotate_indexes.update(indexes)

    reserved_keys = {
        api_key
        for api_key, indexes in literal_keys.items()
        if len(indexes) == 1 and indexes[0] not in rotate_indexes
    }
    for index in sorted(rotate_indexes):
        raw_user = users[index]
        if not isinstance(raw_user, dict):
            continue
        api_key = secrets.token_urlsafe(32)
        while api_key in reserved_keys:
            api_key = secrets.token_urlsafe(32)
        raw_user["api_key"] = api_key
        reserved_keys.add(api_key)
    return len(rotate_indexes)


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
    home_dir = Path(
        str(raw_user.get("home_dir") or _default_home_dir(linux_user))
    ).resolve()
    hermes_home = Path(
        str(raw_user.get("hermes_home") or (home_dir / ".hermes"))
    ).resolve()
    workdir = Path(str(raw_user.get("workdir") or home_dir)).resolve()
    email = str(raw_user.get("email") or "").strip().lower()
    display_name = str(raw_user.get("display_name") or username).strip()
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
    configured_runtime_profile = (
        hermes_cfg["runtime_profile_path"]
        if "runtime_profile_path" in hermes_cfg
        else None
    )

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
        runtime_profile_path=activation_runtime_profile_path(
            configured_runtime_profile
        ),
        browser_cdp_url=local_browser_cdp_url(
            hermes_cfg.get("browser_cdp_url")
        ),
        model_proxy_token=str(raw_user.get("model_proxy_token") or "").strip(),
    )


def _validate_target(
    target: HermesTarget,
    *,
    entry_index: int,
) -> None:
    entry_name = target.username or f"users[{entry_index}]"
    required_text_fields = {
        "username": target.username,
        "linux_user": target.linux_user,
        "api_server_host": target.api_server_host,
        "api_key": target.api_key,
        "api_server_model_name": target.api_server_model_name,
        "systemd_service": target.systemd_service,
    }
    for field_name, value in required_text_fields.items():
        if not str(value or "").strip():
            raise RuntimeError(
                f"Mapping entry {entry_name!r} is missing required field "
                f"{field_name!r}."
            )

    if not 1 <= target.api_port <= 65535:
        raise RuntimeError(
            f"Mapping entry {entry_name!r} has an invalid api_port."
        )

    try:
        local_model_proxy_token(target.username, target.model_proxy_token)
    except ModelProxyConfigError as exc:
        raise RuntimeError(
            f"Mapping entry {entry_name!r} is missing a valid model_proxy_token."
        ) from exc


def _validate_unique_targets(targets: list[HermesTarget]) -> None:
    seen: dict[str, dict[Any, str]] = {
        "username": {},
        "email": {},
        "linux_user": {},
        "home_dir": {},
        "hermes_home": {},
        "workdir": {},
        "state_db_path": {},
        "systemd_service": {},
        "api_endpoint": {},
        "api_key": {},
        "model_proxy_token": {},
    }
    for target in targets:
        values: dict[str, Any] = {
            "username": target.username.casefold(),
            "email": target.email.casefold(),
            "linux_user": target.linux_user,
            "home_dir": target.home_dir.resolve(),
            "hermes_home": target.hermes_home.resolve(),
            "workdir": target.workdir.resolve(),
            "state_db_path": target.state_db_path.resolve(),
            "systemd_service": target.systemd_service,
            "api_endpoint": (target.api_server_host.casefold(), target.api_port),
            "api_key": target.api_key,
            "model_proxy_token": target.model_proxy_token,
        }
        for field_name, value in values.items():
            if field_name == "email" and not value:
                continue
            previous_username = seen[field_name].get(value)
            if previous_username is not None:
                raise RuntimeError(
                    f"Mapping users {previous_username!r} and {target.username!r} "
                    f"must not share {field_name!r}."
                )
            seen[field_name][value] = target.username


def build_targets_from_config(
    config: dict[str, Any],
    *,
    resolve_env: bool = True,
) -> list[HermesTarget]:
    if not isinstance(config, dict):
        raise RuntimeError("Top-level mapping structure must be an object.")
    if resolve_env:
        config = resolve_env_placeholders(config, "users_mapping")

    raw_users = config.get("users")
    if not isinstance(raw_users, list):
        raise RuntimeError("users_mapping.yaml has invalid users structure.")

    hermes_config = config.get("hermes")
    if hermes_config is not None and not isinstance(hermes_config, dict):
        raise RuntimeError("users_mapping.yaml has invalid hermes structure.")

    targets: list[HermesTarget] = []
    for index, raw_user in enumerate(raw_users):
        if not isinstance(raw_user, dict):
            raise RuntimeError(f"Mapping entry users[{index}] must be an object.")
        username = str(raw_user.get("username") or "").strip()
        if not username:
            raise RuntimeError(
                f"Mapping entry users[{index}] is missing required field 'username'."
            )
        if raw_user.get("api_port") is None or isinstance(
            raw_user.get("api_port"), bool
        ):
            raise RuntimeError(
                f"Mapping entry {username!r} is missing a valid api_port."
            )
        try:
            target = _build_target(config, raw_user)
        except (OSError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Mapping entry {username!r} contains an invalid value."
            ) from exc
        _validate_target(target, entry_index=index)
        targets.append(target)

    _validate_unique_targets(targets)
    return targets


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
        targets = build_targets_from_config(config, resolve_env=False)

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
        targets = self.load_targets()
        if normalized_mapping_username:
            for target in targets:
                if target.username == normalized_mapping_username:
                    return target
            return None
        for target in targets:
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
        if not isinstance(item, dict):
            continue
        existing_username = str(item.get("username") or "").strip()
        if existing_username == username:
            entry = item
            break
        if existing_username.casefold() == username.casefold():
            raise RuntimeError(
                f"Mapping username {username!r} conflicts with an existing user."
            )

    slug = slugify_username(username)
    default_linux_user = f"hmx_{username}"
    if entry is None:
        default_home_dir = _default_home_dir(default_linux_user)
        entry = {
            "username": username,
            "email": email,
            "display_name": display_name,
            "linux_user": default_linux_user,
            "home_dir": str(default_home_dir),
            "hermes_home": str(default_home_dir / ".hermes"),
            "workdir": str(default_home_dir),
            "api_port": select_next_port(config),
            "api_server_model_name": DEFAULT_MODEL_NAME,
            "systemd_service": f"hermes-{slug}.service",
        }
        users.append(entry)

    entry["email"] = email
    entry["display_name"] = display_name
    normalized_email = str(email or "").strip().casefold()
    if normalized_email and any(
        isinstance(item, dict)
        and item is not entry
        and str(item.get("email") or "").strip().casefold() == normalized_email
        for item in users
    ):
        raise RuntimeError(
            f"Mapping user {username!r} must not share 'email' with another user."
        )
    entry.setdefault("linux_user", default_linux_user)
    home_dir = Path(
        str(entry.get("home_dir") or _default_home_dir(str(entry["linux_user"])))
    )
    home_dir = home_dir.resolve()
    entry.setdefault("home_dir", str(home_dir))
    entry.setdefault("hermes_home", str(home_dir / ".hermes"))
    entry.setdefault("workdir", str(home_dir))
    entry.setdefault("api_port", select_next_port(config))
    entry.setdefault("api_server_model_name", DEFAULT_MODEL_NAME)
    entry.setdefault("systemd_service", f"hermes-{slug}.service")

    other_api_keys = {
        str(item.get("api_key") or "").strip()
        for item in users
        if isinstance(item, dict) and item is not entry
    }
    if api_key:
        entry["api_key"] = str(api_key).strip()
    elif not str(entry.get("api_key") or "").strip():
        generated_api_key = secrets.token_urlsafe(32)
        while generated_api_key in other_api_keys:
            generated_api_key = secrets.token_urlsafe(32)
        entry["api_key"] = generated_api_key
    if str(entry.get("api_key") or "").strip() in other_api_keys:
        raise RuntimeError(
            f"Mapping user {username!r} must not share 'api_key' with another user."
        )
    token = str(entry.get("model_proxy_token") or "").strip()
    try:
        token = local_model_proxy_token(username, token)
    except ModelProxyConfigError:
        token = ""
    other_tokens = {
        str(item.get("model_proxy_token") or "").strip()
        for item in users
        if isinstance(item, dict) and item is not entry
    }
    if token and token in other_tokens:
        raise RuntimeError(
            f"Mapping user {username!r} must not share 'model_proxy_token' "
            "with another user."
        )
    if not token:
        token = generate_model_proxy_token()
        while token in other_tokens:
            token = generate_model_proxy_token()
        entry["model_proxy_token"] = token

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

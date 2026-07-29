from __future__ import annotations

import grp
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

from interface.secure_paths import (
    DEFAULT_STATE_DIR,
    ensure_private_directory,
)


DEFAULT_MODEL_PROXY_HOST = os.getenv("POTATO_MODEL_PROXY_HOST") or "127.0.0.1"
DEFAULT_MODEL_PROXY_PORT = int(os.getenv("POTATO_MODEL_PROXY_PORT") or "8765")
DEFAULT_MODEL_PROXY_CONFIG_PATH = Path(
    os.getenv("POTATO_MODEL_PROXY_CONFIG_PATH")
    or (DEFAULT_STATE_DIR / "config" / "model_proxy.yaml")
)
MODEL_PROXY_TOKEN_PREFIX = "pmp_"
MODEL_PROXY_TOKEN_MIN_LENGTH = 40
MODEL_PROXY_TOKEN_PLACEHOLDER = "{model_proxy_token}"
MODEL_PROXY_SERVICE_GROUP = "potato-model-proxy"
MAX_MODEL_PROXY_CONFIG_BYTES = 1024 * 1024


class ModelProxyConfigError(RuntimeError):
    pass


def generate_model_proxy_token() -> str:
    import secrets

    return f"{MODEL_PROXY_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def local_model_proxy_token(username: str, configured_token: str | None = None) -> str:
    normalized_username = str(username or "").strip()
    if not normalized_username:
        raise ModelProxyConfigError("username is required for local model proxy token.")
    normalized_token = str(configured_token or "").strip()
    if (
        not normalized_token.startswith(MODEL_PROXY_TOKEN_PREFIX)
        or len(normalized_token) < MODEL_PROXY_TOKEN_MIN_LENGTH
    ):
        raise ModelProxyConfigError(
            f"User {normalized_username!r} is missing a valid model proxy token."
        )
    return normalized_token


def default_model_proxy_base_url() -> str:
    return f"http://{DEFAULT_MODEL_PROXY_HOST}:{DEFAULT_MODEL_PROXY_PORT}/v1"


def get_model_proxy_base_url(config: dict[str, Any] | None = None) -> str:
    hermes = config.get("hermes") if isinstance(config, dict) else {}
    proxy = hermes.get("model_proxy") if isinstance(hermes, dict) else {}
    if isinstance(proxy, dict):
        base_url = str(proxy.get("base_url") or "").strip().rstrip("/")
        if base_url:
            return base_url
        host = str(proxy.get("host") or DEFAULT_MODEL_PROXY_HOST).strip()
        port = int(proxy.get("port") or DEFAULT_MODEL_PROXY_PORT)
        return f"http://{host}:{port}/v1"
    return default_model_proxy_base_url()


def get_model_proxy_config_path(mapping_path: Path | None = None) -> Path:
    configured = os.getenv("POTATO_MODEL_PROXY_CONFIG_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    if mapping_path is not None:
        return mapping_path.expanduser().resolve().with_name("model_proxy.yaml")
    return DEFAULT_MODEL_PROXY_CONFIG_PATH


def load_model_proxy_config(path: Path = DEFAULT_MODEL_PROXY_CONFIG_PATH) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ModelProxyConfigError(f"Model proxy config not found: {path}") from exc
    except OSError as exc:
        raise ModelProxyConfigError("Unable to load model proxy configuration.") from exc

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ModelProxyConfigError("Model proxy config must be a regular file.")
        _validate_proxy_config_access(file_stat)
        if file_stat.st_size > MAX_MODEL_PROXY_CONFIG_BYTES:
            raise ModelProxyConfigError("Model proxy config exceeds the size limit.")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            raw_config = handle.read(MAX_MODEL_PROXY_CONFIG_BYTES + 1)
    except UnicodeError as exc:
        raise ModelProxyConfigError("Model proxy config must be valid UTF-8.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(raw_config.encode("utf-8")) > MAX_MODEL_PROXY_CONFIG_BYTES:
        raise ModelProxyConfigError("Model proxy config exceeds the size limit.")
    try:
        data = yaml.safe_load(raw_config) or {}
    except yaml.YAMLError as exc:
        raise ModelProxyConfigError("Unable to load model proxy configuration.") from exc
    if not isinstance(data, dict):
        raise ModelProxyConfigError("Model proxy config must be a mapping/object.")
    return data


def _validate_proxy_config_access(file_stat: os.stat_result) -> None:
    mode = stat.S_IMODE(file_stat.st_mode)
    if mode not in {0o400, 0o440, 0o600, 0o640}:
        raise ModelProxyConfigError(
            "Model proxy config permissions must be 0400, 0440, 0600, or 0640."
        )

    if mode & 0o040:
        try:
            expected_gid = grp.getgrnam(MODEL_PROXY_SERVICE_GROUP).gr_gid
        except KeyError as exc:
            raise ModelProxyConfigError(
                f"Required model proxy group does not exist: {MODEL_PROXY_SERVICE_GROUP}"
            ) from exc
        if file_stat.st_gid != expected_gid:
            raise ModelProxyConfigError(
                "Group-readable model proxy config must use the dedicated service group."
            )

    effective_uid = os.geteuid()
    if effective_uid == 0:
        if file_stat.st_uid != 0:
            raise ModelProxyConfigError("Model proxy config must be owned by root.")
        return

    if file_stat.st_uid not in {0, effective_uid}:
        raise ModelProxyConfigError(
            "Model proxy config must be owned by root or the current service user."
        )
    if mode & 0o040:
        readable_groups = set(os.getgroups()) | {os.getegid()}
        if file_stat.st_gid not in readable_groups:
            raise ModelProxyConfigError(
                "Model proxy config group is not assigned to the current service user."
            )


def _proxy_config_owner_and_mode() -> tuple[int, int, int]:
    if os.geteuid() != 0:
        return os.geteuid(), os.getegid(), 0o600
    try:
        service_group = grp.getgrnam(MODEL_PROXY_SERVICE_GROUP)
    except KeyError as exc:
        raise ModelProxyConfigError(
            f"Required model proxy group does not exist: {MODEL_PROXY_SERVICE_GROUP}"
        ) from exc
    return 0, service_group.gr_gid, 0o640


def write_model_proxy_config(path: Path, config: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    body = yaml.safe_dump(config, sort_keys=False, allow_unicode=False)
    owner_uid, owner_gid, file_mode = _proxy_config_owner_and_mode()
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
            if os.geteuid() == 0:
                os.fchown(handle.fileno(), owner_uid, owner_gid)
            os.fchmod(handle.fileno(), file_mode)
        os.replace(tmp_path, path)
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

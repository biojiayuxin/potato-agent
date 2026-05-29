from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from interface.secure_paths import (
    DEFAULT_MAPPING_FILE_MODE,
    DEFAULT_STATE_DIR,
    ensure_private_directory,
    ensure_private_file,
)


DEFAULT_MODEL_PROXY_HOST = os.getenv("POTATO_MODEL_PROXY_HOST") or "127.0.0.1"
DEFAULT_MODEL_PROXY_PORT = int(os.getenv("POTATO_MODEL_PROXY_PORT") or "8765")
DEFAULT_MODEL_PROXY_CONFIG_PATH = Path(
    os.getenv("POTATO_MODEL_PROXY_CONFIG_PATH")
    or (DEFAULT_STATE_DIR / "config" / "model_proxy.yaml")
)
TOKEN_SUFFIX = "-local-token"


class ModelProxyConfigError(RuntimeError):
    pass


def local_model_proxy_token(username: str) -> str:
    normalized = str(username or "").strip()
    if not normalized:
        raise ModelProxyConfigError("username is required for local model proxy token.")
    return f"{normalized}{TOKEN_SUFFIX}"


def username_from_local_token(token: str) -> str | None:
    normalized = str(token or "").strip()
    if not normalized.endswith(TOKEN_SUFFIX):
        return None
    username = normalized[: -len(TOKEN_SUFFIX)]
    return username or None


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
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise ModelProxyConfigError(f"Model proxy config not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ModelProxyConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelProxyConfigError("Model proxy config must be a mapping/object.")
    return data


def write_model_proxy_config(path: Path, config: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    ensure_private_file(path, mode=DEFAULT_MAPPING_FILE_MODE)

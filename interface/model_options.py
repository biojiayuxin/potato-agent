from __future__ import annotations

import os
import pwd
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from interface.mapping import HermesTarget
from interface.hermes_profile import apply_runtime_profile
from interface.model_proxy_config import (
    get_model_proxy_base_url,
    local_model_proxy_token,
)
from interface.user_private_files import (
    prepare_user_runtime_directories,
    read_user_private_text,
    write_user_private_text,
)


OPENAI_API_KEY_LINE_RE = re.compile(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=.*$")


class ModelOptionsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelOption:
    id: str
    name: str
    provider: str
    model: str
    context_length: int
    api_mode: str
    reasoning_effort: str
    display_name: str
    route: str
    config_revision: str

    def to_public(self, *, is_primary: bool) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "is_primary": is_primary,
        }
        if self.context_length is not None:
            data["context_length"] = self.context_length
        if self.api_mode:
            data["api_mode"] = self.api_mode
        data["display_name"] = self.display_name or self.name
        data["reasoning_effort"] = self.reasoning_effort
        if self.config_revision:
            data["config_revision"] = self.config_revision
        return data


@dataclass(frozen=True)
class ModelOptions:
    primary_id: str
    options: tuple[ModelOption, ...]
    default_id: str

    @property
    def primary(self) -> ModelOption:
        option = self.get(self.primary_id)
        if option is None:
            raise ModelOptionsError("Primary model option is missing.")
        return option

    @property
    def new_user_default(self) -> ModelOption:
        option = self.get(self.default_id)
        if option is None:
            raise ModelOptionsError("Default model option is missing")
        return option

    def get(self, option_id: str) -> ModelOption | None:
        normalized_id = str(option_id or "").strip()
        for option in self.options:
            if option.id == normalized_id:
                return option
        return None


def normalize_model_options(config: dict[str, Any]) -> ModelOptions:
    if not isinstance(config, dict):
        raise ModelOptionsError("Top-level config must be a mapping/object.")
    hermes = config.get("hermes")
    if not isinstance(hermes, dict):
        raise ModelOptionsError("users_mapping.yaml has invalid hermes structure.")

    if not hermes.get("model_catalog") or "model" in hermes or "model_options" in hermes:
        raise ModelOptionsError("A schema-v2 model catalog is required; legacy model mappings are unsupported")
    from interface.model_catalog import load_public_catalog
    from interface.model_proxy_config import ModelProxyConfigError
    from interface.privileged_client import PrivilegedClient, PrivilegedClientError
    try:
        if os.geteuid() == 0 and not os.getenv("INTERFACE_FORCE_PRIVILEGED_HELPER", "").lower() in {"1", "true", "yes"}:
            catalog = load_public_catalog()
        else:
            catalog = PrivilegedClient().get_model_catalog()
    except (ModelProxyConfigError, PrivilegedClientError):
        raise ModelOptionsError("Model catalog is unavailable") from None
    return model_options_from_catalog(catalog)


def model_options_from_catalog(catalog: dict[str, Any]) -> ModelOptions:
    try:
        options = tuple(ModelOption(
            id=item["id"], name=item["name"], provider=item["provider"], model=item["model"],
            context_length=item["context_length"], api_mode=item["api_mode"],
            reasoning_effort=item["reasoning_effort"], display_name=item["display_name"],
            route=item["route"], config_revision=item["config_revision"],
        ) for item in catalog["options"])
        result = ModelOptions(primary_id=catalog["primary"], default_id=catalog["default"], options=options)
        result.primary
        result.new_user_default
        return result
    except (KeyError, TypeError):
        raise ModelOptionsError("Invalid public model catalog") from None


def _ensure_mapping(parent: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    elif not isinstance(value, dict):
        raise ModelOptionsError(f"{path} must be a mapping/object.")
    return value


def _load_yaml_mapping(
    path: Path, *, target: HermesTarget | None = None
) -> dict[str, Any]:
    raw = (
        read_user_private_text(target, path)
        if target is not None
        else path.read_text(encoding="utf-8") if path.exists() else None
    )
    if raw is None:
        return {}
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ModelOptionsError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelOptionsError(f"{path} must be a mapping/object.")
    return data


def _strip_api_keys_outside_model(
    value: Any, *, protected_model: dict[str, Any] | None = None
) -> None:
    if isinstance(value, dict):
        if value is not protected_model:
            value.pop("api_key", None)
        for child in list(value.values()):
            _strip_api_keys_outside_model(child, protected_model=protected_model)
    elif isinstance(value, list):
        for child in value:
            _strip_api_keys_outside_model(child, protected_model=protected_model)


def _patch_user_hermes_config(
    existing: dict[str, Any],
    target: HermesTarget,
    option: ModelOption,
    *,
    proxy_base_url: str | None = None,
) -> dict[str, Any]:
    patched = deepcopy(existing)
    model = _ensure_mapping(patched, "model", "model")
    model["default"] = option.name
    model["provider"] = option.provider
    model["base_url"] = (proxy_base_url or get_model_proxy_base_url()).rstrip("/")
    model["api_key"] = local_model_proxy_token(
        target.username, target.model_proxy_token
    )

    model["api_mode"] = option.api_mode
    model["context_length"] = option.context_length
    auxiliary = _ensure_mapping(patched, "auxiliary", "auxiliary")
    compression = _ensure_mapping(auxiliary, "compression", "auxiliary.compression")
    compression["context_length"] = option.context_length

    agent = _ensure_mapping(patched, "agent", "agent")
    agent["reasoning_effort"] = option.reasoning_effort

    patched.pop("fallback_providers", None)
    patched.pop("fallback_model", None)
    _strip_api_keys_outside_model(patched, protected_model=model)
    return apply_runtime_profile(patched)


def strip_openai_api_key_env(existing: str | None) -> str:
    if existing is None:
        return ""

    lines = [
        line
        for line in existing.splitlines(keepends=True)
        if not OPENAI_API_KEY_LINE_RE.match(line[:-1] if line.endswith("\n") else line)
    ]
    return "".join(lines)


def repair_user_model_config(
    target: HermesTarget,
    option: ModelOption,
    *,
    proxy_base_url: str | None = None,
) -> None:
    try:
        pw = pwd.getpwnam(target.linux_user)
    except KeyError as exc:
        raise ModelOptionsError(
            f"Linux user {target.linux_user!r} does not exist."
        ) from exc

    prepare_user_runtime_directories(target, pw)

    config_path = target.hermes_home / "config.yaml"
    patched_config = _patch_user_hermes_config(
        _load_yaml_mapping(config_path, target=target),
        target,
        option,
        proxy_base_url=proxy_base_url,
    )
    write_user_private_text(
        target,
        config_path,
        yaml.safe_dump(patched_config, sort_keys=False, allow_unicode=False),
    )

    env_path = target.hermes_home / ".env"
    existing_env = read_user_private_text(target, env_path)
    write_user_private_text(
        target,
        env_path,
        strip_openai_api_key_env(existing_env),
    )

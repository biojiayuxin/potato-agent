from __future__ import annotations

import contextlib
import os
import pwd
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from interface.mapping import HermesTarget

try:
    from utils import atomic_yaml_write
except Exception:  # pragma: no cover - depends on Hermes source import path
    atomic_yaml_write = None


MAX_MODEL_OPTIONS = 3
DEFAULT_MODEL_PROVIDER = "custom"
DEFAULT_MODEL_API_MODE = "codex_responses"
DEFAULT_REASONING_EFFORT = "xhigh"
VALID_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
VALID_API_MODES = {
    "anthropic_messages",
    "bedrock_converse",
    "chat_completions",
    "codex_responses",
}
OPENAI_API_KEY_LINE_RE = re.compile(
    r"^(?P<prefix>\s*)(?:export\s+)?OPENAI_API_KEY\s*=.*$"
)


class ModelOptionsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelOption:
    id: str
    name: str
    provider: str
    model: str
    base_url: str
    api_key: str
    context_length: int | None = None
    api_mode: str = DEFAULT_MODEL_API_MODE
    reasoning_effort: str = DEFAULT_REASONING_EFFORT

    def to_config(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key": self.api_key,
        }
        if self.context_length is not None:
            data["context_length"] = self.context_length
        if self.api_mode:
            data["api_mode"] = self.api_mode
        if self.reasoning_effort:
            data["reasoning_effort"] = self.reasoning_effort
        return data

    def to_public(self, *, is_primary: bool, is_active: bool) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "is_primary": is_primary,
            "is_active": is_active,
        }
        if self.context_length is not None:
            data["context_length"] = self.context_length
        if self.api_mode:
            data["api_mode"] = self.api_mode
        return data

    def matches_model_config(self, model_config: dict[str, Any]) -> bool:
        configured_model = str(
            model_config.get("default") or model_config.get("model") or ""
        ).strip()
        configured_provider = str(
            model_config.get("provider") or DEFAULT_MODEL_PROVIDER
        ).strip()
        configured_base_url = _normalize_base_url(model_config.get("base_url"))
        configured_api_mode = _normalize_api_mode(model_config.get("api_mode"))
        return (
            configured_model == self.model
            and configured_provider == self.provider
            and configured_base_url == self.base_url
            and configured_api_mode == self.api_mode
        )


@dataclass(frozen=True)
class ModelOptions:
    primary_id: str
    options: tuple[ModelOption, ...]

    @property
    def primary(self) -> ModelOption:
        option = self.get(self.primary_id)
        if option is None:
            raise ModelOptionsError("Primary model option is missing.")
        return option

    def get(self, option_id: str) -> ModelOption | None:
        normalized_id = str(option_id or "").strip()
        for option in self.options:
            if option.id == normalized_id:
                return option
        return None


def _normalize_base_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _normalize_api_mode(
    value: Any, *, default: str = DEFAULT_MODEL_API_MODE
) -> str:
    if value is None:
        return default
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    if normalized not in VALID_API_MODES:
        raise ModelOptionsError(
            f"api_mode must be one of: {', '.join(sorted(VALID_API_MODES))}."
        )
    return normalized


def _normalize_reasoning_effort(
    value: Any, *, default: str = DEFAULT_REASONING_EFFORT
) -> str:
    if value is None:
        return default
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    if normalized not in VALID_REASONING_EFFORTS:
        raise ModelOptionsError(
            "reasoning_effort must be one of: "
            f"{', '.join(VALID_REASONING_EFFORTS)}."
        )
    return normalized


def parse_model_context_length(value: Any, *, path: str = "context_length") -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().replace(",", "")
        if not normalized:
            return None
        try:
            parsed = int(normalized)
        except ValueError as exc:
            raise ModelOptionsError(f"{path} must be a positive integer.") from exc
    elif isinstance(value, int):
        parsed = value
    else:
        raise ModelOptionsError(f"{path} must be a positive integer.")
    if parsed <= 0:
        raise ModelOptionsError(f"{path} must be a positive integer.")
    return parsed


def model_option_from_mapping(entry: dict[str, Any], *, path: str) -> ModelOption:
    if not isinstance(entry, dict):
        raise ModelOptionsError(f"{path} must be a mapping/object.")

    option_id = str(entry.get("id") or "").strip()
    model_name = str(entry.get("model") or entry.get("default") or "").strip()
    provider = str(entry.get("provider") or DEFAULT_MODEL_PROVIDER).strip()
    display_name = str(entry.get("name") or model_name or option_id).strip()
    base_url = _normalize_base_url(entry.get("base_url"))
    api_key = str(entry.get("api_key") or "").strip()
    context_length = parse_model_context_length(
        entry.get("context_length"), path=f"{path}.context_length"
    )
    api_mode = _normalize_api_mode(entry.get("api_mode"))
    reasoning_effort = _normalize_reasoning_effort(entry.get("reasoning_effort"))

    missing = [
        field
        for field, value in (
            ("id", option_id),
            ("model", model_name),
            ("base_url", base_url),
            ("api_key", api_key),
        )
        if not value
    ]
    if missing:
        raise ModelOptionsError(
            f"{path} is missing required field(s): {', '.join(missing)}."
        )
    if not provider:
        provider = DEFAULT_MODEL_PROVIDER

    return ModelOption(
        id=option_id,
        name=display_name,
        provider=provider,
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        context_length=context_length,
        api_mode=api_mode,
        reasoning_effort=reasoning_effort,
    )


def model_option_from_hermes_model(
    hermes: dict[str, Any],
    *,
    option_id: str | None = None,
    name: str | None = None,
) -> ModelOption:
    model_config = hermes.get("model")
    if not isinstance(model_config, dict):
        raise ModelOptionsError("hermes.model must be a mapping/object.")

    extra_env = hermes.get("extra_env") if isinstance(hermes.get("extra_env"), dict) else {}
    config_overrides = (
        hermes.get("config_overrides")
        if isinstance(hermes.get("config_overrides"), dict)
        else {}
    )
    agent_config = (
        config_overrides.get("agent")
        if isinstance(config_overrides.get("agent"), dict)
        else {}
    )
    model_name = str(
        model_config.get("default") or model_config.get("model") or ""
    ).strip()
    resolved_id = str(option_id or model_config.get("id") or model_name).strip()
    entry = {
        "id": resolved_id,
        "name": str(name or model_config.get("name") or model_name or resolved_id),
        "provider": model_config.get("provider") or DEFAULT_MODEL_PROVIDER,
        "model": model_name,
        "base_url": model_config.get("base_url"),
        "api_key": model_config.get("api_key") or extra_env.get("OPENAI_API_KEY"),
        "context_length": model_config.get("context_length"),
        "api_mode": model_config.get("api_mode"),
        "reasoning_effort": agent_config.get("reasoning_effort"),
    }
    return model_option_from_mapping(entry, path="hermes.model")


def normalize_model_options(config: dict[str, Any]) -> ModelOptions:
    if not isinstance(config, dict):
        raise ModelOptionsError("Top-level config must be a mapping/object.")
    hermes = config.get("hermes")
    if not isinstance(hermes, dict):
        raise ModelOptionsError("users_mapping.yaml has invalid hermes structure.")

    raw_model_options = hermes.get("model_options")
    if raw_model_options is None:
        primary = model_option_from_hermes_model(hermes)
        return ModelOptions(primary_id=primary.id, options=(primary,))

    if not isinstance(raw_model_options, dict):
        raise ModelOptionsError("hermes.model_options must be a mapping/object.")

    primary_id = str(raw_model_options.get("primary") or "").strip()
    if not primary_id:
        raise ModelOptionsError("hermes.model_options.primary is required.")

    raw_options = raw_model_options.get("options")
    if not isinstance(raw_options, list):
        raise ModelOptionsError("hermes.model_options.options must be a list.")
    if len(raw_options) > MAX_MODEL_OPTIONS:
        raise ModelOptionsError(
            "hermes.model_options.options supports at most 3 entries "
            "(1 primary and up to 2 optional models)."
        )

    seen_ids: set[str] = set()
    options: list[ModelOption] = []
    for index, item in enumerate(raw_options):
        option = model_option_from_mapping(
            item, path=f"hermes.model_options.options[{index}]"
        )
        if option.id in seen_ids:
            raise ModelOptionsError(
                f"Duplicate model option id in hermes.model_options: {option.id}"
            )
        seen_ids.add(option.id)
        options.append(option)

    if primary_id not in seen_ids:
        raise ModelOptionsError(
            "hermes.model_options.primary must match one of the option ids."
        )
    return ModelOptions(primary_id=primary_id, options=tuple(options))


def get_active_model_option_id(target: HermesTarget, model_options: ModelOptions) -> str:
    config_path = target.hermes_home / "config.yaml"
    try:
        user_config = _load_yaml_mapping(config_path)
    except ModelOptionsError:
        return model_options.primary_id

    model_config = user_config.get("model")
    if not isinstance(model_config, dict):
        return model_options.primary_id
    for option in model_options.options:
        with contextlib.suppress(ModelOptionsError):
            if option.matches_model_config(model_config):
                return option.id
    return model_options.primary_id


def _ensure_mapping(parent: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    elif not isinstance(value, dict):
        raise ModelOptionsError(f"{path} must be a mapping/object.")
    return value


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ModelOptionsError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelOptionsError(f"{path} must be a mapping/object.")
    return data


def _patch_user_hermes_config(
    existing: dict[str, Any], option: ModelOption
) -> dict[str, Any]:
    patched = deepcopy(existing)
    model = _ensure_mapping(patched, "model", "model")
    model["default"] = option.model
    model["provider"] = option.provider
    model["base_url"] = option.base_url
    model["api_key"] = option.api_key

    if option.api_mode:
        model["api_mode"] = option.api_mode
    else:
        model.pop("api_mode", None)

    if option.context_length is not None:
        model["context_length"] = option.context_length
        auxiliary = _ensure_mapping(patched, "auxiliary", "auxiliary")
        compression = _ensure_mapping(
            auxiliary, "compression", "auxiliary.compression"
        )
        compression["context_length"] = option.context_length
    else:
        model.pop("context_length", None)
        auxiliary = patched.get("auxiliary")
        compression = (
            auxiliary.get("compression")
            if isinstance(auxiliary, dict)
            else None
        )
        if isinstance(compression, dict):
            compression.pop("context_length", None)

    agent = _ensure_mapping(patched, "agent", "agent")
    agent["reasoning_effort"] = option.reasoning_effort

    return patched


def _quote_env_value(value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_/-.:")
    if value and all(char in allowed for char in value):
        return value
    return repr(value)


def _patch_env_content(existing: str | None, api_key: str) -> str:
    rendered = f"OPENAI_API_KEY={_quote_env_value(api_key)}"
    if existing is None:
        return f"{rendered}\n"

    lines = existing.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        match = OPENAI_API_KEY_LINE_RE.match(body)
        if match:
            prefix = match.group("prefix") or ""
            lines[index] = f"{prefix}{rendered}{newline}"
            return "".join(lines)

    separator = "" if existing.endswith("\n") or not existing else "\n"
    return f"{existing}{separator}{rendered}\n"


def _set_owner_and_mode(path: Path, uid: int, gid: int, mode: int) -> None:
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def _write_text_atomic(path: Path, body: str, *, uid: int, gid: int, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(body, encoding="utf-8")
        _set_owner_and_mode(tmp_path, uid, gid, mode)
        os.replace(tmp_path, path)
        _set_owner_and_mode(path, uid, gid, mode)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()


def _write_yaml_atomic(
    path: Path, data: dict[str, Any], *, uid: int, gid: int, mode: int
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = atomic_yaml_write
    if writer is None:
        with contextlib.suppress(Exception):
            from utils import atomic_yaml_write as imported_writer

            writer = imported_writer
    if writer is not None:
        writer(path, data, sort_keys=False)
        _set_owner_and_mode(path, uid, gid, mode)
        return
    _write_text_atomic(
        path,
        yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        uid=uid,
        gid=gid,
        mode=mode,
    )


def patch_user_active_model(target: HermesTarget, option: ModelOption) -> None:
    try:
        pw = pwd.getpwnam(target.linux_user)
    except KeyError as exc:
        raise ModelOptionsError(
            f"Linux user {target.linux_user!r} does not exist."
        ) from exc

    for directory in (
        target.home_dir,
        target.workdir,
        target.hermes_home,
        target.hermes_home / "home",
    ):
        directory.mkdir(parents=True, exist_ok=True)
        _set_owner_and_mode(directory, pw.pw_uid, pw.pw_gid, 0o700)

    config_path = target.hermes_home / "config.yaml"
    patched_config = _patch_user_hermes_config(_load_yaml_mapping(config_path), option)
    _write_yaml_atomic(
        config_path,
        patched_config,
        uid=pw.pw_uid,
        gid=pw.pw_gid,
        mode=0o600,
    )

    env_path = target.hermes_home / ".env"
    existing_env = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    _write_text_atomic(
        env_path,
        _patch_env_content(existing_env, option.api_key),
        uid=pw.pw_uid,
        gid=pw.pw_gid,
        mode=0o600,
    )

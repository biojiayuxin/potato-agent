#!/usr/bin/env python3

from __future__ import annotations

import argparse
import getpass
import os
import pwd
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from interface.hermes_service import DEFAULT_HERMES_BIN, DEFAULT_TERMINAL_TIMEOUT
from interface.hermes_service import (
    build_config_data,
    is_service_active,
    require_binary,
    require_root,
    restart_service,
    wait_for_service_active,
)
from interface.mapping import (
    DEFAULT_API_SERVER_HOST,
    DEFAULT_MAPPING_PATH,
    DEFAULT_MODEL_NAME,
    DEFAULT_START_PORT,
    MappingStore,
    load_mapping,
    write_mapping,
)


DEFAULT_UPSTREAM_MODEL_NAME = "gpt-5.4"
DEFAULT_FALLBACK_PROVIDER = "custom"
FALLBACK_ACTION_PRESERVE = "preserve"
FALLBACK_ACTION_SET = "set"
FALLBACK_ACTION_CLEAR = "clear"
OPENAI_API_KEY_LINE_RE = re.compile(
    r"^(?P<prefix>\s*)(?:export\s+)?OPENAI_API_KEY\s*=.*$"
)


class ConfigureHermesModelError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or update the Hermes upstream model configuration in "
            "users_mapping.yaml without changing user mappings."
        )
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING_PATH,
        help=f"Path to users_mapping.yaml (default: {DEFAULT_MAPPING_PATH})",
    )
    parser.add_argument(
        "--base-url",
        help="Upstream OpenAI-compatible base URL, e.g. https://gateway.example/v1",
    )
    parser.add_argument(
        "--model",
        help="Default upstream model name, e.g. gpt-5.4",
    )
    parser.add_argument(
        "--api-key",
        help="Upstream API key written to hermes.model.api_key and hermes.extra_env.OPENAI_API_KEY",
    )
    parser.add_argument(
        "--context-length",
        help=(
            "Total upstream model context window in tokens, e.g. 1050000. "
            "Leave unset to preserve the current value."
        ),
    )
    parser.add_argument(
        "--fallback-base-url",
        help=(
            "Fallback OpenAI-compatible base URL written to "
            "hermes.fallback_providers[0].base_url"
        ),
    )
    parser.add_argument(
        "--fallback-model",
        help="Fallback model name written to hermes.fallback_providers[0].model",
    )
    parser.add_argument(
        "--fallback-api-key",
        help="Fallback API key written to hermes.fallback_providers[0].api_key",
    )
    parser.add_argument(
        "--fallback-provider",
        help=(
            "Fallback provider name written to hermes.fallback_providers[0].provider "
            f"(default: {DEFAULT_FALLBACK_PROVIDER})"
        ),
    )
    parser.add_argument(
        "--clear-fallback",
        action="store_true",
        help="Remove hermes.fallback_providers and hermes.fallback_model.",
    )
    parser.add_argument(
        "--apply-to-users",
        action="store_true",
        help=(
            "Update Hermes config for all mapped users and restart their "
            "services after an interactive confirmation prompt."
        ),
    )
    return parser


def fallback_args_requested(args: argparse.Namespace) -> bool:
    return any(
        value is not None
        for value in (
            args.fallback_base_url,
            args.fallback_model,
            args.fallback_api_key,
            args.fallback_provider,
        )
    )


def prompt_value(
    label: str,
    *,
    default: str | None = None,
    secret: bool = False,
    allow_blank: bool = False,
) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        prompt = f"{label}{suffix}: "
        value = getpass.getpass(prompt) if secret else input(prompt)
        value = value.strip()
        if value:
            return value
        if allow_blank:
            return ""
        if default is not None:
            return default
        print("Value is required.", file=sys.stderr)


def resolve_value(
    cli_value: str | None,
    *,
    label: str,
    current: str | None = None,
    default: str | None = None,
    secret: bool = False,
) -> str:
    if cli_value is not None:
        resolved = cli_value.strip()
        if resolved:
            return resolved

    fallback = current or default
    if not sys.stdin.isatty():
        if fallback:
            return fallback
        raise ConfigureHermesModelError(
            f"{label} is required when stdin is not interactive."
        )

    if secret and current:
        updated = prompt_value(
            f"{label} [leave blank to keep current]",
            secret=True,
            allow_blank=True,
        )
        return updated or current

    return prompt_value(label, default=fallback, secret=secret)


def validate_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigureHermesModelError(
            "Base URL must be a full http(s) URL, e.g. https://gateway.example/v1"
        )
    return normalized


def parse_context_length(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().replace(",", "")
    if not normalized:
        return None
    try:
        context_length = int(normalized)
    except ValueError as exc:
        raise ConfigureHermesModelError(
            "--context-length must be a plain positive integer, e.g. 1050000"
        ) from exc
    if context_length <= 0:
        raise ConfigureHermesModelError(
            "--context-length must be a plain positive integer, e.g. 1050000"
        )
    return context_length


def _first_fallback_provider(hermes: dict[str, Any]) -> dict[str, Any] | None:
    providers = hermes.get("fallback_providers")
    if isinstance(providers, list):
        for provider in providers:
            if isinstance(provider, dict):
                return deepcopy(provider)

    fallback_model = hermes.get("fallback_model")
    if isinstance(fallback_model, list):
        for provider in fallback_model:
            if isinstance(provider, dict):
                return deepcopy(provider)
    if isinstance(fallback_model, dict):
        return deepcopy(fallback_model)
    return None


def extract_current_fallback_provider(config: dict[str, Any]) -> dict[str, Any] | None:
    hermes = config.get("hermes") if isinstance(config.get("hermes"), dict) else {}
    return _first_fallback_provider(hermes)


def standardize_fallback_config(hermes: dict[str, Any]) -> None:
    providers = hermes.get("fallback_providers")
    if providers is not None and not isinstance(providers, list):
        raise ConfigureHermesModelError(
            "users_mapping.yaml has invalid hermes.fallback_providers structure."
        )

    fallback_model = hermes.get("fallback_model")
    if isinstance(fallback_model, list):
        if providers is None:
            hermes["fallback_providers"] = deepcopy(fallback_model)
        hermes.pop("fallback_model", None)
        return

    if fallback_model is not None and not isinstance(fallback_model, dict):
        raise ConfigureHermesModelError(
            "users_mapping.yaml has invalid hermes.fallback_model structure."
        )

    if providers is not None and "fallback_model" in hermes:
        hermes.pop("fallback_model", None)


def set_fallback_provider(
    hermes: dict[str, Any],
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
) -> None:
    current_providers = hermes.get("fallback_providers")
    tail = (
        [
            deepcopy(item)
            for item in current_providers[1:]
            if isinstance(item, dict)
        ]
        if isinstance(current_providers, list)
        else []
    )
    hermes["fallback_providers"] = [
        {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
        },
        *tail,
    ]
    hermes.pop("fallback_model", None)


def clear_fallback_config(hermes: dict[str, Any]) -> None:
    hermes.pop("fallback_providers", None)
    hermes.pop("fallback_model", None)


def _ensure_mapping(parent: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    elif not isinstance(value, dict):
        raise ConfigureHermesModelError(f"{path} must be a mapping/object.")
    return value


def set_compression_context_length(
    hermes: dict[str, Any], context_length: int
) -> None:
    config_overrides = _ensure_mapping(
        hermes, "config_overrides", "hermes.config_overrides"
    )
    auxiliary = _ensure_mapping(
        config_overrides,
        "auxiliary",
        "hermes.config_overrides.auxiliary",
    )
    compression = _ensure_mapping(
        auxiliary,
        "compression",
        "hermes.config_overrides.auxiliary.compression",
    )
    compression["context_length"] = context_length


def ensure_mapping_structure(config: dict) -> tuple[dict, list]:
    if not isinstance(config, dict):
        raise ConfigureHermesModelError(
            "Top-level YAML structure must be a mapping/object."
        )

    users = config.get("users")
    if users is None:
        users = []
        config["users"] = users
    elif not isinstance(users, list):
        raise ConfigureHermesModelError("users_mapping.yaml has invalid users structure.")

    config.setdefault("start_port", DEFAULT_START_PORT)

    hermes = config.get("hermes")
    if hermes is None:
        hermes = {}
        config["hermes"] = hermes
    elif not isinstance(hermes, dict):
        raise ConfigureHermesModelError("users_mapping.yaml has invalid hermes structure.")

    hermes.setdefault("executable", DEFAULT_HERMES_BIN)
    hermes.setdefault("api_server_host", DEFAULT_API_SERVER_HOST)
    hermes.setdefault("api_server_model_name", DEFAULT_MODEL_NAME)

    config_overrides = hermes.get("config_overrides")
    if config_overrides is None:
        config_overrides = {}
        hermes["config_overrides"] = config_overrides
    elif not isinstance(config_overrides, dict):
        raise ConfigureHermesModelError(
            "users_mapping.yaml has invalid hermes.config_overrides structure."
        )

    agent = config_overrides.get("agent")
    if agent is None:
        agent = {}
        config_overrides["agent"] = agent
    elif not isinstance(agent, dict):
        raise ConfigureHermesModelError(
            "users_mapping.yaml has invalid hermes.config_overrides.agent structure."
        )
    agent.setdefault("reasoning_effort", "high")

    terminal = hermes.get("terminal")
    if terminal is None:
        terminal = {}
        hermes["terminal"] = terminal
    elif not isinstance(terminal, dict):
        raise ConfigureHermesModelError(
            "users_mapping.yaml has invalid hermes.terminal structure."
        )
    terminal.setdefault("backend", "local")
    terminal.setdefault("timeout", DEFAULT_TERMINAL_TIMEOUT)

    model = hermes.get("model")
    if model is None:
        model = {}
        hermes["model"] = model
    elif not isinstance(model, dict):
        raise ConfigureHermesModelError(
            "users_mapping.yaml has invalid hermes.model structure."
        )

    extra_env = hermes.get("extra_env")
    if extra_env is None:
        extra_env = {}
        hermes["extra_env"] = extra_env
    elif not isinstance(extra_env, dict):
        raise ConfigureHermesModelError(
            "users_mapping.yaml has invalid hermes.extra_env structure."
        )

    standardize_fallback_config(hermes)
    return model, users


def build_new_config() -> dict:
    return {
        "start_port": DEFAULT_START_PORT,
        "hermes": {
            "executable": DEFAULT_HERMES_BIN,
            "api_server_host": DEFAULT_API_SERVER_HOST,
            "api_server_model_name": DEFAULT_MODEL_NAME,
            "config_overrides": {
                "agent": {
                    "reasoning_effort": "high",
                }
            },
            "terminal": {
                "backend": "local",
                "timeout": DEFAULT_TERMINAL_TIMEOUT,
            },
            "model": {},
            "extra_env": {},
        },
        "users": [],
    }


def mask_secret(value: str | None) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def format_fallback_provider(value: dict[str, Any] | None) -> str:
    if not value:
        return "<empty>"
    provider = str(value.get("provider") or "<empty>")
    model = str(value.get("model") or "<empty>")
    base_url = str(value.get("base_url") or "<empty>")
    api_key = mask_secret(str(value.get("api_key") or ""))
    return f"{provider}/{model} @ {base_url} ({api_key})"


def format_change_summary(
    *,
    old_base_url: str | None,
    new_base_url: str,
    old_model_name: str | None,
    new_model_name: str,
    old_context_length: int | None,
    new_context_length: int | None,
    old_api_key: str | None,
    new_api_key: str,
    old_fallback_provider: dict[str, Any] | None,
    new_fallback_provider: dict[str, Any] | None,
) -> list[str]:
    return [
        f"- base_url: {old_base_url or '<empty>'} -> {new_base_url}",
        f"- default model: {old_model_name or '<empty>'} -> {new_model_name}",
        (
            "- context_length: "
            f"{old_context_length or '<empty>'} -> {new_context_length or '<empty>'}"
        ),
        f"- api_key: {mask_secret(old_api_key)} -> {mask_secret(new_api_key)}",
        (
            "- fallback_providers[0]: "
            f"{format_fallback_provider(old_fallback_provider)} -> "
            f"{format_fallback_provider(new_fallback_provider)}"
        ),
    ]


def confirm_apply_to_users(
    mapping_path: Path,
    targets: list,
    *,
    old_base_url: str | None,
    new_base_url: str,
    old_model_name: str | None,
    new_model_name: str,
    old_context_length: int | None,
    new_context_length: int | None,
    old_api_key: str | None,
    new_api_key: str,
    old_fallback_provider: dict[str, Any] | None,
    new_fallback_provider: dict[str, Any] | None,
) -> bool:
    if not sys.stdin.isatty():
        raise ConfigureHermesModelError(
            "--apply-to-users requires an interactive terminal for confirmation."
        )

    print()
    print("WARNING: about to update Hermes config for existing users.")
    print(f"Mapping file: {mapping_path}")
    print("Config changes:")
    for line in format_change_summary(
        old_base_url=old_base_url,
        new_base_url=new_base_url,
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        old_context_length=old_context_length,
        new_context_length=new_context_length,
        old_api_key=old_api_key,
        new_api_key=new_api_key,
        old_fallback_provider=old_fallback_provider,
        new_fallback_provider=new_fallback_provider,
    ):
        print(line)
    print(f"Affected users: {len(targets)}")
    for target in targets:
        print(
            f"- {target.username} ({target.linux_user}) -> {target.systemd_service}"
        )
    print("This will:")
    print("- patch each user's ~/.hermes/config.yaml")
    print("- patch each user's ~/.hermes/.env")
    print("- restart each user's Hermes systemd service")
    print("- interrupt any in-flight requests on those Hermes instances")
    answer = input("Type APPLY to continue: ").strip()
    return answer == "APPLY"


def _set_owner_and_mode(path: Path, uid: int, gid: int, mode: int) -> None:
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def _load_user_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigureHermesModelError(
            f"Invalid YAML in user Hermes config {config_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigureHermesModelError(
            f"User Hermes config {config_path} must be a mapping/object."
        )
    return data


def _patch_user_hermes_config(
    existing: dict[str, Any],
    generated: dict[str, Any],
    *,
    context_length: int | None,
    fallback_action: str,
) -> dict[str, Any]:
    patched = deepcopy(existing)

    generated_model = generated.get("model")
    if not isinstance(generated_model, dict):
        raise ConfigureHermesModelError("Generated Hermes config is missing model.")

    model = _ensure_mapping(patched, "model", "model")
    for key in ("default", "provider", "base_url", "api_key"):
        model[key] = generated_model[key]

    if context_length is not None:
        model["context_length"] = context_length
        auxiliary = _ensure_mapping(patched, "auxiliary", "auxiliary")
        compression = _ensure_mapping(
            auxiliary, "compression", "auxiliary.compression"
        )
        compression["context_length"] = context_length

    if fallback_action == FALLBACK_ACTION_CLEAR:
        patched.pop("fallback_providers", None)
        patched.pop("fallback_model", None)
    elif fallback_action == FALLBACK_ACTION_SET:
        generated_providers = generated.get("fallback_providers")
        if (
            not isinstance(generated_providers, list)
            or not generated_providers
            or not isinstance(generated_providers[0], dict)
        ):
            raise ConfigureHermesModelError(
                "Generated Hermes config is missing fallback_providers[0]."
            )
        existing_providers = patched.get("fallback_providers")
        tail = existing_providers[1:] if isinstance(existing_providers, list) else []
        patched["fallback_providers"] = [
            deepcopy(generated_providers[0]),
            *deepcopy(tail),
        ]
    elif fallback_action != FALLBACK_ACTION_PRESERVE:
        raise ConfigureHermesModelError(f"Unknown fallback action: {fallback_action}")

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


def apply_user_runtime_model_patch(
    resolved_config: dict[str, Any],
    target,
    *,
    context_length: int | None,
    fallback_action: str,
) -> None:
    try:
        pw = pwd.getpwnam(target.linux_user)
    except KeyError as exc:
        raise RuntimeError(f"Linux user {target.linux_user!r} does not exist.") from exc

    gid = pw.pw_gid
    for directory in [
        target.home_dir,
        target.workdir,
        target.hermes_home,
        target.hermes_home / "home",
    ]:
        directory.mkdir(parents=True, exist_ok=True)
        _set_owner_and_mode(directory, pw.pw_uid, gid, 0o700)

    generated = build_config_data(resolved_config, target)

    config_path = target.hermes_home / "config.yaml"
    config_exists = config_path.exists()
    patched_config = _patch_user_hermes_config(
        _load_user_config(config_path) if config_exists else deepcopy(generated),
        generated,
        context_length=context_length,
        fallback_action=fallback_action,
    )
    config_path.write_text(
        yaml.safe_dump(patched_config, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    _set_owner_and_mode(config_path, pw.pw_uid, gid, 0o600)

    model = generated.get("model") if isinstance(generated.get("model"), dict) else {}
    api_key = str(model.get("api_key") or "").strip()
    if not api_key:
        raise ConfigureHermesModelError("Generated Hermes config is missing model.api_key.")

    env_path = target.hermes_home / ".env"
    existing_env = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    env_path.write_text(_patch_env_content(existing_env, api_key), encoding="utf-8")
    _set_owner_and_mode(env_path, pw.pw_uid, gid, 0o600)


def apply_model_config_to_users(
    mapping_path: Path,
    *,
    old_base_url: str | None,
    new_base_url: str,
    old_model_name: str | None,
    new_model_name: str,
    old_context_length: int | None,
    new_context_length: int | None,
    old_api_key: str | None,
    new_api_key: str,
    old_fallback_provider: dict[str, Any] | None,
    new_fallback_provider: dict[str, Any] | None,
    context_length_provided: bool = True,
    fallback_action: str = FALLBACK_ACTION_PRESERVE,
) -> None:
    mapping_store = MappingStore(mapping_path)
    targets = mapping_store.load_targets()
    if not targets:
        print("No mapped users found; skipped Hermes service reload.")
        return

    require_root()
    require_binary("systemctl")

    if not confirm_apply_to_users(
        mapping_path,
        targets,
        old_base_url=old_base_url,
        new_base_url=new_base_url,
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        old_context_length=old_context_length,
        new_context_length=new_context_length,
        old_api_key=old_api_key,
        new_api_key=new_api_key,
        old_fallback_provider=old_fallback_provider,
        new_fallback_provider=new_fallback_provider,
    ):
        print("Skipped applying config to existing users. users_mapping.yaml was updated.")
        return

    resolved_config = load_mapping(mapping_path, resolve_env=True)
    for target in targets:
        print(f"Applying config for user: {target.username}")
        apply_user_runtime_model_patch(
            resolved_config,
            target,
            context_length=new_context_length if context_length_provided else None,
            fallback_action=fallback_action,
        )
        if is_service_active(target.systemd_service):
            restart_service(target.systemd_service)
            wait_for_service_active(target.systemd_service)
            print(f"Restarted Hermes service: {target.systemd_service}")
        else:
            print(
                f"Updated config for stopped runtime: {target.systemd_service} "
                "(will apply on next workspace login)"
            )


def extract_current_values(
    config: dict,
) -> tuple[str | None, str | None, str | None, int | None]:
    hermes = config.get("hermes") if isinstance(config.get("hermes"), dict) else {}
    model = hermes.get("model") if isinstance(hermes.get("model"), dict) else {}
    extra_env = (
        hermes.get("extra_env") if isinstance(hermes.get("extra_env"), dict) else {}
    )

    current_base_url = str(model.get("base_url") or "").strip() or None
    current_model_name = str(model.get("default") or "").strip() or None
    current_api_key = str(model.get("api_key") or extra_env.get("OPENAI_API_KEY") or "").strip() or None
    current_context_length = model.get("context_length")
    if not isinstance(current_context_length, int) or current_context_length <= 0:
        current_context_length = None
    return current_base_url, current_model_name, current_api_key, current_context_length


def main() -> int:
    args = build_parser().parse_args()
    mapping_path = args.mapping.expanduser().resolve()

    if args.clear_fallback and fallback_args_requested(args):
        raise ConfigureHermesModelError(
            "--clear-fallback cannot be combined with --fallback-* options."
        )

    if not mapping_path.parent.exists():
        raise ConfigureHermesModelError(
            f"Parent directory does not exist: {mapping_path.parent}"
        )

    created = not mapping_path.exists()
    config = build_new_config() if created else load_mapping(mapping_path, resolve_env=False)
    (
        current_base_url,
        current_model_name,
        current_api_key,
        current_context_length,
    ) = extract_current_values(config)
    current_fallback_provider = extract_current_fallback_provider(config)
    model, users = ensure_mapping_structure(config)
    hermes = config["hermes"]
    extra_env = hermes["extra_env"]

    base_url = validate_base_url(
        resolve_value(
            args.base_url,
            label="Upstream model base URL",
            current=current_base_url,
        )
    )
    model_name = resolve_value(
        args.model,
        label="Default model name",
        current=current_model_name,
        default=DEFAULT_UPSTREAM_MODEL_NAME,
    )
    api_key = resolve_value(
        args.api_key,
        label="Upstream API key",
        current=current_api_key,
        secret=True,
    )

    model["default"] = model_name
    model["provider"] = "custom"
    model["base_url"] = base_url
    model["api_key"] = api_key
    context_length = parse_context_length(args.context_length)
    if context_length is not None:
        model["context_length"] = context_length
        set_compression_context_length(hermes, context_length)
    new_context_length = model.get("context_length")
    if not isinstance(new_context_length, int) or new_context_length <= 0:
        new_context_length = None
    extra_env["OPENAI_API_KEY"] = api_key

    fallback_action = FALLBACK_ACTION_PRESERVE
    if args.clear_fallback:
        clear_fallback_config(hermes)
        fallback_action = FALLBACK_ACTION_CLEAR
    elif fallback_args_requested(args):
        existing_fallback = _first_fallback_provider(hermes) or {}
        fallback_provider = resolve_value(
            args.fallback_provider,
            label="Fallback provider",
            current=str(existing_fallback.get("provider") or "").strip() or None,
            default=DEFAULT_FALLBACK_PROVIDER,
        )
        fallback_model = resolve_value(
            args.fallback_model,
            label="Fallback model name",
            current=str(existing_fallback.get("model") or "").strip() or None,
        )
        fallback_base_url = validate_base_url(
            resolve_value(
                args.fallback_base_url,
                label="Fallback model base URL",
                current=str(existing_fallback.get("base_url") or "").strip() or None,
            )
        )
        fallback_api_key = resolve_value(
            args.fallback_api_key,
            label="Fallback API key",
            current=str(existing_fallback.get("api_key") or "").strip() or None,
            secret=True,
        )
        set_fallback_provider(
            hermes,
            provider=fallback_provider,
            model=fallback_model,
            base_url=fallback_base_url,
            api_key=fallback_api_key,
        )
        fallback_action = FALLBACK_ACTION_SET

    new_fallback_provider = _first_fallback_provider(hermes)

    write_mapping(mapping_path, config)
    mapping_path.chmod(0o600)

    action = "Created" if created else "Updated"
    print(f"{action} Hermes model config in: {mapping_path}")
    print(f"Upstream base URL: {base_url}")
    print(f"Default model: {model_name}")
    if model.get("context_length"):
        print(f"Context length: {model['context_length']}")
    print(f"Fallback provider: {format_fallback_provider(new_fallback_provider)}")
    print(f"Preserved users: {len(users)}")
    print(
        "Updated fields: hermes.model.*, hermes.extra_env.OPENAI_API_KEY, "
        "hermes.config_overrides.auxiliary.compression.context_length, "
        "hermes.fallback_providers"
    )
    print("File mode: 600")
    if created:
        print("Users section: []")

    if args.apply_to_users:
        apply_model_config_to_users(
            mapping_path,
            old_base_url=current_base_url,
            new_base_url=base_url,
            old_model_name=current_model_name,
            new_model_name=model_name,
            old_context_length=current_context_length,
            new_context_length=new_context_length,
            old_api_key=current_api_key,
            new_api_key=api_key,
            old_fallback_provider=current_fallback_provider,
            new_fallback_provider=new_fallback_provider,
            context_length_provided=context_length is not None,
            fallback_action=fallback_action,
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

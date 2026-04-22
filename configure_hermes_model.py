#!/usr/bin/env python3

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from urllib.parse import urlparse

from interface.hermes_service import DEFAULT_HERMES_BIN, DEFAULT_TERMINAL_TIMEOUT
from interface.hermes_service import (
    is_service_active,
    install_user_files,
    require_binary,
    require_root,
    restart_service,
    wait_for_hermes_models,
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
        "--apply-to-users",
        action="store_true",
        help=(
            "Rewrite Hermes config for all mapped users and restart their "
            "services after an interactive confirmation prompt."
        ),
    )
    return parser


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


def format_change_summary(
    *,
    old_base_url: str | None,
    new_base_url: str,
    old_model_name: str | None,
    new_model_name: str,
    old_api_key: str | None,
    new_api_key: str,
) -> list[str]:
    return [
        f"- base_url: {old_base_url or '<empty>'} -> {new_base_url}",
        f"- default model: {old_model_name or '<empty>'} -> {new_model_name}",
        f"- api_key: {mask_secret(old_api_key)} -> {mask_secret(new_api_key)}",
    ]


def confirm_apply_to_users(
    mapping_path: Path,
    targets: list,
    *,
    old_base_url: str | None,
    new_base_url: str,
    old_model_name: str | None,
    new_model_name: str,
    old_api_key: str | None,
    new_api_key: str,
) -> bool:
    if not sys.stdin.isatty():
        raise ConfigureHermesModelError(
            "--apply-to-users requires an interactive terminal for confirmation."
        )

    print()
    print("WARNING: about to rewrite Hermes config for existing users.")
    print(f"Mapping file: {mapping_path}")
    print("Config changes:")
    for line in format_change_summary(
        old_base_url=old_base_url,
        new_base_url=new_base_url,
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        old_api_key=old_api_key,
        new_api_key=new_api_key,
    ):
        print(line)
    print(f"Affected users: {len(targets)}")
    for target in targets:
        print(
            f"- {target.username} ({target.linux_user}) -> {target.systemd_service}"
        )
    print("This will:")
    print("- rewrite each user's ~/.hermes/config.yaml")
    print("- rewrite each user's ~/.hermes/.env")
    print("- restart each user's Hermes systemd service")
    print("- interrupt any in-flight requests on those Hermes instances")
    answer = input("Type APPLY to continue: ").strip()
    return answer == "APPLY"


def apply_model_config_to_users(
    mapping_path: Path,
    *,
    old_base_url: str | None,
    new_base_url: str,
    old_model_name: str | None,
    new_model_name: str,
    old_api_key: str | None,
    new_api_key: str,
) -> None:
    mapping_store = MappingStore(mapping_path)
    targets = mapping_store.load_targets()
    if not targets:
        print("No mapped users found; skipped Hermes service reload.")
        return

    require_root()
    require_binary("systemctl")
    require_binary("useradd")

    if not confirm_apply_to_users(
        mapping_path,
        targets,
        old_base_url=old_base_url,
        new_base_url=new_base_url,
        old_model_name=old_model_name,
        new_model_name=new_model_name,
        old_api_key=old_api_key,
        new_api_key=new_api_key,
    ):
        print("Skipped applying config to existing users. users_mapping.yaml was updated.")
        return

    resolved_config = load_mapping(mapping_path, resolve_env=True)
    for target in targets:
        print(f"Applying config for user: {target.username}")
        install_user_files(resolved_config, target)
        if is_service_active(target.systemd_service):
            restart_service(target.systemd_service)
            wait_for_hermes_models(target.api_key, target.api_server_host, target.api_port)
            print(f"Restarted Hermes service: {target.systemd_service}")
        else:
            print(
                f"Updated config for stopped runtime: {target.systemd_service} "
                "(will apply on next workspace login)"
            )


def extract_current_values(config: dict) -> tuple[str | None, str | None, str | None]:
    hermes = config.get("hermes") if isinstance(config.get("hermes"), dict) else {}
    model = hermes.get("model") if isinstance(hermes.get("model"), dict) else {}
    extra_env = (
        hermes.get("extra_env") if isinstance(hermes.get("extra_env"), dict) else {}
    )

    current_base_url = str(model.get("base_url") or "").strip() or None
    current_model_name = str(model.get("default") or "").strip() or None
    current_api_key = str(model.get("api_key") or extra_env.get("OPENAI_API_KEY") or "").strip() or None
    return current_base_url, current_model_name, current_api_key


def main() -> int:
    args = build_parser().parse_args()
    mapping_path = args.mapping.expanduser().resolve()

    if not mapping_path.parent.exists():
        raise ConfigureHermesModelError(
            f"Parent directory does not exist: {mapping_path.parent}"
        )

    created = not mapping_path.exists()
    config = build_new_config() if created else load_mapping(mapping_path, resolve_env=False)
    current_base_url, current_model_name, current_api_key = extract_current_values(config)
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
    extra_env["OPENAI_API_KEY"] = api_key

    write_mapping(mapping_path, config)
    mapping_path.chmod(0o600)

    action = "Created" if created else "Updated"
    print(f"{action} Hermes model config in: {mapping_path}")
    print(f"Upstream base URL: {base_url}")
    print(f"Default model: {model_name}")
    print(f"Preserved users: {len(users)}")
    print("Updated fields: hermes.model.*, hermes.extra_env.OPENAI_API_KEY")
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
            old_api_key=current_api_key,
            new_api_key=api_key,
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import pwd
import sys
from pathlib import Path
from typing import Any

import yaml

from interface.mapping import DEFAULT_MAPPING_PATH, MappingStore, load_mapping
from interface.model_options import normalize_model_options, patch_user_active_model
from interface.model_proxy_config import get_model_proxy_base_url


class CleanupHermesUserKeysError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove historical upstream model keys from each mapped user's "
            "~/.hermes/config.yaml and .env, then rewrite the active model to "
            "the local model proxy."
        )
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING_PATH,
        help=f"Path to users_mapping.yaml (default: {DEFAULT_MAPPING_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report affected users without writing files.",
    )
    return parser


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise CleanupHermesUserKeysError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CleanupHermesUserKeysError(f"{path} must be a mapping/object.")
    return data


def _contains_key_name(value: Any, key_name: str) -> bool:
    if isinstance(value, dict):
        return key_name in value or any(
            _contains_key_name(child, key_name) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key_name(child, key_name) for child in value)
    return False


def main() -> int:
    args = build_parser().parse_args()
    mapping_path = args.mapping.expanduser().resolve()
    config = load_mapping(mapping_path, resolve_env=True)
    model_options = normalize_model_options(config)
    proxy_base_url = get_model_proxy_base_url(config)
    targets = MappingStore(mapping_path).load_targets()

    if not targets:
        print("No mapped users found.")
        return 0

    for target in targets:
        config_path = target.hermes_home / "config.yaml"
        env_path = target.hermes_home / ".env"
        before_config = _load_yaml_mapping(config_path)
        active_id = model_options.primary_id
        model_cfg = before_config.get("model")
        if isinstance(model_cfg, dict):
            for option in model_options.options:
                if option.matches_model_config(
                    model_cfg, proxy_base_url=proxy_base_url
                ):
                    active_id = option.id
                    break
                configured_model = str(
                    model_cfg.get("default") or model_cfg.get("model") or ""
                ).strip()
                if configured_model == option.model:
                    active_id = option.id
                    break

        option = model_options.get(active_id) or model_options.primary
        env_had_key = False
        if env_path.exists():
            env_had_key = "OPENAI_API_KEY" in env_path.read_text(encoding="utf-8")
        config_had_key = _contains_key_name(before_config, "api_key")

        print(
            f"{target.username}: active={option.id} "
            f"config_api_key={config_had_key} env_openai_key={env_had_key}"
        )
        if args.dry_run:
            continue

        try:
            pwd.getpwnam(target.linux_user)
        except KeyError as exc:
            raise CleanupHermesUserKeysError(
                f"Linux user {target.linux_user!r} does not exist."
            ) from exc
        patch_user_active_model(target, option, proxy_base_url=proxy_base_url)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

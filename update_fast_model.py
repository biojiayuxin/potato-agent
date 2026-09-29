#!/usr/bin/env python3
"""Align the Fast route and user model names with the actual upstream model."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

import yaml

from interface.mapping import (
    DEFAULT_MAPPING_PATH, build_targets_from_config, load_mapping, write_mapping,
)
from interface.model_options import ModelOption, normalize_model_options
from interface.model_proxy_config import (
    get_model_proxy_base_url, get_model_proxy_config_path,
    load_model_proxy_config, write_model_proxy_config,
)
from interface.user_lifecycle_lock import acquire_user_lock, mapping_lifecycle_lock
from interface.user_private_files import read_user_private_text, write_user_private_text


NEW_MODEL = "gpt-6-sol"
NEW_REASONING_EFFORT = "xhigh"
FAST_KEYS = {"fast", "gpt-5.6-terra", NEW_MODEL}


class FastModelUpdateError(RuntimeError):
    pass


def prepare_update(
    mapping: dict[str, Any], proxy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], ModelOption]:
    options = normalize_model_options(mapping)
    candidates = [
        option for option in options.options
        if FAST_KEYS.intersection(
            value.casefold() for value in (option.id, option.name, option.model)
        )
    ]
    if len(candidates) != 1:
        raise FastModelUpdateError("Expected exactly one existing Fast model option.")
    fast = candidates[0]
    updated_mapping, updated_proxy = deepcopy(mapping), deepcopy(proxy)
    entries = updated_mapping["hermes"]["model_options"]["options"]
    public = next(item for item in entries if str(item.get("id", "")).strip() == fast.id)
    routes = [
        item for item in updated_proxy.get("models", [])
        if str(item.get("name") or item.get("model") or "").strip() == fast.name
    ]
    if len(routes) != 1 or routes[0].get("model") != fast.model:
        raise FastModelUpdateError("Fast whitelist and proxy route do not agree.")
    if any(
        item is not routes[0]
        and str(item.get("name") or item.get("model") or "").strip() == NEW_MODEL
        for item in updated_proxy.get("models", [])
    ) or any(option.id != fast.id and option.name == NEW_MODEL for option in options.options):
        raise FastModelUpdateError("The new Fast route name is already in use.")
    for item in (public, routes[0]):
        # Hermes exposes this route name in config.yaml and its system prompt.
        item["name"] = NEW_MODEL
        item["model"] = NEW_MODEL
        item["reasoning_effort"] = NEW_REASONING_EFFORT
    normalize_model_options(updated_mapping)
    return updated_mapping, updated_proxy, fast


def retained_settings(config: dict[str, Any]) -> dict[str, Any]:
    model = config.get("model") or {}
    compression = (config.get("auxiliary") or {}).get("compression") or {}
    return {
        "context_length": model.get("context_length"),
        "api_mode": model.get("api_mode"),
        "compression": {
            key: compression[key]
            for key in ("model", "context_length", "api_mode", "reasoning_effort")
            if key in compression
        },
    }


def check_compression(config: dict[str, Any], fast: ModelOption) -> None:
    compression = (config.get("auxiliary") or {}).get("compression") or {}
    model = str(compression.get("model") or "").strip()
    if not model or model in {fast.name, fast.model}:
        raise FastModelUpdateError(
            "Compression is unset or uses the Fast route. Its previous model must "
            "be confirmed and kept on a separate existing route before applying."
        )


def check_retired_compression_route(config: dict[str, Any], fast: ModelOption) -> None:
    compression = (config.get("auxiliary") or {}).get("compression") or {}
    if fast.name != NEW_MODEL and compression.get("model") == fast.name:
        raise FastModelUpdateError(
            "Compression explicitly uses the retiring Fast route; update its model "
            "reference before renaming Fast."
        )


def patch_current_fast_user(
    config: dict[str, Any], fast: ModelOption, *, proxy_base_url: str,
) -> dict[str, Any]:
    model = config.get("model")
    if not isinstance(model, dict) or not fast.matches_model_config(
        model, proxy_base_url=proxy_base_url,
    ):
        return config
    updated = deepcopy(config)
    updated["model"]["default"] = NEW_MODEL
    updated.setdefault("agent", {})["reasoning_effort"] = NEW_REASONING_EFFORT
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_PATH)
    parser.add_argument("--proxy-config", type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply the checked changes.")
    parser.add_argument(
        "--keep-compression-policy", action="store_true",
        help="Keep compression following Fast when no separate model is configured.",
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise FastModelUpdateError("Run with sudo to access the private model configuration.")
    proxy_path = args.proxy_config or get_model_proxy_config_path(args.mapping)

    with mapping_lifecycle_lock(exclusive=True), ExitStack() as locks:
        mapping = load_mapping(args.mapping, resolve_env=False)
        proxy = load_model_proxy_config(proxy_path)
        updated_mapping, updated_proxy, fast = prepare_update(mapping, proxy)
        overrides = mapping["hermes"].get("config_overrides") or {}
        preserved = retained_settings(overrides)
        preserved["context_length"] = fast.context_length
        preserved["api_mode"] = fast.api_mode
        print(json.dumps({
            "fast_id": fast.id,
            "route_name": {"before": fast.name, "after": NEW_MODEL},
            "model": {"before": fast.model, "after": NEW_MODEL},
            "reasoning_effort": {"before": fast.reasoning_effort, "after": NEW_REASONING_EFFORT},
            "retained": preserved,
        }, ensure_ascii=False, indent=2))

        issues = []
        try:
            check_retired_compression_route(overrides, fast)
        except FastModelUpdateError as exc:
            issues.append(f"Global config: {exc}")
        if fast.context_length is None:
            issues.append("Fast context length is inferred; confirm the current value before applying.")
        if not args.keep_compression_policy:
            try:
                check_compression(overrides, fast)
            except FastModelUpdateError as exc:
                issues.append(f"Global config: {exc}")

        user_changes = []
        for target in build_targets_from_config(mapping):
            path = target.hermes_home / "config.yaml"
            raw = read_user_private_text(target, path)
            if raw is None:
                continue
            config = yaml.safe_load(raw)
            if not isinstance(config, dict):
                raise FastModelUpdateError("A user runtime config is not a mapping.")
            try:
                check_retired_compression_route(config, fast)
            except FastModelUpdateError as exc:
                issues.append(f"User {target.username}: {exc}")
            updated = patch_current_fast_user(config, fast, proxy_base_url=get_model_proxy_base_url(mapping))
            if updated is config:
                continue
            if args.apply:
                try:
                    locks.enter_context(acquire_user_lock(target.username, exclusive=True, blocking=False))
                except BlockingIOError:
                    raise FastModelUpdateError(
                        f"Fast runtime for {target.username} is in use; close it and retry. No files changed."
                    ) from None
                if read_user_private_text(target, path) != raw:
                    raise FastModelUpdateError("A Fast user config changed during planning; retry. No files changed.")
            print(json.dumps({"user": target.username, "retained": retained_settings(config)}))
            if not args.keep_compression_policy:
                try:
                    check_compression(config, fast)
                except FastModelUpdateError as exc:
                    issues.append(f"User {target.username}: {exc}")
            if updated != config:
                user_changes.append((target, path, raw, updated))

        for issue in issues:
            print(issue)
        if not args.apply:
            print("Preview only; no configuration files changed.")
            return 0
        if issues:
            raise FastModelUpdateError("Apply stopped: retained settings need confirmation; no files changed.")

        backup = Path(tempfile.mkdtemp(prefix="fast-model-backup-", dir=args.mapping.parent))
        os.chmod(backup, 0o700)
        def save_backup(name: str, body: str) -> None:
            fd = os.open(backup / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())

        save_backup("users_mapping.yaml", yaml.safe_dump(mapping, sort_keys=False))
        save_backup("model_proxy.yaml", yaml.safe_dump(proxy, sort_keys=False))
        for index, (_, _, raw, _) in enumerate(user_changes):
            save_backup(f"user-{index}.yaml", raw)
        save_backup("users.json", json.dumps([
            {"backup": f"user-{index}.yaml", "path": str(path), "username": target.username}
            for index, (target, path, _, _) in enumerate(user_changes)
        ]))
        print(f"Private backup: {backup}")
        written_users = []
        try:
            write_model_proxy_config(proxy_path, updated_proxy)
            write_mapping(args.mapping, updated_mapping)
            for target, path, raw, updated in user_changes:
                written_users.append((target, path, raw))
                write_user_private_text(target, path, yaml.safe_dump(updated, sort_keys=False))
            if load_mapping(args.mapping) != updated_mapping or load_model_proxy_config(proxy_path) != updated_proxy:
                raise FastModelUpdateError("Central configuration readback did not match.")
            for target, path, _, updated in user_changes:
                if yaml.safe_load(read_user_private_text(target, path)) != updated:
                    raise FastModelUpdateError("User configuration readback did not match.")
        except Exception:
            rollback_failed = False
            for target, path, raw in reversed(written_users):
                try:
                    write_user_private_text(target, path, raw)
                except Exception:
                    rollback_failed = True
            for writer, path, original in (
                (write_mapping, args.mapping, mapping),
                (write_model_proxy_config, proxy_path, proxy),
            ):
                try:
                    writer(path, original)
                except Exception:
                    rollback_failed = True
            if rollback_failed:
                raise FastModelUpdateError(
                    f"Update and automatic rollback failed; restore private backup at {backup}."
                ) from None
            raise FastModelUpdateError("Update failed; restored the previous configuration.") from None
        print("Applied Fast: gpt-6-sol / xhigh. Retained settings verified unchanged.")
        print("Existing Fast runtimes must reload their config after current responses finish.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FastModelUpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # YAML parser errors may contain credential-bearing source lines.
        print(f"error: Fast update failed ({type(exc).__name__}); private values omitted.", file=sys.stderr)
        raise SystemExit(1)

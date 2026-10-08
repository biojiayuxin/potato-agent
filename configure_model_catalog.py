#!/usr/bin/env python3
"""Initialize, validate or update the single protected schema-v2 model catalog.

Preview is the default. Changes to an option's upstream create a new backend
when necessary, retaining old backends for already admitted signed requests.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import secrets
import sqlite3
from pathlib import Path
import tempfile

import yaml

from interface.mapping import DEFAULT_MAPPING_PATH, MappingStore, load_mapping, write_mapping
from interface.model_catalog import ModelCatalogError, public_catalog, validate_catalog
from interface.model_proxy_config import get_model_proxy_config_path, load_model_proxy_config, write_model_proxy_config
from interface.user_lifecycle_lock import mapping_lifecycle_lock
from interface.secure_paths import ensure_private_directory
from interface.hermes_service import require_root
from interface.user_private_files import read_user_private_text


def update_option(config: dict, option_id: str, *, model: str | None = None, display_name: str | None = None,
                  reasoning_effort: str | None = None, context_length: int | None = None) -> dict:
    result = deepcopy(validate_catalog(config))
    if option_id not in result["options"]:
        raise ModelCatalogError("Unknown stable model option ID")
    option = result["options"][option_id]
    if model is not None and model != result["backends"][option["backend"]]["model"]:
        backend = deepcopy(result["backends"][option["backend"]])
        backend["model"] = model
        backend_id = f"{option_id}-{len(result['backends']) + 1}"
        while backend_id in result["backends"]:
            backend_id += "x"
        result["backends"][backend_id] = backend
        option["backend"] = backend_id
    for key, value in (("display_name", display_name), ("reasoning_effort", reasoning_effort), ("context_length", context_length)):
        if value is not None:
            option[key] = value
    return validate_catalog(result)


def validate_mapping(mapping: dict, *, initialize: bool = False) -> dict:
    result = deepcopy(mapping)
    hermes = result.setdefault("hermes", {})
    if not isinstance(hermes, dict) or "model" in hermes or "model_options" in hermes:
        raise ModelCatalogError("Legacy model mappings are unsupported")
    if initialize:
        if result.get("users"):
            raise ModelCatalogError("Initialize only on a new installation without mapped users")
        hermes["model_catalog"] = True
        result.setdefault("users", [])
    elif not hermes.get("model_catalog"):
        raise ModelCatalogError("Mapping must enable the schema-v2 catalog")
    return result


def validate_session_selections(catalog: dict, db_path: Path) -> None:
    """Read-only deployment check before removing support for old option IDs."""
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'session_model_state'").fetchone():
            return
        selected = {row[0] for row in conn.execute("SELECT DISTINCT model_id FROM session_model_state")}
    if selected - set(catalog["options"]):
        raise ModelCatalogError("Session selections require migration before deploying this version")


def validate_user_model_routes(catalog: dict, mapping_path: Path) -> None:
    """Require stable option IDs in persisted bootstrap and auxiliary settings."""
    option_ids = set(catalog["options"])
    for target in MappingStore(mapping_path).load_targets():
        raw = read_user_private_text(target, target.hermes_home / "config.yaml")
        try:
            config = yaml.safe_load(raw or "")
        except yaml.YAMLError:
            raise ModelCatalogError("Invalid user runtime configuration") from None
        if not isinstance(config, dict) or not isinstance(config.get("model"), dict):
            raise ModelCatalogError("User runtime requires a stable bootstrap model ID")
        model = config["model"]
        if model.get("default") not in option_ids:
            raise ModelCatalogError("User runtime requires a stable bootstrap model ID")
        if model.get("model") and model["model"] not in option_ids:
            raise ModelCatalogError("User runtime requires a stable bootstrap model ID")
        auxiliary = config.get("auxiliary") or {}
        if not isinstance(auxiliary, dict):
            raise ModelCatalogError("Invalid user auxiliary configuration")
        for entry in auxiliary.values():
            if isinstance(entry, dict) and entry.get("model") and entry["model"] not in option_ids:
                raise ModelCatalogError("User auxiliary models must use stable option IDs")


def apply_catalog(mapping_path: Path, proxy_path: Path, mapping: dict, catalog: dict, *, initialize: bool = False) -> None:
    """Publish validated configuration; restore existing files on failure."""
    catalog = deepcopy(validate_catalog(catalog))
    mapping = validate_mapping(mapping, initialize=initialize)
    old_mapping = load_mapping(mapping_path, resolve_env=False) if mapping_path.exists() else None
    if initialize:
        if proxy_path.exists() or proxy_path.is_symlink():
            raise ModelCatalogError("Catalog already exists; initialization cannot overwrite it")
        old_proxy = None
    else:
        old_proxy = validate_catalog(load_model_proxy_config(proxy_path))
    ensure_private_directory(proxy_path.parent)
    backup = Path(tempfile.mkdtemp(prefix="model-catalog-backup-", dir=proxy_path.parent))
    os.chmod(backup, 0o700)
    for name, value in (("users_mapping.yaml", old_mapping), ("model_proxy.yaml", old_proxy)):
        if value is None:
            continue
        descriptor = os.open(backup / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            yaml.safe_dump(value, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
    try:
        write_model_proxy_config(proxy_path, catalog)
        write_mapping(mapping_path, mapping)
    except Exception:
        if old_proxy is None:
            proxy_path.unlink(missing_ok=True)
        else:
            write_model_proxy_config(proxy_path, old_proxy)
        if old_mapping is None:
            mapping_path.unlink(missing_ok=True)
        else:
            write_mapping(mapping_path, old_mapping)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING_PATH)
    parser.add_argument("--proxy-config", type=Path)
    parser.add_argument("--initialize-from", type=Path, help="Protected schema-v2 input file for a new installation")
    parser.add_argument("--check-session-db", type=Path, help="Verify saved option IDs without modifying SQLite")
    parser.add_argument("--check-user-configs", action="store_true", help="Verify user bootstrap and auxiliary model IDs without writing files")
    parser.add_argument("--option")
    parser.add_argument("--model")
    parser.add_argument("--display-name")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--context-length", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require_root()
    proxy_path = args.proxy_config or get_model_proxy_config_path(args.mapping)
    with mapping_lifecycle_lock(exclusive=True):
        initialize = args.initialize_from is not None
        mapping = load_mapping(args.mapping, resolve_env=False) if args.mapping.exists() else {}
        mapping = validate_mapping(mapping, initialize=initialize)
        if initialize and (proxy_path.exists() or proxy_path.is_symlink()):
            raise ModelCatalogError("Catalog already exists; initialization cannot overwrite it")
        catalog = load_model_proxy_config(args.initialize_from if initialize else proxy_path)
        if initialize:
            catalog.setdefault("catalog_signing_key", secrets.token_urlsafe(48))
        validate_catalog(catalog)
        if args.check_session_db:
            validate_session_selections(catalog, args.check_session_db)
        if args.check_user_configs:
            validate_user_model_routes(catalog, args.mapping)
        if args.option:
            catalog = update_option(catalog, args.option, model=args.model, display_name=args.display_name,
                                    reasoning_effort=args.reasoning_effort, context_length=args.context_length)
        elif any(value is not None for value in (args.model, args.display_name, args.reasoning_effort, args.context_length)):
            raise ModelCatalogError("Option updates require --option")
        print(json.dumps(public_catalog(catalog, include_routes=False), ensure_ascii=False, indent=2))
        if args.apply:
            apply_catalog(args.mapping, proxy_path, mapping, catalog, initialize=initialize)
            print("Catalog applied.")
        else:
            print("Preview only. Use --apply to publish the catalog.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Third-party YAML/filesystem exceptions may contain credential values.
        print(f"Catalog update failed ({type(exc).__name__}); protected values omitted.")
        raise SystemExit(1) from None

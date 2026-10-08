"""Authoritative model catalog and authenticated, credential-free turn routes.

Only model_proxy.yaml is editable configuration. A signed route is an immutable
public snapshot, not another configuration source. Credentials stay in that
file; changing an endpoint requires a new backend ID so admitted turns keep
their original destination. Rotating a backend's API key is supported.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any

from interface.model_options import ModelOptionsError

ROUTE_MARKER = ".pmc1."
ID_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
SNAPSHOT_FIELDS = frozenset({
    "id", "display_name", "backend", "model", "provider", "api_mode",
    "reasoning_effort", "context_length", "config_revision",
})
OPTION_FIELDS = frozenset({"display_name", "backend", "reasoning_effort", "context_length"})


class ModelCatalogError(ModelOptionsError):
    pass


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _text(value: Any, field: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ModelCatalogError(f"Invalid catalog {field}")
    return value.strip()


def _id(value: Any) -> str:
    value = _text(value, "ID", 64)
    if not ID_RE.fullmatch(value):
        raise ModelCatalogError("Invalid catalog ID")
    return value


def validate_catalog(config: dict[str, Any]) -> dict[str, Any]:
    """Validate without including any configuration values in errors."""
    if not isinstance(config, dict) or config.get("schema_version") != 2:
        raise ModelCatalogError("Model catalog schema_version must be 2")
    key = _text(config.get("catalog_signing_key"), "signing key", 256)
    if len(key) < 43:
        raise ModelCatalogError("Catalog signing key must have at least 256 bits of random entropy")
    backends, options = config.get("backends"), config.get("options")
    if not isinstance(backends, dict) or not backends or not isinstance(options, dict) or not 1 <= len(options) <= 4:
        raise ModelCatalogError("Catalog requires backends and one to four options")
    for backend_id, backend in backends.items():
        _id(backend_id)
        if not isinstance(backend, dict):
            raise ModelCatalogError("Invalid catalog backend")
        if ROUTE_MARKER in _text(backend.get("model"), "upstream model"):
            raise ModelCatalogError("Upstream model contains a reserved route marker")
        url = _text(backend.get("base_url"), "backend address", 2048)
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError:
            raise ModelCatalogError("Invalid catalog backend address") from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelCatalogError("Invalid catalog backend address")
        _text(backend.get("api_key"), "backend credential", 8192)
        if backend.get("api_mode", "codex_responses") not in {"codex_responses", "chat_completions"}:
            raise ModelCatalogError("Invalid catalog API mode")
    for option_id, option in options.items():
        _id(option_id)
        if not isinstance(option, dict) or option.get("backend") not in backends:
            raise ModelCatalogError("Invalid catalog option backend")
        if set(option) - OPTION_FIELDS:
            raise ModelCatalogError("Unsupported catalog option fields")
        _text(option.get("display_name"), "display name", 128)
        if option.get("reasoning_effort") not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ModelCatalogError("Invalid catalog reasoning effort")
        context = option.get("context_length")
        if isinstance(context, bool) or not isinstance(context, int) or context < 64000:
            raise ModelCatalogError("Catalog context length must be at least 64000")
    for field in ("primary_option_id", "default_option_id"):
        if config.get(field) not in options:
            raise ModelCatalogError(f"Catalog {field} must reference an option")
    return config


def _sign(config: dict, payload: bytes, backend: dict) -> str:
    # Bind the snapshot to its original endpoint without exposing its URL or a
    # dictionary-attackable URL hash. API key rotation does not invalidate it.
    address = backend["base_url"].strip().rstrip("/").encode()
    return hmac.new(config["catalog_signing_key"].encode(), payload + b"\0" + address, hashlib.sha256).hexdigest()


def option_snapshot(config: dict, option_id: str) -> dict:
    option = config["options"][option_id]
    backend = config["backends"][option["backend"]]
    snapshot = {
        "id": option_id, "display_name": option["display_name"], "backend": option["backend"],
        "model": backend["model"], "provider": "custom",
        "api_mode": backend.get("api_mode", "codex_responses"),
        "reasoning_effort": option["reasoning_effort"], "context_length": option["context_length"],
    }
    snapshot["config_revision"] = _sign(config, _json(snapshot), backend)
    return snapshot


def signed_route(config: dict, snapshot: dict) -> str:
    body = _json(snapshot)
    encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
    signature = _sign(config, body, config["backends"][snapshot["backend"]])
    return snapshot["model"] + ROUTE_MARKER + encoded + "." + signature


def resolve_route(config: dict, route: str) -> tuple[dict, dict]:
    """Resolve an authenticated historical snapshot or a current stable option ID."""
    validate_catalog(config)
    if ROUTE_MARKER not in route:
        if route not in config["options"]:
            raise ModelCatalogError("Model option is not available")
        snapshot = option_snapshot(config, route)
        return snapshot, config["backends"][snapshot["backend"]]
    try:
        if len(route) > 4096:
            raise ValueError()
        prefix, token = route.split(ROUTE_MARKER, 1)
        encoded, signature = token.rsplit(".", 1)
        body = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        snapshot = json.loads(body)
        if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
            raise ValueError()
        backend = config["backends"][snapshot["backend"]]
        if not hmac.compare_digest(signature, _sign(config, body, backend)):
            raise ValueError()
        if prefix != snapshot["model"] or snapshot["id"] not in config["options"]:
            raise ValueError()
        return snapshot, backend
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ModelCatalogError("Model configuration snapshot is invalid or retired") from None


def public_catalog(config: dict, *, include_routes: bool = True) -> dict:
    validate_catalog(config)
    options = []
    for option_id in config["options"]:
        snapshot = option_snapshot(config, option_id)
        entry = {key: value for key, value in snapshot.items() if key != "backend"}
        entry["name"] = option_id
        if include_routes:
            entry["route"] = signed_route(config, snapshot)
        options.append(entry)
    return {"primary": config["primary_option_id"], "default": config["default_option_id"], "options": options}


def load_public_catalog() -> dict:
    from interface.model_proxy_config import get_model_proxy_config_path, load_model_proxy_config
    return public_catalog(load_model_proxy_config(get_model_proxy_config_path()))

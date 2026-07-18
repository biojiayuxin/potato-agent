"""Small provider readiness helper that does not import the classic CLI."""

from __future__ import annotations

from hermes_cli.runtime_provider import resolve_runtime_provider
from runtime_profile import get_runtime_profile


def configured_runtime() -> dict:
    profile = get_runtime_profile()
    if profile is None:
        return {}
    runtime = resolve_runtime_provider(requested="custom")
    if runtime.get("provider") not in profile.model_providers:
        return {}
    if runtime.get("api_mode") not in profile.api_modes:
        return {}
    if not str(runtime.get("model") or "").strip():
        return {}
    if not str(runtime.get("base_url") or "").strip():
        return {}
    return runtime


def has_configured_runtime() -> bool:
    try:
        return bool(configured_runtime())
    except Exception:
        return False

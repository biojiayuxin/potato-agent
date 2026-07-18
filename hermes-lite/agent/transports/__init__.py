"""Profile-limited transport registry for provider response normalization."""

import importlib

from agent.transports.types import (
    NormalizedResponse,
    ToolCall,
    Usage,
    build_tool_call,
    map_finish_reason,
)  # noqa: F401

_REGISTRY: dict = {}
_TRANSPORT_MODULES = {
    "chat_completions": "agent.transports.chat_completions",
    "codex_responses": "agent.transports.codex",
}


def register_transport(api_mode: str, transport_cls: type) -> None:
    """Register a transport class for an api_mode string."""
    from runtime_profile import get_runtime_profile

    profile = get_runtime_profile()
    if profile is not None and api_mode not in profile.api_modes:
        return
    _REGISTRY[api_mode] = transport_cls


def get_transport(api_mode: str):
    """Get a transport instance for the given api_mode.

    Returns None if no transport is registered for this api_mode.
    This allows gradual migration — call sites can check for None
    and fall back to the legacy code path.
    """
    from runtime_profile import get_runtime_profile

    profile = get_runtime_profile()
    if profile is not None and api_mode not in profile.api_modes:
        return None
    cls = _REGISTRY.get(api_mode)
    if cls is None:
        module = _TRANSPORT_MODULES.get(api_mode)
        if module is None:
            return None
        importlib.import_module(module)
        cls = _REGISTRY.get(api_mode)
    if cls is None:
        return None
    return cls()

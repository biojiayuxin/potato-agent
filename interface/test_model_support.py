"""Public catalog fixtures; no real provider or privileged helper is used."""
from interface.model_catalog import public_catalog


def make_catalog():
    return {
        "schema_version": 2,
        "catalog_signing_key": "test-signing-key-" + "s" * 64,
        "primary_option_id": "deep", "default_option_id": "fast",
        "backends": {
            "deep": {"model": "upstream-deep", "base_url": "https://deep.example/v1",
                     "api_key": "UPSTREAM_DEEP_SECRET", "api_mode": "chat_completions"},
            "fast": {"model": "upstream-fast", "base_url": "https://fast.example/v1",
                     "api_key": "UPSTREAM_FAST_SECRET", "api_mode": "chat_completions"},
        },
        "options": {
            "deep": {"display_name": "Deep", "backend": "deep", "context_length": 200000, "reasoning_effort": "high"},
            "fast": {"display_name": "Fast", "backend": "fast", "context_length": 100000, "reasoning_effort": "medium"},
        },
    }


def install_catalog(monkeypatch, catalog):
    from interface.privileged_client import PrivilegedClient
    monkeypatch.setattr("interface.model_catalog.load_public_catalog", lambda: public_catalog(catalog))
    monkeypatch.setattr(PrivilegedClient, "get_model_catalog", lambda self: public_catalog(catalog))

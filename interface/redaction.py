from __future__ import annotations

import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any


_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "id_token",
        "jwt",
        "key",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)
_FALLBACK_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-|AIza|pplx-|fal_|AKIA)[A-Za-z0-9_=-]{10,}"
)
_FALLBACK_AUTH_RE = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"
)
_FALLBACK_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


@lru_cache(maxsize=1)
def _lite_redactor() -> ModuleType | None:
    path = Path(__file__).resolve().parent.parent / "hermes-lite" / "agent" / "redact.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_potato_lite_redact", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def force_redact_text(value: Any) -> str:
    text = "" if value is None else str(value)
    module = _lite_redactor()
    if module is not None:
        redact = getattr(module, "redact_sensitive_text", None)
        if callable(redact):
            try:
                return str(redact(text, force=True))
            except Exception:
                pass
    text = _FALLBACK_SECRET_RE.sub("***", text)
    text = _FALLBACK_AUTH_RE.sub(lambda match: match.group(1) + "***", text)
    return _FALLBACK_JWT_RE.sub("***", text)


def force_redact_value(value: Any, *, key: str = "") -> Any:
    if key.casefold() in _SENSITIVE_KEYS:
        return "***"
    if isinstance(value, str):
        return force_redact_text(value)
    if isinstance(value, dict):
        return {
            str(item_key): force_redact_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [force_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [force_redact_value(item) for item in value]
    return value

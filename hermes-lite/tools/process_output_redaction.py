"""Mandatory redaction for terminal and managed-process output."""

from typing import Any


def redact_process_output(value: Any) -> str:
    """Return process output with secret redaction enforced by the caller."""
    from agent.redact import redact_sensitive_text

    text = value if isinstance(value, str) else str(value)
    return redact_sensitive_text(text, force=True)

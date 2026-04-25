from __future__ import annotations

import os


if os.getenv("POTATO_AGENT_ENABLE_APPROVAL_PATCH", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}:
    try:
        from hermes_api_approval_patch import apply_patch

        apply_patch()
    except Exception:
        # Preserve original Hermes startup behaviour if the optional patch fails.
        pass

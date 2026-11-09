"""Project model content into display text without exposing encoded media."""

from __future__ import annotations

import re
from typing import Any

_IMAGE_DATA = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[a-zA-Z0-9+/=_-]+", re.IGNORECASE)


def display_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _IMAGE_DATA.sub("[image]", content)
    if isinstance(content, list):
        return "\n".join(filter(None, (display_content(part) for part in content)))
    if isinstance(content, dict):
        if content.get("type") in {"image", "image_url", "input_image"}:
            return "[image]"
        if content.get("_multimodal"):
            return display_content(content.get("text_summary") or content.get("content"))
        return display_content(content.get("text", ""))
    return str(content)

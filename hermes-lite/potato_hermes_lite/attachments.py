"""Local attachment parsing shared by the Potato TUI gateway."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg", ".ico"}
)


def split_path_input(raw: str) -> tuple[str, str]:
    raw = str(raw or "").strip()
    if not raw:
        return "", ""
    if raw[0] in {'"', "'"}:
        quote = raw[0]
        pos = 1
        while pos < len(raw):
            char = raw[pos]
            if char == "\\" and pos + 1 < len(raw):
                pos += 2
                continue
            if char == quote:
                return raw[1:pos], raw[pos + 1 :].strip()
            pos += 1
        return raw[1:], ""

    pos = 0
    while pos < len(raw):
        char = raw[pos]
        if char == "\\" and pos + 1 < len(raw) and raw[pos + 1] == " ":
            pos += 2
        elif char == " ":
            break
        else:
            pos += 1
    return raw[:pos].replace("\\ ", " "), raw[pos:].strip()


def resolve_attachment_path(raw_path: str) -> Path | None:
    token = str(raw_path or "").strip()
    if not token:
        return None
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        token = token[1:-1].strip()
    token = token.replace("\\ ", " ")
    if not token:
        return None

    expanded = token
    if token.startswith("file://"):
        try:
            parsed = urlparse(token)
            expanded = unquote(parsed.path or "")
            if parsed.netloc and os.name == "nt":
                expanded = f"//{parsed.netloc}{expanded}"
        except Exception:
            expanded = token
    expanded = os.path.expandvars(os.path.expanduser(expanded))
    if os.name != "nt":
        normalized = expanded.replace("\\", "/")
        if (
            len(normalized) >= 3
            and normalized[1] == ":"
            and normalized[2] == "/"
            and normalized[0].isalpha()
        ):
            expanded = f"/mnt/{normalized[0].lower()}/{normalized[3:]}"
    path = Path(expanded)
    if not path.is_absolute():
        path = Path(os.getenv("TERMINAL_CWD", os.getcwd())) / path
    try:
        resolved = path.resolve()
        return resolved if resolved.is_file() else None
    except OSError:
        return None


def detect_file_drop(user_input: str) -> dict | None:
    if not isinstance(user_input, str):
        return None
    stripped = user_input.strip()
    if not stripped:
        return None
    starts_like_path = (
        stripped.startswith(("/", "~", "./", "../", "file://", "'/", "\"/", "'~", "\"~"))
        or (
            len(stripped) >= 3
            and stripped[0].isalpha()
            and stripped[1] == ":"
            and stripped[2] in {"\\", "/"}
        )
    )
    if not starts_like_path:
        return None

    direct = resolve_attachment_path(stripped)
    if direct is not None:
        return {"path": direct, "is_image": direct.suffix.lower() in IMAGE_EXTENSIONS, "remainder": ""}

    first, remainder = split_path_input(stripped)
    path = resolve_attachment_path(first)
    if path is None and " " in stripped and stripped[0] not in {"'", '"'}:
        for position in reversed([index for index, char in enumerate(stripped) if char == " "]):
            candidate = resolve_attachment_path(stripped[:position].rstrip())
            if candidate is not None:
                path = candidate
                remainder = stripped[position + 1 :].strip()
                break
    if path is None:
        return None
    return {"path": path, "is_image": path.suffix.lower() in IMAGE_EXTENSIONS, "remainder": remainder}

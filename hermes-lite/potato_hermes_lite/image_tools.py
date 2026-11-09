"""Load original image bytes for the main model, without auxiliary inference."""

from __future__ import annotations

import base64
import io
import json
import tempfile
import warnings
from pathlib import Path

from tools.registry import registry
from tools.vision_tools import _download_image, _validate_image_url_async

MAX_IMAGE_BYTES = 50 * 1024 * 1024
SUPPORTED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


def load_image(path: Path) -> dict:
    """Validate decoded image content, but preserve every original byte."""
    from agent.file_safety import raise_if_read_blocked
    from PIL import Image

    try:
        path = path.resolve()
        raise_if_read_blocked(str(path))
        with path.open("rb") as stream:
            raw = stream.read(MAX_IMAGE_BYTES + 1)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("Image is empty or exceeds the 50 MiB input limit")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as img:
                mime = SUPPORTED_FORMATS.get(img.format)
                if mime is None:
                    raise ValueError("Unsupported image format; render or convert to PNG, JPEG, WebP or GIF")
                width, height = img.size
                img.verify()
        return {"url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
                "size_bytes": len(raw), "width": width, "height": height, "mime": mime}
    except ValueError:
        raise
    except Exception:
        raise ValueError("Image could not be read or validated; check the file or upload it again") from None


def image_result(source: str, loaded: dict, question: str = "") -> dict:
    note = f"Image source: {source}\nUse read_image to reload this source if the image is no longer in context."
    if question:
        note += f"\nQuestion: {question}"
    return {"_multimodal": True,
            "content": [{"type": "text", "text": note},
                        {"type": "image_url", "image_url": {"url": loaded["url"]}}],
            "text_summary": note,
            "meta": {"image_url": source, "native_vision": True,
                     **{key: loaded[key] for key in ("size_bytes", "width", "height")}}}


async def read_image(image_url: str, question: str = ""):
    from potato_hermes_lite.attachments import resolve_attachment_path
    from tools.interrupt import is_interrupted

    try:
        if is_interrupted():
            raise ValueError("Image loading interrupted")
        if not isinstance(image_url, str) or not image_url.strip():
            raise ValueError("image_url must be a local path or HTTP(S) URL")
        source = image_url.strip()
        if source.startswith(("http://", "https://")):
            if not await _validate_image_url_async(source):
                raise ValueError("Image URL is not allowed")
            with tempfile.TemporaryDirectory(prefix="potato-image-") as directory:
                path = Path(directory) / "image"
                await _download_image(source, path)
                loaded = load_image(path)
        else:
            path = resolve_attachment_path(source)
            if path is None:
                raise ValueError("Image file is unavailable; regenerate it or upload it again")
            source = str(path)
            loaded = load_image(path)
        if is_interrupted():
            raise ValueError("Image loading interrupted")
        return image_result(source, loaded, question)
    except Exception:
        # Do not expose source paths, URL credentials or transport details in errors/logs.
        return json.dumps({"success": False, "error":
                          "Image could not be loaded. Check access, size (50 MiB maximum), and format "
                          "(PNG/JPEG/WebP/GIF); regenerate it or upload it again. No visual analysis was performed."})


READ_IMAGE_SCHEMA = {
    "name": "read_image",
    "description": "Load a local image or HTTP(S) image into your visual context. Use after rendering PDF pages or crops to PNG. Returns original pixels for you to inspect, without a separate vision model.",
    "parameters": {"type": "object", "properties": {
        "image_url": {"type": "string", "description": "Local image path or HTTP(S) URL"},
        "question": {"type": "string", "description": "Optional focus for your inspection"},
    }, "required": ["image_url"]},
}

registry.deregister("vision_analyze")
registry.register(name="read_image", toolset="vision", schema=READ_IMAGE_SCHEMA,
                  handler=lambda args, **kw: read_image(args.get("image_url", ""), args.get("question", "")),
                  check_fn=lambda: True, is_async=True, description="Load image pixels", emoji="🖼️")

"""Potato screenshot tool: capture pixels without calling a vision model."""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from pathlib import Path

from tools import browser_tool as browser
from tools.registry import registry
from potato_hermes_lite.image_tools import image_result, load_image


def browser_image(question: str = "", annotate: bool = False, task_id: str | None = None):
    from hermes_constants import get_hermes_dir

    try:
        key = browser._last_session_key(task_id or "default")
        blocked = browser._blocked_current_page_response(key)
        if blocked:
            return blocked
        directory = get_hermes_dir("cache/screenshots", "browser_screenshots")
        directory.mkdir(parents=True, exist_ok=True)
        browser._cleanup_old_screenshots(directory, max_age_hours=24)
        path = directory / f"browser_screenshot_{uuid.uuid4().hex}.png"
        args = ["--annotate"] if annotate else []
        engine = browser._get_browser_engine()
        if engine == "lightpanda" and browser._should_inject_engine(engine):
            result = browser._chrome_fallback_screenshot(key, args, browser._get_command_timeout())
        else:
            result = browser._run_browser_command(key, "screenshot", [*args, "--full", str(path)])
        if not result.get("success"):
            raise ValueError("Screenshot capture failed")
        actual = Path(result.get("data", {}).get("path") or path)
        loaded = load_image(actual)
        if actual != path:
            shutil.copyfile(actual, path)
        output = image_result(str(path), loaded, question)
        output["meta"]["screenshot_path"] = str(path)
        annotations = result.get("data", {}).get("annotations")
        if annotate and annotations:
            output["content"][0]["text"] += "\nAnnotations: " + json.dumps(annotations, ensure_ascii=False)
        return output
    except Exception:
        return json.dumps({"success": False, "error":
                          "Could not capture a readable browser screenshot. No visual analysis was performed."})


schema = copy.deepcopy(browser._BROWSER_SCHEMA_MAP["browser_vision"])
schema["description"] = "Capture the current browser page as an image for your own visual inspection. Returns original screenshot pixels without a separate vision model."
registry.register(name="browser_vision", toolset="browser", schema=schema,
                  handler=lambda args, **kw: browser_image(args.get("question", ""), args.get("annotate", False), kw.get("task_id")),
                  check_fn=browser.check_browser_requirements, description="Capture browser image",
                  emoji="📸", override=True)

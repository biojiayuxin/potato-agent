from __future__ import annotations

import asyncio
import base64
import copy
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from potato_hermes_lite.attachments import build_image_prompt, normalize_attachments
from potato_hermes_lite.image_tools import load_image, read_image
from potato_hermes_lite.native_images import ImageChatTransport, install_agent_image_adapter


def _png(tmp_path, name="page.png"):
    path = tmp_path / name
    Image.new("RGB", (32, 24), (10, 100, 220)).save(path, format="PNG")
    return path


def test_local_image_bytes_and_true_mime_are_preserved(tmp_path, monkeypatch):
    path = _png(tmp_path, "page.jpg")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    output = asyncio.run(read_image(path.name, "Inspect the labels"))
    url = output["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == path.read_bytes()
    assert str(path) in output["text_summary"]
    assert "Inspect the labels" in output["content"][0]["text"]


def test_remote_image_is_loaded_not_forwarded_as_a_url(tmp_path, monkeypatch):
    from potato_hermes_lite import image_tools
    path = _png(tmp_path)
    destinations = []

    async def validate(url):
        return url == "https://example.test/image"

    async def download(url, destination):
        destinations.append(destination)
        destination.write_bytes(path.read_bytes())

    monkeypatch.setattr(image_tools, "_validate_image_url_async", validate)
    monkeypatch.setattr(image_tools, "_download_image", download)
    result = asyncio.run(read_image("https://example.test/image"))
    assert result["content"][1]["image_url"]["url"] == load_image(path)["url"]
    assert not destinations[0].exists()
    blocked = json.loads(asyncio.run(read_image("https://private.test/image")))
    assert blocked["success"] is False
    assert len(destinations) == 1


def test_invalid_images_fail_before_submission_and_disabled_capability_is_explicit(tmp_path):
    path = _png(tmp_path)
    broken = tmp_path / "broken.png"
    broken.write_text("not an image")
    with pytest.raises(ValueError, match="declared image capability"):
        build_image_prompt("look", [{"path": str(path)}], [], supports_vision=False)
    with pytest.raises(ValueError, match="could not be read"):
        build_image_prompt("look", [{"path": str(path)}, {"path": str(broken)}], [], supports_vision=True)
    result = json.loads(asyncio.run(read_image(str(broken))))
    assert result["success"] is False
    assert str(tmp_path) not in result["error"]


def test_multi_image_prompt_deduplicates_and_preserves_ordinary_files(tmp_path):
    first, second = _png(tmp_path, "one.png"), _png(tmp_path, "two.png")
    parts = build_image_prompt("compare", [{"path": str(first)}, {"path": "report.pdf"}],
                               [str(first), str(second)], supports_vision=True)
    assert [p["image_url"]["url"] for p in parts[1:]] == [load_image(first)["url"], load_image(second)["url"]]
    assert str(first) in parts[0]["text"] and str(second) in parts[0]["text"]
    assert build_image_prompt("report", [{"path": "report.pdf"}], [], supports_vision=False) == "report"


@pytest.mark.parametrize("value", [{}, [None], [{"path": "x", "size": True}], [{"path": "x", "url": "remote"}]])
def test_invalid_attachment_rpc_shape(value):
    with pytest.raises(ValueError):
        normalize_attachments(value)


def test_chat_image_observations_follow_complete_tool_batch_without_mutating_history(tmp_path):
    result = asyncio.run(read_image(str(_png(tmp_path))))
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "image", "type": "function", "function": {"name": "read_image", "arguments": "{}"}},
                                               {"id": "text", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]},
        {"role": "tool", "name": "read_image", "tool_call_id": "image", "content": result["content"]},
        {"role": "tool", "name": "terminal", "tool_call_id": "text", "content": "finished"},
    ]
    original = copy.deepcopy(messages)
    converted = ImageChatTransport().convert_messages(messages, model="custom-alias")
    assert [m["role"] for m in converted] == ["assistant", "tool", "tool", "user"]
    assert isinstance(converted[1]["content"], str)
    assert converted[-1]["content"][1] == result["content"][1]
    assert "tool_call_id=image" in converted[-1]["content"][0]["text"]
    assert messages == original
    with pytest.raises(ValueError, match="incomplete"):
        ImageChatTransport().convert_messages(messages[:2])


def test_capability_adapter_is_per_session_and_never_uses_auxiliary_vision(tmp_path):
    result = asyncio.run(read_image(str(_png(tmp_path))))
    first = SimpleNamespace(_potato_model_config={"supports_vision": True})
    second = SimpleNamespace(_potato_model_config={"supports_vision": False})
    for agent in (first, second):
        install_agent_image_adapter(agent)
    assert first._model_supports_vision() and not second._model_supports_vision()
    assert first._tool_result_content_for_active_model("read_image", result) == result["content"]
    assert json.loads(second._tool_result_content_for_active_model("read_image", result))["success"] is False
    with pytest.raises(ValueError, match="declared image capability"):
        second._prepare_messages_for_non_vision_model([{"role": "user", "content": result["content"]}])
    first._potato_model_config = {"supports_vision": False}
    assert not first._model_supports_vision()


@pytest.mark.parametrize("config", [None, {}])
def test_default_and_legacy_sessions_receive_original_images(config, tmp_path):
    agent = SimpleNamespace(_potato_model_config=config)
    install_agent_image_adapter(agent)
    result = asyncio.run(read_image(str(_png(tmp_path))))
    assert agent._model_supports_vision()
    assert agent._tool_result_content_for_active_model("read_image", result) == result["content"]
    messages = [{"role": "user", "content": result["content"]}]
    assert agent._prepare_messages_for_non_vision_model(messages) is messages


def test_retired_plugin_cannot_make_an_auxiliary_request(monkeypatch):
    from tools import vision_tools
    from tools.registry import registry
    monkeypatch.setattr(vision_tools, "async_call_llm", lambda **kw: pytest.fail("auxiliary vision called"))
    result = json.loads(asyncio.run(vision_tools.vision_analyze_tool("unused.png", "Inspect")))
    assert result["success"] is False
    assert "read_image" in result["error"]
    assert registry.get_entry("vision_analyze") is None


def test_browser_capture_returns_pixels_without_vision_backend(runtime_paths, tmp_path, monkeypatch):
    from potato_hermes_lite import browser_images
    from tools import browser_tool
    import hermes_constants

    path = _png(tmp_path)
    monkeypatch.setattr(hermes_constants, "get_hermes_dir", lambda *args: tmp_path / "screenshots")
    monkeypatch.setattr(browser_tool, "_last_session_key", lambda key: key)
    monkeypatch.setattr(browser_tool, "_blocked_current_page_response", lambda key: None)
    monkeypatch.setattr(browser_tool, "_get_browser_engine", lambda: "chrome")
    monkeypatch.setattr(browser_tool, "_run_browser_command", lambda *args: {"success": True, "data": {"path": str(path)}})
    monkeypatch.setattr(browser_tool, "call_llm", lambda **kw: pytest.fail("auxiliary vision called"))
    result = browser_images.browser_image("Read the page", task_id="session")
    assert result["content"][1]["image_url"]["url"] == load_image(path)["url"]
    assert result["meta"]["screenshot_path"] in result["text_summary"]
    direct = browser_tool.browser_vision("Read the page", task_id="session")
    assert direct["content"][1]["image_url"]["url"] == load_image(path)["url"]


def test_gateway_freezes_pending_and_structured_attachments_at_admission(runtime_paths, monkeypatch):
    from tests.test_tui_contract import _import_server, _prompt_session, _request
    server = _import_server()
    tasks, calls = [], []

    class DeferredThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            tasks.append(self.target)

    agent = SimpleNamespace()
    session = _prompt_session(agent)
    session["running"] = False
    session["attached_images"] = ["first.png"]
    monkeypatch.setattr(server, "_sess_nowait", lambda *args: (session, None))
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda *args: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *args: None)
    monkeypatch.setattr(server, "_wait_agent", lambda *args: None)
    monkeypatch.setattr(server.threading, "Thread", DeferredThread)
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *args, **kw: calls.append(kw))
    response = _request(server, "prompt.submit", {"session_id": "s", "text": "see", "attachments": [{"path": "upload.png"}]})
    assert "error" not in response
    session["attached_images"].append("later.png")
    tasks[0]()
    assert calls[0]["images"] == ["first.png"]
    assert calls[0]["attachments"] == [{"path": "upload.png"}]
    assert session["attached_images"] == ["later.png"]

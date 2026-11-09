from interface.image_content import display_content


def test_multimodal_content_never_becomes_encoded_display_text():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
    assert display_content([{"type": "text", "text": "Question"}, image]) == "Question\n[image]"
    assert display_content(image) == "[image]"
    assert display_content(str(image)).find("base64") == -1


def test_app_display_export_and_share_strip_image_blobs(monkeypatch):
    from types import SimpleNamespace
    from interface import app
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
    content = [{"type": "text", "text": "Question"}, image]
    assert "base64" not in str(app._normalize_display_message({"content": content}))
    assert "base64" not in str(app._build_fallback_display_messages([{"role": "user", "content": content}]))
    assert "base64" not in app._parse_stored_user_content_for_export(content)[0]
    monkeypatch.setattr(app, "_redact_chat_share_private_paths", lambda content, target: content)
    assert "base64" not in str(app._sanitize_chat_share_display_messages([{"role": "user", "content": content}], SimpleNamespace()))

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from potato_hermes_lite.attachments import (
    detect_file_drop,
    resolve_attachment_path,
    split_path_input,
)


def test_split_path_input_handles_quotes_and_escaped_spaces() -> None:
    assert split_path_input('"/tmp/my file.png" describe it') == (
        "/tmp/my file.png",
        "describe it",
    )
    assert split_path_input("/tmp/my\\ file.png describe it") == (
        "/tmp/my file.png",
        "describe it",
    )


def test_resolve_relative_path_uses_terminal_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    attachment = tmp_path / "notes.txt"
    attachment.write_text("notes", encoding="utf-8")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    assert resolve_attachment_path("notes.txt") == attachment.resolve()


def test_resolve_file_url_decodes_escaped_characters(tmp_path: Path) -> None:
    attachment = tmp_path / "sample image.png"
    attachment.write_bytes(b"image")
    uri = f"file://{quote(str(attachment))}"

    assert resolve_attachment_path(uri) == attachment.resolve()


def test_detect_file_drop_preserves_prompt_after_spaced_path(tmp_path: Path) -> None:
    attachment = tmp_path / "sample image.PNG"
    attachment.write_bytes(b"image")

    result = detect_file_drop(f"{attachment} inspect the labels")

    assert result == {
        "path": attachment.resolve(),
        "is_image": True,
        "remainder": "inspect the labels",
    }


def test_detect_file_drop_classifies_non_image(tmp_path: Path) -> None:
    attachment = tmp_path / "report.PDF"
    attachment.write_bytes(b"report")

    result = detect_file_drop(f"'{attachment}' summarize")

    assert result == {
        "path": attachment.resolve(),
        "is_image": False,
        "remainder": "summarize",
    }


def test_detect_file_drop_ignores_natural_language_and_missing_paths() -> None:
    assert detect_file_drop("please explain this image") is None
    assert detect_file_drop("") is None
    assert detect_file_drop("/definitely/missing/file.png") is None


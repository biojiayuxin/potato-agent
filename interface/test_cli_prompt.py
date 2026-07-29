from __future__ import annotations

import argparse
import io
import os
from pathlib import Path

import pytest

from interface.cli_prompt import (
    CliPromptError,
    add_prompt_source_arguments,
    read_prompt,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_prompt_source_arguments(parser)
    return parser


def test_plaintext_prompt_argument_is_not_supported() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["--prompt", "visible in argv"])


def test_prompt_uses_default_without_an_explicit_source() -> None:
    args = _parser().parse_args([])
    assert read_prompt(args, default="diagnostic default") == "diagnostic default"


def test_prompt_reads_multiline_private_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("first line\nsecond line\n", encoding="utf-8")
    prompt_file.chmod(0o600)

    args = _parser().parse_args(["--prompt-file", str(prompt_file)])
    assert read_prompt(args, default="unused") == "first line\nsecond line"


def test_prompt_rejects_public_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("private prompt\n", encoding="utf-8")
    prompt_file.chmod(0o644)

    args = _parser().parse_args(["--prompt-file", str(prompt_file)])
    with pytest.raises(CliPromptError, match="group or other"):
        read_prompt(args, default="unused")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_prompt_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.fifo"
    os.mkfifo(prompt_file, 0o600)

    args = _parser().parse_args(["--prompt-file", str(prompt_file)])
    with pytest.raises(CliPromptError, match="regular file"):
        read_prompt(args, default="unused")


def test_prompt_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("private stdin prompt\n"))
    args = _parser().parse_args(["--prompt-stdin"])
    assert read_prompt(args, default="unused") == "private stdin prompt"

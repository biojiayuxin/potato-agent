from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path


MAX_CLI_PROMPT_BYTES = 1024 * 1024


class CliPromptError(RuntimeError):
    pass


def add_prompt_source_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--prompt-file",
        type=Path,
        help="Read the prompt from a private regular file.",
    )
    group.add_argument(
        "--prompt-stdin",
        action="store_true",
        help="Read the prompt from stdin.",
    )


def _validate_prompt(value: str, *, source: str) -> str:
    normalized = value.rstrip("\r\n")
    if not normalized.strip():
        raise CliPromptError(f"{source} is empty.")
    if "\x00" in normalized:
        raise CliPromptError(f"{source} contains a NUL byte.")
    if len(normalized.encode("utf-8")) > MAX_CLI_PROMPT_BYTES:
        raise CliPromptError(
            f"{source} exceeds the {MAX_CLI_PROMPT_BYTES}-byte limit."
        )
    return normalized


def read_private_prompt_file(path: Path) -> str:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path.expanduser(), flags)
    except OSError as exc:
        raise CliPromptError("Unable to open prompt file.") from exc

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise CliPromptError("Prompt file must be a regular file.")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise CliPromptError(
                "Prompt file must not be accessible by group or other users."
            )
        if file_stat.st_uid not in {0, os.geteuid()}:
            raise CliPromptError("Prompt file must be owned by root or the caller.")
        if file_stat.st_size > MAX_CLI_PROMPT_BYTES:
            raise CliPromptError(
                f"Prompt file exceeds the {MAX_CLI_PROMPT_BYTES}-byte limit."
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            value = handle.read(MAX_CLI_PROMPT_BYTES + 1)
    except UnicodeError as exc:
        raise CliPromptError("Prompt file must contain UTF-8 text.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _validate_prompt(value, source="Prompt file")


def read_prompt(args: argparse.Namespace, *, default: str) -> str:
    prompt_file = getattr(args, "prompt_file", None)
    if prompt_file is not None:
        return read_private_prompt_file(prompt_file)
    if bool(getattr(args, "prompt_stdin", False)):
        return _validate_prompt(
            sys.stdin.read(MAX_CLI_PROMPT_BYTES + 1),
            source="Prompt from stdin",
        )
    return _validate_prompt(default, source="Default prompt")

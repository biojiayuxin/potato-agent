from __future__ import annotations

import argparse
import getpass
import os
import stat
import sys
from pathlib import Path


MAX_CLI_SECRET_BYTES = 16 * 1024


class CliSecretError(RuntimeError):
    pass


def add_password_source_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--password-file",
        type=Path,
        help="Read the password from a private regular file.",
    )
    group.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read exactly one password line from stdin.",
    )


def _validate_secret_text(value: str, *, source: str) -> str:
    normalized = value.rstrip("\r\n")
    if not normalized:
        raise CliSecretError(f"{source} is empty.")
    if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        raise CliSecretError(f"{source} must contain exactly one text line.")
    if len(normalized.encode("utf-8")) > MAX_CLI_SECRET_BYTES:
        raise CliSecretError(
            f"{source} exceeds the {MAX_CLI_SECRET_BYTES}-byte limit."
        )
    return normalized


def read_private_secret_file(path: Path, *, source: str = "Password file") -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.expanduser(), flags)
    except OSError as exc:
        raise CliSecretError(f"Unable to open {source.lower()}.") from exc

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise CliSecretError(f"{source} must be a regular file.")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise CliSecretError(
                f"{source} must not be accessible by group or other users."
            )
        if file_stat.st_uid not in {0, os.geteuid()}:
            raise CliSecretError(f"{source} must be owned by root or the caller.")
        if file_stat.st_size > MAX_CLI_SECRET_BYTES:
            raise CliSecretError(
                f"{source} exceeds the {MAX_CLI_SECRET_BYTES}-byte limit."
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            value = handle.read(MAX_CLI_SECRET_BYTES + 1)
    except UnicodeError as exc:
        raise CliSecretError(f"{source} must contain UTF-8 text.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _validate_secret_text(value, source=source)


def read_secret_from_stdin(*, source: str = "Password from stdin") -> str:
    return _validate_secret_text(
        sys.stdin.read(MAX_CLI_SECRET_BYTES + 1),
        source=source,
    )


def read_password(
    args: argparse.Namespace,
    *,
    prompt: str = "Interface password: ",
    confirmation_prompt: str = "Confirm interface password: ",
) -> str:
    password_file = getattr(args, "password_file", None)
    if password_file is not None:
        return read_private_secret_file(password_file)
    if bool(getattr(args, "password_stdin", False)):
        return read_secret_from_stdin()

    first = _validate_secret_text(getpass.getpass(prompt), source="Password")
    repeated = _validate_secret_text(
        getpass.getpass(confirmation_prompt), source="Password confirmation"
    )
    if first != repeated:
        raise CliSecretError("Passwords do not match.")
    return first

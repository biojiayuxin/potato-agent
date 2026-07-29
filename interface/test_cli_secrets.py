from __future__ import annotations

import argparse
import io
import stat

import pytest

import bind_existing_linux_user
import provision_interface_user
from interface.cli_secrets import (
    CliSecretError,
    add_password_source_arguments,
    read_password,
)


def _args(*values: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_password_source_arguments(parser)
    return parser.parse_args(list(values))


def test_password_stdin_is_not_part_of_process_arguments(monkeypatch) -> None:
    sentinel = "UniquePassword-stdin-42!"
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{sentinel}\n"))

    assert read_password(_args("--password-stdin")) == sentinel


def test_private_password_file_is_accepted(tmp_path) -> None:
    password_path = tmp_path / "password"
    password_path.write_text("UniquePassword-file-42!\n", encoding="utf-8")
    password_path.chmod(0o600)

    assert read_password(_args("--password-file", str(password_path))) == (
        "UniquePassword-file-42!"
    )


def test_world_readable_password_file_is_rejected(tmp_path) -> None:
    password_path = tmp_path / "password"
    password_path.write_text("UniquePassword-file-42!\n", encoding="utf-8")
    password_path.chmod(0o644)

    with pytest.raises(CliSecretError, match="must not be accessible"):
        read_password(_args("--password-file", str(password_path)))

    assert stat.S_IMODE(password_path.stat().st_mode) == 0o644


def test_multiline_password_input_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("first\nsecond\n"))

    with pytest.raises(CliSecretError, match="exactly one text line"):
        read_password(_args("--password-stdin"))


@pytest.mark.parametrize(
    "parser",
    [
        provision_interface_user.build_parser(),
        bind_existing_linux_user.build_parser(),
    ],
)
def test_provisioning_parsers_reject_password_positionals(parser) -> None:
    with pytest.raises(SystemExit):
        parser.parse_args(["alice", "alice@example.com", "VisiblePassword-42!"])


def test_provisioning_parser_rejects_plaintext_api_key_flag() -> None:
    parser = provision_interface_user.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["alice", "alice@example.com", "--api-key", "visible-secret"]
        )

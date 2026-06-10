#!/usr/bin/env python3

from __future__ import annotations

import argparse
import getpass
import secrets
import string
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from interface.auth_db import (
    DEFAULT_AUTH_DB_PATH,
    InterfaceUser,
    get_user_with_password_by_login,
    list_users,
    update_user_password,
    verify_password,
)
from interface.password_policy import (
    COMMON_PASSWORD_SYMBOLS,
    PASSWORD_COMPLEXITY_DETAIL,
    password_complexity_error,
)


DEFAULT_WEAK_PASSWORD_CANDIDATES = (
    "password",
    "Password",
    "password1",
    "Password1",
    "password123",
    "Password123",
    "Password1234",
    "admin",
    "Admin123",
    "admin123",
    "123456",
    "12345678",
    "123456789",
    "qwerty",
    "Qwerty123",
    "abc123",
    "Abc12345",
    "welcome",
    "Welcome123",
    "letmein",
    "Potato123",
    "PotatoAgent123",
)


class ManagementError(RuntimeError):
    pass


def _require_existing_db(db_path: Path) -> Path:
    resolved = db_path.expanduser()
    if not resolved.exists():
        raise ManagementError(
            f"Auth DB does not exist: {resolved}. "
            "Pass --auth-db if the service uses another path."
        )
    return resolved


def _format_timestamp(timestamp: int) -> str:
    if timestamp <= 0:
        return "-"
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _format_bool(value: bool) -> str:
    return "yes" if value else "no"


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    if not rows:
        print("No users found.")
        return
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    header_line = "  ".join(
        headers[index].ljust(widths[index]) for index in range(len(headers))
    )
    print(header_line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(
            "  ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        )


def _bcrypt_summary(password_hash: str | None) -> str:
    if not password_hash:
        return "missing password hash"
    parts = password_hash.split("$")
    if len(parts) >= 4 and parts[1].startswith("2") and parts[2].isdigit():
        return f"bcrypt {parts[1]} cost={int(parts[2])}"
    return "unknown hash format"


def _load_user_with_hash(
    login: str, *, db_path: Path
) -> tuple[InterfaceUser, str]:
    normalized_login = login.strip()
    if not normalized_login:
        raise ManagementError("Login cannot be empty.")
    user, password_hash = get_user_with_password_by_login(
        normalized_login, db_path=db_path
    )
    if user is None or password_hash is None:
        raise ManagementError(f"User not found: {normalized_login}")
    return user, password_hash


def _read_password_file(path: Path) -> str:
    try:
        password = path.expanduser().read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ManagementError(f"Failed to read password file: {exc}") from exc
    if password == "":
        raise ManagementError("Password file is empty.")
    return password


def _read_password_from_args(
    args: argparse.Namespace,
    *,
    prompt: str,
    confirm: bool,
) -> str:
    source_count = sum(
        bool(source)
        for source in (
            getattr(args, "password", None) is not None,
            getattr(args, "password_file", None) is not None,
            bool(getattr(args, "password_stdin", False)),
            bool(getattr(args, "generate", False)),
        )
    )
    if source_count > 1:
        raise ManagementError(
            "Choose only one password source: prompt, --password, "
            "--password-file, --password-stdin, or --generate."
        )

    if getattr(args, "generate", False):
        return _generate_password(int(args.length))
    if getattr(args, "password", None) is not None:
        print(
            "warning: --password can be visible in shell history or process "
            "lists; prefer prompt, --password-file, or --password-stdin.",
            file=sys.stderr,
        )
        password = str(args.password)
    elif getattr(args, "password_file", None) is not None:
        password = _read_password_file(args.password_file)
    elif getattr(args, "password_stdin", False):
        password = sys.stdin.read().rstrip("\r\n")
        if password == "":
            raise ManagementError("No password was read from stdin.")
    else:
        password = getpass.getpass(prompt)
        if confirm:
            repeated = getpass.getpass("Confirm new password: ")
            if password != repeated:
                raise ManagementError("Passwords do not match.")

    if password == "":
        raise ManagementError("Password cannot be empty.")
    return password


def _generate_password(length: int) -> str:
    if length < 12:
        raise ManagementError("Generated password length must be at least 12.")
    alphabet = string.ascii_letters + string.digits + COMMON_PASSWORD_SYMBOLS
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if password_complexity_error(password) is None:
            return password


def _validate_new_password(password: str) -> None:
    error = password_complexity_error(password)
    if error is not None:
        raise ManagementError(f"{error} Refusing to write a weak password.")


def _add_password_source_args(
    parser: argparse.ArgumentParser, *, include_generate: bool
) -> None:
    group = parser.add_argument_group("password input")
    group.add_argument(
        "--password",
        help="Password value. Prefer prompt or stdin on shared systems.",
    )
    group.add_argument(
        "--password-file",
        type=Path,
        help="Read the password from a UTF-8 text file.",
    )
    group.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin.",
    )
    if include_generate:
        group.add_argument(
            "--generate",
            action="store_true",
            help="Generate a strong random password and print it after reset.",
        )
        group.add_argument(
            "--length",
            type=int,
            default=20,
            help="Length for --generate passwords (default: 20, minimum: 12).",
        )


def _load_candidates(wordlist_path: Path | None) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        if candidate == "" or candidate.startswith("#") or candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    for candidate in DEFAULT_WEAK_PASSWORD_CANDIDATES:
        add(candidate)

    if wordlist_path is not None:
        try:
            lines = wordlist_path.expanduser().read_text(encoding="utf-8")
            for line in lines.splitlines():
                add(line.rstrip("\r\n"))
        except OSError as exc:
            raise ManagementError(f"Failed to read wordlist: {exc}") from exc

    return candidates


def command_list(args: argparse.Namespace) -> int:
    db_path = _require_existing_db(args.auth_db)
    rows = [
        (
            user.username,
            user.email,
            user.role,
            _format_bool(user.active),
            user.mapping_username,
            str(user.auth_session_version),
            _format_timestamp(user.updated_at),
        )
        for user in list_users(db_path=db_path)
    ]
    _print_table(
        (
            "username",
            "email",
            "role",
            "active",
            "mapping",
            "session_ver",
            "updated_at",
        ),
        rows,
    )
    return 0


def command_show(args: argparse.Namespace) -> int:
    db_path = _require_existing_db(args.auth_db)
    user, password_hash = _load_user_with_hash(args.login, db_path=db_path)
    print(f"id: {user.id}")
    print(f"username: {user.username}")
    print(f"email: {user.email}")
    print(f"name: {user.name}")
    print(f"role: {user.role}")
    print(f"mapping_username: {user.mapping_username}")
    print(f"active: {_format_bool(user.active)}")
    print(f"auth_session_version: {user.auth_session_version}")
    print(f"created_at: {_format_timestamp(user.created_at)}")
    print(f"updated_at: {_format_timestamp(user.updated_at)}")
    print("plaintext_password: unavailable; only a non-reversible hash is stored")
    if args.show_hash:
        print(f"password_hash: {password_hash}")
    else:
        print(f"password_hash: {_bcrypt_summary(password_hash)}; hidden")
    return 0


def command_check_password(args: argparse.Namespace) -> int:
    db_path = _require_existing_db(args.auth_db)
    user, password_hash = _load_user_with_hash(args.login, db_path=db_path)
    password = _read_password_from_args(
        args, prompt="Password to check: ", confirm=False
    )
    if verify_password(password, password_hash):
        print(f"Password matches user {user.username}.")
        return 0
    print(f"Password does not match user {user.username}.")
    return 2


def command_reset_password(args: argparse.Namespace) -> int:
    db_path = _require_existing_db(args.auth_db)
    user, _ = _load_user_with_hash(args.login, db_path=db_path)
    new_password = _read_password_from_args(
        args, prompt="New password: ", confirm=not args.generate
    )
    _validate_new_password(new_password)

    updated = update_user_password(user.id, new_password, db_path=db_path)
    if updated is None:
        raise ManagementError(f"User disappeared during password reset: {user.username}")

    print(f"Password reset for {updated.username} <{updated.email}>.")
    print(
        f"Auth session version: {user.auth_session_version} -> {updated.auth_session_version}"
    )
    print("Existing browser sessions for this user are revoked.")
    if args.generate:
        print(f"Generated password: {new_password}")
    return 0


def _iter_audit_targets(
    login: str | None, *, db_path: Path
) -> Iterable[tuple[InterfaceUser, str]]:
    if login:
        yield _load_user_with_hash(login, db_path=db_path)
        return
    for user in list_users(db_path=db_path):
        yield _load_user_with_hash(user.username, db_path=db_path)


def command_audit_passwords(args: argparse.Namespace) -> int:
    db_path = _require_existing_db(args.auth_db)
    candidates = _load_candidates(args.wordlist)
    if not candidates:
        raise ManagementError("No audit password candidates loaded.")

    matches: list[InterfaceUser] = []
    for user, password_hash in _iter_audit_targets(args.login, db_path=db_path):
        for candidate in candidates:
            if verify_password(candidate, password_hash):
                matches.append(user)
                break

    if not matches:
        print(f"No matches found against {len(candidates)} weak/common candidates.")
        return 0

    print("Weak/common password matches found:")
    for user in matches:
        print(f"- {user.username} <{user.email}>")
    print("Reset these users with the reset-password command.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Potato Agent interface users and password hashes."
    )
    parser.add_argument(
        "--auth-db",
        type=Path,
        default=DEFAULT_AUTH_DB_PATH,
        help=f"Path to interface auth DB (default: {DEFAULT_AUTH_DB_PATH})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List interface users.")
    list_parser.set_defaults(func=command_list)

    show_parser = subparsers.add_parser("show", help="Show one user account.")
    show_parser.add_argument("login", help="Username or email.")
    show_parser.add_argument(
        "--show-hash",
        action="store_true",
        help="Print the stored password hash. This is sensitive.",
    )
    show_parser.set_defaults(func=command_show)

    check_parser = subparsers.add_parser(
        "check-password", help="Check whether a candidate password matches a user."
    )
    check_parser.add_argument("login", help="Username or email.")
    _add_password_source_args(check_parser, include_generate=False)
    check_parser.set_defaults(func=command_check_password)

    reset_parser = subparsers.add_parser(
        "reset-password", help="Reset one user's password and revoke old sessions."
    )
    reset_parser.add_argument("login", help="Username or email.")
    _add_password_source_args(reset_parser, include_generate=True)
    reset_parser.set_defaults(func=command_reset_password)

    audit_parser = subparsers.add_parser(
        "audit-passwords",
        help="Check stored hashes against a small weak/common password candidate list.",
    )
    audit_parser.add_argument(
        "--login",
        help="Optional username or email to audit. Defaults to all users.",
    )
    audit_parser.add_argument(
        "--wordlist",
        type=Path,
        help="Optional UTF-8 wordlist. Lines starting with # are ignored.",
    )
    audit_parser.set_defaults(func=command_audit_passwords)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ManagementError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        detail = str(exc) or PASSWORD_COMPLEXITY_DETAIL
        print(f"error: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

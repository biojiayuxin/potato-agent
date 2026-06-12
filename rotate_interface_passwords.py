#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from interface.auth_db import (
    DEFAULT_AUTH_DB_PATH,
    InterfaceUser,
    get_user_with_password_by_login,
    list_users,
    update_user_password,
)
from interface.mailer import (
    MailerConfigurationError,
    MailerDeliveryError,
    get_resend_settings,
    send_password_rotation_notice_email,
)
from manage_interface_users import (
    ManagementError,
    _generate_password,
    _require_existing_db,
    _validate_new_password,
)


MAIL_ENV_KEYS = (
    "INTERFACE_RESEND_API_KEY",
    "INTERFACE_MAIL_FROM",
    "INTERFACE_MAIL_REPLY_TO",
)
REQUIRED_MAIL_ENV_KEYS = ("INTERFACE_RESEND_API_KEY", "INTERFACE_MAIL_FROM")


@dataclass(frozen=True)
class RotationTarget:
    user: InterfaceUser
    old_password_hash: str
    old_auth_session_version: int
    old_updated_at: int


@dataclass(frozen=True)
class RotationOutcome:
    user: InterfaceUser
    updated_user: InterfaceUser
    email_id: str


def _format_timestamp(timestamp: int) -> str:
    if timestamp <= 0:
        return "-"
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _parse_cutoff_timestamp(value: str) -> int:
    normalized = value.strip()
    if not normalized:
        raise ManagementError("--before-date cannot be empty.")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            parsed = datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=UTC)
        else:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
    except ValueError as exc:
        raise ManagementError(
            "Invalid --before-date. Use YYYY-MM-DD or an ISO timestamp."
        ) from exc
    return int(parsed.timestamp())


def _load_missing_mail_env_from_systemd(unit: str) -> bool:
    if all((os.getenv(key) or "").strip() for key in REQUIRED_MAIL_ENV_KEYS):
        return False
    normalized_unit = unit.strip()
    if not normalized_unit:
        return False
    try:
        output = subprocess.check_output(
            ["systemctl", "show", normalized_unit, "-p", "Environment"],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManagementError(
            f"Failed to load mail settings from systemd unit {normalized_unit!r}: {exc}"
        ) from exc

    line = output.strip()
    raw_environment = line.split("=", 1)[1] if "=" in line else ""
    loaded = False
    for item in shlex.split(raw_environment):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key in MAIL_ENV_KEYS and not os.getenv(key):
            os.environ[key] = value
            loaded = True
    return loaded


def _load_user_target(login: str, *, db_path: Path) -> RotationTarget:
    normalized_login = login.strip()
    if not normalized_login:
        raise ManagementError("--login cannot be empty.")
    user, password_hash = get_user_with_password_by_login(
        normalized_login, db_path=db_path
    )
    if user is None or password_hash is None:
        raise ManagementError(f"User not found: {normalized_login}")
    return RotationTarget(
        user=user,
        old_password_hash=password_hash,
        old_auth_session_version=user.auth_session_version,
        old_updated_at=user.updated_at,
    )


def _select_targets(args: argparse.Namespace, *, db_path: Path) -> list[RotationTarget]:
    targets: list[RotationTarget] = []
    seen_user_ids: set[str] = set()

    def add_target(target: RotationTarget) -> None:
        if target.user.id in seen_user_ids:
            return
        seen_user_ids.add(target.user.id)
        targets.append(target)

    if args.login:
        for login in args.login:
            target = _load_user_target(login, db_path=db_path)
            if not args.include_inactive and not target.user.active:
                print(
                    f"Skipping inactive user {target.user.username} <{target.user.email}>.",
                    file=sys.stderr,
                )
                continue
            add_target(target)
    else:
        if not args.before_date:
            raise ManagementError("Pass --before-date when --login is not used.")
        cutoff = _parse_cutoff_timestamp(args.before_date)
        for user in list_users(db_path=db_path):
            if not args.include_inactive and not user.active:
                continue
            if user.created_at >= cutoff:
                continue
            add_target(_load_user_target(user.username, db_path=db_path))

    if args.limit is not None:
        targets = targets[: max(int(args.limit), 0)]
    return sorted(targets, key=lambda target: target.user.username)


def _restore_password_hash(target: RotationTarget, *, db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            "update users set password_hash = ?, auth_session_version = ?, updated_at = ? "
            "where id = ?",
            (
                target.old_password_hash,
                target.old_auth_session_version,
                target.old_updated_at,
                target.user.id,
            ),
        )
        conn.commit()
    if cursor.rowcount <= 0:
        raise ManagementError(
            f"Failed to restore password hash for {target.user.username}."
        )


def _print_targets(targets: Sequence[RotationTarget], *, execute: bool) -> None:
    mode = "EXECUTE" if execute else "DRY RUN"
    print(f"{mode}: {len(targets)} user(s) selected for password rotation.")
    for target in targets:
        user = target.user
        print(
            f"- {user.username} <{user.email}> "
            f"created_at={_format_timestamp(user.created_at)} "
            f"active={'yes' if user.active else 'no'}"
        )


async def _rotate_one(
    target: RotationTarget,
    *,
    db_path: Path,
    password_length: int,
    site_url: str,
) -> RotationOutcome:
    new_password = _generate_password(password_length)
    _validate_new_password(new_password)

    updated = update_user_password(target.user.id, new_password, db_path=db_path)
    if updated is None:
        raise ManagementError(
            f"User disappeared during password rotation: {target.user.username}"
        )

    try:
        result = await send_password_rotation_notice_email(
            email=target.user.email,
            username=target.user.username,
            new_password=new_password,
            idempotency_key=f"password-rotation-{target.user.id}-{uuid.uuid4()}",
            site_url=site_url,
        )
    except Exception:
        _restore_password_hash(target, db_path=db_path)
        raise

    return RotationOutcome(
        user=target.user,
        updated_user=updated,
        email_id=result.email_id,
    )


async def _run(args: argparse.Namespace) -> int:
    db_path = _require_existing_db(args.auth_db)
    targets = _select_targets(args, db_path=db_path)
    _print_targets(targets, execute=args.execute)
    if not targets:
        return 0
    if not args.execute:
        print("No passwords changed. Pass --execute to update users and send email.")
        return 0

    if not args.no_systemd_env:
        _load_missing_mail_env_from_systemd(args.systemd_unit)
    get_resend_settings()

    successes: list[RotationOutcome] = []
    failures: list[tuple[RotationTarget, Exception]] = []
    for target in targets:
        try:
            outcome = await _rotate_one(
                target,
                db_path=db_path,
                password_length=args.length,
                site_url=args.site_url.strip(),
            )
        except Exception as exc:
            failures.append((target, exc))
            print(
                f"FAILED {target.user.username} <{target.user.email}>: {exc}",
                file=sys.stderr,
            )
            if not args.continue_on_error:
                break
            continue
        successes.append(outcome)
        print(
            f"Rotated {outcome.user.username} <{outcome.user.email}>; "
            f"session {outcome.user.auth_session_version} -> "
            f"{outcome.updated_user.auth_session_version}; "
            f"email_id={outcome.email_id or '-'}"
        )

    print(f"Completed: {len(successes)} succeeded, {len(failures)} failed.")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rotate Potato Agent interface passwords and notify users by email. "
            "Runs as a dry run unless --execute is passed."
        )
    )
    parser.add_argument(
        "--auth-db",
        type=Path,
        default=DEFAULT_AUTH_DB_PATH,
        help=f"Path to interface auth DB (default: {DEFAULT_AUTH_DB_PATH})",
    )
    parser.add_argument(
        "--before-date",
        help=(
            "Select users created before this UTC date or ISO timestamp, for "
            "example 2026-06-01. Required unless --login is used."
        ),
    )
    parser.add_argument(
        "--login",
        action="append",
        help=(
            "Rotate a specific username or email. May be passed multiple times; "
            "explicit logins do not require --before-date."
        ),
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive users. Inactive users are skipped by default.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of selected users, useful for staged execution.",
    )
    parser.add_argument(
        "--length",
        type=int,
        default=20,
        help="Generated temporary password length (default: 20, minimum: 12).",
    )
    parser.add_argument(
        "--site-url",
        default=os.getenv("INTERFACE_PUBLIC_BASE_URL", ""),
        help="Optional Potato Agent sign-in URL to include in the email.",
    )
    parser.add_argument(
        "--systemd-unit",
        default="potato-interface.service",
        help=(
            "Systemd unit to read missing mail environment from "
            "(default: potato-interface.service)."
        ),
    )
    parser.add_argument(
        "--no-systemd-env",
        action="store_true",
        help="Do not load missing Resend mail settings from systemd.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with later users after a per-user failure.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually update passwords and send notification emails.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (
        ManagementError,
        MailerConfigurationError,
        MailerDeliveryError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

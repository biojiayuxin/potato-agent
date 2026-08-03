#!/usr/bin/env python3
"""Wait for signup work to drain before a protected production cutover."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys
import time
from typing import Callable


class SignupDrainError(RuntimeError):
    pass


def _count_jobs(path: Path, statuses: tuple[str, ...]) -> int:
    placeholders = ",".join("?" for _status in statuses)
    try:
        with sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            timeout=1,
        ) as connection:
            table_exists = connection.execute(
                "select 1 from sqlite_master "
                "where type = 'table' and name = 'signup_jobs'"
            ).fetchone()
            if table_exists is None:
                raise SignupDrainError("signup drain gate lacks signup_jobs table")
            return int(
                connection.execute(
                    f"select count(*) from signup_jobs where status in ({placeholders})",
                    statuses,
                ).fetchone()[0]
            )
    except sqlite3.Error as exc:
        raise SignupDrainError(f"unable to read signup job state: {exc}") from exc


def wait_for_signup_jobs(
    path: Path,
    timeout_seconds: float,
    *,
    poll_interval: float = 1.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_seconds
    while True:
        count = _count_jobs(path, ("pending", "provisioning"))
        if count == 0:
            return
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise SignupDrainError(
                f"signup jobs did not drain within {timeout_seconds:g} seconds: "
                f"active={count}"
            )
        sleep(min(poll_interval, remaining))


def assert_no_provisioning_jobs(path: Path) -> None:
    count = _count_jobs(path, ("provisioning",))
    if count:
        raise SignupDrainError(
            f"Interface stopped with provisioning signup jobs still active: {count}"
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    wait_parser = subparsers.add_parser("wait")
    wait_parser.add_argument("--timeout", type=float, default=60.0)
    subparsers.add_parser("check-provisioning")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "wait":
            if args.timeout < 0:
                raise SignupDrainError("signup drain timeout must not be negative")
            wait_for_signup_jobs(args.db, args.timeout)
        else:
            assert_no_provisioning_jobs(args.db)
    except SignupDrainError as exc:
        print(f"signup drain error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

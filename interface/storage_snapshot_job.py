from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from interface.admin_store import (
    list_storage_snapshot_candidates,
    save_storage_snapshot,
)
from interface.auth_db import DEFAULT_AUTH_DB_PATH, ensure_auth_db
from interface.privileged_client import PrivilegedClientError, privileged_client


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _snapshot_day(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=SHANGHAI).date().isoformat()


def _snapshot_error_code(exc: Exception) -> str:
    detail = str(exc).lower()
    if "timeout" in detail or "timed out" in detail:
        return "timeout"
    if "permission" in detail or "denied" in detail:
        return "permission_denied"
    if "unknown mapping" in detail or "not found" in detail:
        return "not_found"
    return "unavailable"


def run_storage_snapshot(
    *,
    now: int | None = None,
    db_path: Path = DEFAULT_AUTH_DB_PATH,
) -> dict[str, int]:
    started_at = int(time.time()) if now is None else int(now)
    snapshot_day = _snapshot_day(started_at)
    result = {"candidates": 0, "saved": 0, "errors": 0, "skipped_race": 0}
    candidates = list_storage_snapshot_candidates(db_path=db_path)
    result["candidates"] = len(candidates)
    for candidate in candidates:
        sampled_at = int(time.time()) if now is None else int(now)
        try:
            allocated_bytes = privileged_client.home_usage(
                str(candidate["mapping_username"])
            )
            saved = save_storage_snapshot(
                user_id=str(candidate["user_id"]),
                mapping_username=str(candidate["mapping_username"]),
                snapshot_day=snapshot_day,
                sampled_at=sampled_at,
                allocated_bytes=allocated_bytes,
                status="ok",
                db_path=db_path,
            )
        except PrivilegedClientError as exc:
            result["errors"] += 1
            saved = save_storage_snapshot(
                user_id=str(candidate["user_id"]),
                mapping_username=str(candidate["mapping_username"]),
                snapshot_day=snapshot_day,
                sampled_at=sampled_at,
                allocated_bytes=None,
                status="error",
                error_code=_snapshot_error_code(exc),
                db_path=db_path,
            )
        if saved:
            result["saved"] += 1
        else:
            result["skipped_race"] += 1
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sample Potato user home usage.")
    parser.add_argument("command", choices=("run",))
    parser.add_argument(
        "--auth-db",
        type=Path,
        default=DEFAULT_AUTH_DB_PATH,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_auth_db(args.auth_db)
    result = run_storage_snapshot(db_path=args.auth_db)
    print(json.dumps({"ok": True, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

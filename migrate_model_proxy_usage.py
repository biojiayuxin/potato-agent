#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import pwd
import sqlite3
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from interface.auth_db import DEFAULT_AUTH_DB_PATH
from interface.secure_paths import ensure_sqlite_sidecar_modes
from interface.token_usage_store import (
    DEFAULT_MODEL_PROXY_USAGE_DB_PATH,
    ensure_token_usage_store,
)


PROXY_USER = "potato-model-proxy"
USAGE_TABLE = "model_proxy_usage_requests"
QUOTA_TABLE = "model_proxy_user_quotas"
USAGE_COLUMNS = (
    "id",
    "mapping_username",
    "endpoint",
    "route_model",
    "upstream_model",
    "provider",
    "api_mode",
    "status_code",
    "streaming",
    "started_at",
    "completed_at",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "usage_status",
    "raw_usage_json",
)
QUOTA_COLUMNS = (
    "mapping_username",
    "input_token_limit",
    "output_token_limit",
    "cache_read_token_limit",
    "cache_write_token_limit",
    "total_token_limit",
    "period",
    "enabled",
    "created_at",
    "updated_at",
)


class ModelProxyUsageMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationResult:
    usage_rows: int
    quota_rows: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy model proxy usage and quota tables out of interface.db into "
            "the dedicated model proxy database. Run while Interface, Hermes, "
            "and the model proxy are stopped."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_AUTH_DB_PATH,
        help=f"Existing Interface DB (default: {DEFAULT_AUTH_DB_PATH})",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_MODEL_PROXY_USAGE_DB_PATH,
        help=(
            "Dedicated proxy usage DB "
            f"(default: {DEFAULT_MODEL_PROXY_USAGE_DB_PATH})"
        ),
    )
    return parser


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str] | None:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ? limit 1",
        (table,),
    ).fetchone()
    if row is None:
        return None
    return {
        str(column["name"])
        for column in conn.execute(f'pragma table_info("{table}")').fetchall()
    }


def _read_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
) -> list[tuple[object, ...]]:
    available = _table_columns(conn, table)
    if available is None:
        return []
    missing = set(columns) - available
    if missing:
        raise ModelProxyUsageMigrationError(
            f"Source table {table} is missing required columns: "
            + ", ".join(sorted(missing))
        )
    selected = ", ".join(f'"{column}"' for column in columns)
    return [tuple(row) for row in conn.execute(f'select {selected} from "{table}"')]


def _canonicalize_usage_rows(
    rows: list[tuple[object, ...]],
) -> list[tuple[object, ...]]:
    indexes = {name: USAGE_COLUMNS.index(name) for name in USAGE_COLUMNS}
    canonical: list[tuple[object, ...]] = []
    for row in rows:
        values = list(row)
        usage_status = str(values[indexes["usage_status"]] or "").strip()
        normalized_tokens = {
            field: max(0, int(values[indexes[field]] or 0))
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        }
        values[indexes["raw_usage_json"]] = json.dumps(
            normalized_tokens if usage_status == "present" else {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        canonical.append(tuple(values))
    return canonical


def _insert_rows_if_missing(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    conflict_column: str,
    rows: list[tuple[object, ...]],
) -> None:
    if not rows:
        return
    selected = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f'insert into "{table}" ({selected}) values ({placeholders}) '
        f'on conflict("{conflict_column}") do nothing',
        rows,
    )


def migrate_usage_database(source: Path, destination: Path) -> MigrationResult:
    source_path = source.expanduser().absolute()
    destination_path = destination.expanduser().absolute()
    if source_path.is_symlink():
        raise ModelProxyUsageMigrationError(
            f"Source must not be a symbolic link: {source_path}"
        )
    if destination_path.is_symlink():
        raise ModelProxyUsageMigrationError(
            f"Destination must not be a symbolic link: {destination_path}"
        )
    source = source_path.resolve()
    destination = destination_path.resolve()
    if source == destination:
        raise ModelProxyUsageMigrationError("Source and destination must be different")
    if not source.is_file():
        raise ModelProxyUsageMigrationError(f"Source database not found: {source}")
    if destination.exists() and source.samefile(destination):
        raise ModelProxyUsageMigrationError(
            "Source and destination resolve to the same database inode"
        )
    with _connect_read_only(source) as source_conn:
        usage_rows = _canonicalize_usage_rows(
            _read_rows(source_conn, table=USAGE_TABLE, columns=USAGE_COLUMNS)
        )
        quota_rows = _read_rows(
            source_conn, table=QUOTA_TABLE, columns=QUOTA_COLUMNS
        )

    ensure_token_usage_store(destination)
    with sqlite3.connect(str(destination)) as destination_conn:
        destination_conn.execute("PRAGMA secure_delete = ON")
        destination_conn.execute("begin immediate")
        _insert_rows_if_missing(
            destination_conn,
            table=USAGE_TABLE,
            columns=USAGE_COLUMNS,
            conflict_column="id",
            rows=usage_rows,
        )
        _insert_rows_if_missing(
            destination_conn,
            table=QUOTA_TABLE,
            columns=QUOTA_COLUMNS,
            conflict_column="mapping_username",
            rows=quota_rows,
        )
        destination_conn.commit()

    ensure_sqlite_sidecar_modes(destination, mode=0o600)
    return MigrationResult(
        usage_rows=len(usage_rows),
        quota_rows=len(quota_rows),
    )


def _validate_destination_directory(path: Path, *, uid: int, gid: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ModelProxyUsageMigrationError(
            f"Create the dedicated proxy directory before migration: {path}"
        ) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ModelProxyUsageMigrationError(
            f"{path} must be owned by {PROXY_USER}:{PROXY_USER} with mode 0700"
        )


def _set_database_owner(path: Path, *, uid: int, gid: int) -> None:
    for candidate in (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    ):
        if not candidate.exists():
            continue
        os.chown(candidate, uid, gid)
        os.chmod(candidate, 0o600)


def main() -> int:
    args = build_parser().parse_args()
    if os.geteuid() != 0:
        raise ModelProxyUsageMigrationError("This migration must run as root")
    try:
        proxy_account = pwd.getpwnam(PROXY_USER)
    except KeyError as exc:
        raise ModelProxyUsageMigrationError(
            f"Required service account does not exist: {PROXY_USER}"
        ) from exc

    destination = args.destination.expanduser().resolve()
    _validate_destination_directory(
        destination.parent,
        uid=proxy_account.pw_uid,
        gid=proxy_account.pw_gid,
    )
    result = migrate_usage_database(args.source, destination)
    _set_database_owner(
        destination,
        uid=proxy_account.pw_uid,
        gid=proxy_account.pw_gid,
    )
    print(f"Migrated usage rows: {result.usage_rows}")
    print(f"Migrated quota rows: {result.quota_rows}")
    print(f"Dedicated proxy database: {destination}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

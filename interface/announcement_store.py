from __future__ import annotations

import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from interface import auth_db


@dataclass(frozen=True)
class Announcement:
    id: str
    message: str
    starts_at: float
    ends_at: float | None
    withdrawn: bool
    updated_at: float

    def status(self, now: float) -> str:
        if self.withdrawn:
            return "withdrawn"
        if now < self.starts_at:
            return "scheduled"
        if self.ends_at is not None and now >= self.ends_at:
            return "ended"
        return "active"

    def public_payload(self) -> dict:
        return {
            "id": self.id,
            "message": self.message,
            "starts_at": iso_timestamp(self.starts_at),
            "ends_at": iso_timestamp(self.ends_at),
        }

    def admin_payload(self, now: float) -> dict:
        return {
            **self.public_payload(),
            "withdrawn": self.withdrawn,
            "updated_at": iso_timestamp(self.updated_at),
            "status": self.status(now),
        }


def iso_timestamp(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


def get_announcement(*, db_path: Path | None = None) -> Announcement | None:
    with auth_db.connect_auth_db(db_path or auth_db.DEFAULT_AUTH_DB_PATH) as conn:
        row = conn.execute("select * from site_announcement where singleton = 1").fetchone()
    return _from_row(row) if row else None


def _from_row(row: sqlite3.Row) -> Announcement:
    return Announcement(
        id=row["id"], message=row["message"], starts_at=row["starts_at"],
        ends_at=row["ends_at"], withdrawn=bool(row["withdrawn"]),
        updated_at=row["updated_at"],
    )


def save_announcement(
    *,
    message: str,
    starts_at: float | None = None,
    ends_at: float | None = None,
    existing_id: str | None = None,
    now: float | None = None,
    db_path: Path | None = None,
) -> Announcement:
    timestamp = time.time() if now is None else now
    start = timestamp if starts_at is None else starts_at
    message = message.strip()
    if not message or len(message) > 500 or "\x00" in message:
        raise ValueError("Announcement must contain 1 to 500 plain text characters")
    if not math.isfinite(start) or (ends_at is not None and not math.isfinite(ends_at)):
        raise ValueError("Announcement times must be finite")
    # Validate the representable UTC range before committing a record.
    iso_timestamp(start)
    iso_timestamp(ends_at)
    if ends_at is not None and ends_at <= start:
        raise ValueError("End time must be later than start time")
    with auth_db.connect_auth_db(db_path or auth_db.DEFAULT_AUTH_DB_PATH) as conn:
        if existing_id is None:
            conn.execute(
                "insert into site_announcement "
                "(singleton, id, message, starts_at, ends_at, withdrawn, updated_at) "
                "values (1, ?, ?, ?, ?, 0, ?) "
                "on conflict(singleton) do update set id = excluded.id, "
                "message = excluded.message, starts_at = excluded.starts_at, "
                "ends_at = excluded.ends_at, withdrawn = 0, updated_at = excluded.updated_at",
                (str(uuid.uuid4()), message, start, ends_at, timestamp),
            )
        else:
            cursor = conn.execute(
                "update site_announcement set message = ?, starts_at = ?, "
                "ends_at = ?, updated_at = ? where singleton = 1 and id = ?",
                (message, start, ends_at, timestamp, existing_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("Announcement has been replaced; reload before editing")
        row = conn.execute("select * from site_announcement where singleton = 1").fetchone()
    return _from_row(row)


def withdraw_announcement(
    existing_id: str, *, now: float | None = None, db_path: Path | None = None,
) -> Announcement:
    timestamp = time.time() if now is None else now
    with auth_db.connect_auth_db(db_path or auth_db.DEFAULT_AUTH_DB_PATH) as conn:
        cursor = conn.execute(
            "update site_announcement set withdrawn = 1, updated_at = ? "
            "where singleton = 1 and id = ?", (timestamp, existing_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("Announcement has been replaced; reload before editing")
        row = conn.execute("select * from site_announcement where singleton = 1").fetchone()
    return _from_row(row)

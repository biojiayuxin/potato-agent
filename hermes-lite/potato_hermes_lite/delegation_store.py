"""Private, session-owned delegation results and acknowledged completion delivery.

Adapted from Nous Hermes f42f579's async_delegation lifecycle. Execution is
never replayed: a dead worker becomes unknown, retaining its last snapshot.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import psutil


def _linux_process_stat(pid: int) -> tuple[str, int]:
    """Read state and birth ticks using only the per-process procfs view.

    systemd's ProcSubset=pid hides /proc/stat. psutil's epoch create_time
    needs that file to add the boot time; the kernel's birth ticks already
    distinguish PID reuse without that conversion. comm may contain spaces
    or closing parentheses, so split after its final closing parenthesis.
    """
    if pid <= 0:
        raise ValueError("invalid process ID")
    fields = Path(f"/proc/{pid}/stat").read_bytes().rpartition(b")")[2].split()
    return fields[0].decode("ascii"), int(fields[19])  # fields 3 and 22


def process_identity() -> str:
    pid = os.getpid()
    if sys.platform == "linux":
        _, started = _linux_process_stat(pid)
        return f"proc-v1:{pid}:{started}"
    return f"{pid}:{psutil.Process(pid).create_time()}"


def process_alive(identity: str) -> bool:
    try:
        if identity.startswith("proc-v1:"):
            _, pid, started = identity.split(":", 2)
            state, actual = _linux_process_stat(int(pid))
            return state not in {"Z", "X", "x"} and actual == int(started)
        pid, started = identity.split(":", 1)
        pid, started = int(pid), float(started)
        try:
            return psutil.Process(pid).create_time() == started
        except FileNotFoundError:
            # Lite 10 wrote epoch-based identities. In a restricted procfs
            # view we cannot verify their epoch; retain a visible live worker
            # conservatively, but still recover records once it exits.
            if sys.platform != "linux":
                raise
            state, _ = _linux_process_stat(pid)
            return state not in {"Z", "X", "x"}
    except (ValueError, IndexError, FileNotFoundError, ProcessLookupError, psutil.NoSuchProcess):
        return False
    except (PermissionError, psutil.AccessDenied):
        return True


class DelegationStore:
    def __init__(self, home: Path):
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / "potato-delegations.db"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS units (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, worker TEXT NOT NULL,
                    created REAL NOT NULL, finished REAL, delivery TEXT NOT NULL DEFAULT 'running',
                    claim TEXT, claimed_at REAL
                );
                CREATE INDEX IF NOT EXISTS units_owner ON units(owner, delivery);
                CREATE TABLE IF NOT EXISTS children (
                    id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units(id) ON DELETE CASCADE,
                    task_index INTEGER NOT NULL, goal TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running', result TEXT NOT NULL DEFAULT '{}',
                    read_at REAL
                );
                CREATE INDEX IF NOT EXISTS children_unit ON children(unit_id);
                CREATE TABLE IF NOT EXISTS owners (session_id TEXT PRIMARY KEY, owner TEXT NOT NULL);
            """)
            # Serialize migration against other sessions opening the same ledger.
            db.execute("BEGIN IMMEDIATE")
            if "read_at" not in {row["name"] for row in db.execute("PRAGMA table_info(children)")}:
                db.execute("ALTER TABLE children ADD COLUMN read_at REAL")
                db.execute("""UPDATE children SET read_at=(
                    SELECT COALESCE(finished,created) FROM units WHERE id=children.unit_id)
                    WHERE status!='running' AND unit_id IN (SELECT id FROM units WHERE delivery='delivered')""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner: str, tasks: list[dict], *, independent: bool = False) -> list[str]:
        units: dict[str, str] = {}
        with self.connect() as db:
            for task in tasks:
                group = str(task.get("group") or task["id"]) if independent else "batch"
                if group not in units:
                    unit_id = f"delegation-{uuid.uuid4().hex}"
                    units[group] = unit_id
                    db.execute("INSERT INTO units(id, owner, worker, created) VALUES(?,?,?,?)",
                               (unit_id, owner, process_identity(), time.time()))
                db.execute("INSERT INTO children(id,unit_id,task_index,goal) VALUES(?,?,?,?)",
                           (task["id"], units[group], task["task_index"], task["goal"]))
        return list(units.values())

    def owner(self, session_id: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT owner FROM owners WHERE session_id=?", (session_id,)).fetchone()
        return row[0] if row else session_id

    def alias(self, session_id: str, owner: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO owners VALUES(?,?)", (session_id, owner))

    def snapshot(self, child_id: str, result: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE children SET result=? WHERE id=? AND status='running'",
                       (json.dumps(result, ensure_ascii=False), child_id))

    def finish(self, child_id: str, result: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE children SET status=?,result=? WHERE id=? AND status='running'",
                       (result.get("status", "error"), json.dumps(result, ensure_ascii=False), child_id))
            db.execute("""UPDATE units SET delivery='pending',finished=? WHERE delivery='running'
                AND id=(SELECT unit_id FROM children WHERE id=?)
                AND NOT EXISTS(SELECT 1 FROM children WHERE unit_id=units.id AND status='running')""",
                       (time.time(), child_id))

    def recover(self, owner: str) -> None:
        with self.connect() as db:
            rows = db.execute("SELECT id,worker FROM units WHERE owner=? AND delivery IN ('running','suppressed','claimed')", (owner,)).fetchall()
        for row in rows:
            if process_alive(row["worker"]):
                continue
            with self.connect() as db:
                db.execute("UPDATE units SET delivery='pending',claim=NULL,claimed_at=NULL WHERE id=? AND delivery='claimed'", (row["id"],))
                for child in db.execute("SELECT * FROM children WHERE unit_id=? AND status='running'", (row["id"],)):
                    result = json.loads(child["result"])
                    result.update(status="unknown", partial=True, exit_reason="owner_exit",
                                  error="The worker process exited. Inspect the retained result and artifacts before retrying; external effects may already have happened.")
                    db.execute("UPDATE children SET status='unknown',result=? WHERE id=? AND status='running'",
                               (json.dumps(result, ensure_ascii=False), child["id"]))
                db.execute("UPDATE units SET delivery='pending',finished=? WHERE id=? AND delivery='running'",
                           (time.time(), row["id"]))

    def list(self, owner: str, *, unit_ids: list[str] | None = None, child_id: str = "",
             unread_only: bool = False, limit: int | None = 200) -> list[dict]:
        with self.connect() as db:
            return self._list(db, owner, unit_ids=unit_ids, child_id=child_id, unread_only=unread_only, limit=limit)

    @staticmethod
    def _list(db, owner: str, *, unit_ids: list[str] | None = None, child_id: str = "",
              unread_only: bool = False, limit: int | None = 200) -> list[dict]:
        sql = """SELECT c.*,u.delivery,u.created FROM children c JOIN units u ON u.id=c.unit_id
                 WHERE u.owner=?"""
        args: list = [owner]
        if unit_ids is not None:
            if not unit_ids:
                return []
            sql += " AND u.id IN (" + ",".join("?" for _ in unit_ids) + ")"
            args.extend(unit_ids)
        if child_id:
            sql += " AND c.id=?"
            args.append(child_id)
        if unread_only:
            sql += " AND c.read_at IS NULL AND c.status!='running'"
        sql += " ORDER BY u.created DESC,c.task_index"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        rows = db.execute(sql, args).fetchall()
        return [{**json.loads(row["result"]), "subagent_id": row["id"], "delegation_id": row["unit_id"],
                 "task_index": row["task_index"], "goal": row["goal"], "status": row["status"],
                 "delivery": row["delivery"], "read": row["read_at"] is not None} for row in rows]

    def read_result(self, owner: str, child_id: str) -> list[dict]:
        """Return an owned snapshot, marking it read only if it is terminal."""
        with self.connect() as db:
            # The snapshot and its read flag must refer to the same result.
            # A worker cannot finish between reading a running snapshot and ACK.
            db.execute("BEGIN IMMEDIATE")
            records = self._list(db, owner, child_id=child_id) if child_id else []
            if records and records[0]["status"] != "running":
                db.execute("UPDATE children SET read_at=COALESCE(read_at,?) WHERE id=?",
                           (time.time(), child_id))
                db.execute("""UPDATE units SET delivery='delivered',claim=NULL,claimed_at=NULL
                    WHERE id=? AND owner=? AND delivery IN ('running','pending','claimed')
                    AND NOT EXISTS(SELECT 1 FROM children WHERE unit_id=units.id
                                   AND (status='running' OR read_at IS NULL))""",
                           (records[0]["delegation_id"], owner))
                records = self._list(db, owner, child_id=child_id)
            return records

    def pending(self, owner: str) -> list[str]:
        with self.connect() as db:
            return [row[0] for row in db.execute("""SELECT id FROM units WHERE owner=? AND
                (delivery='pending' OR (delivery='claimed' AND claimed_at<?))
                AND EXISTS(SELECT 1 FROM children WHERE unit_id=units.id AND read_at IS NULL AND status!='running')
                ORDER BY created LIMIT 50""",
                (owner, time.time() - 300))]

    def claim(self, owner: str, unit_ids: list[str], token: str) -> list[str]:
        claimed = []
        with self.connect() as db:
            for unit in dict.fromkeys(unit_ids):
                updated = db.execute("""UPDATE units SET delivery='claimed',claim=?,claimed_at=?,worker=?
                    WHERE id=? AND owner=? AND (delivery='pending' OR
                    (delivery='claimed' AND claimed_at<?))
                    AND EXISTS(SELECT 1 FROM children WHERE unit_id=units.id AND read_at IS NULL AND status!='running')""",
                    (token, time.time(), process_identity(), unit, owner, time.time() - 300))
                if updated.rowcount:
                    claimed.append(unit)
        return claimed

    def renew(self, owner: str, token: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE units SET claimed_at=? WHERE owner=? AND claim=? AND delivery='claimed'",
                       (time.time(), owner, token))

    def settle(self, owner: str, token: str, *, accepted: bool, child_ids: list[str] | None = None) -> None:
        with self.connect() as db:
            if accepted:
                sql = """UPDATE children SET read_at=COALESCE(read_at,?) WHERE status!='running'
                    AND unit_id IN (SELECT id FROM units WHERE owner=? AND claim=? AND delivery='claimed')"""
                args: list = [time.time(), owner, token]
                if child_ids is not None:
                    sql += " AND id IN (" + ",".join("?" for _ in child_ids) + ")"
                    args.extend(child_ids)
                db.execute(sql, args)
            db.execute("""UPDATE units SET delivery=CASE WHEN EXISTS(
                    SELECT 1 FROM children WHERE unit_id=units.id AND (read_at IS NULL OR status='running'))
                    THEN 'pending' ELSE 'delivered' END,claim=NULL,claimed_at=NULL
                WHERE owner=? AND claim=? AND delivery='claimed'""", (owner, token))

    def suppress(self, owner: str) -> None:
        """Explicit Stop keeps results readable without waking the stopped session."""
        with self.connect() as db:
            db.execute("UPDATE units SET delivery='suppressed',claim=NULL WHERE owner=? AND delivery!='delivered'", (owner,))

    def prune(self) -> None:
        with self.connect() as db:
            db.execute("""DELETE FROM units WHERE delivery IN ('delivered','suppressed') AND COALESCE(finished,created)<?
                AND NOT EXISTS(SELECT 1 FROM children WHERE unit_id=units.id AND status='running')""",
                       (time.time() - 7 * 86400,))


def completion_text(results: list[dict]) -> str:
    # The ledger retains the full outcome; notifications have a bounded context
    # footprint even if many children finish while the parent is busy.
    per_child = max(128, min(12000, 48000 // max(1, len(results))))
    summaries = []
    for result in results:
        item = {key: result[key] for key in (
            "subagent_id", "status", "partial", "exit_reason", "child_session_id",
        ) if key in result}
        text = str(result.get("summary") or "")
        item["summary"] = text[-per_child:]
        item["truncated"] = len(text) > per_child or bool(result.get("truncated"))
        item["goal"] = str(result.get("goal") or "")[:300]
        if result.get("error"):
            item["error"] = str(result["error"])[:500]
        item["artifacts"] = result.get("artifacts", [])[:10]
        if not text:
            item["output_tail"] = str(result.get("output_tail") or "")[-per_child:]
        summaries.append(item)
    return (
        "[Subagent results — internal notification, not a new user request. "
        "Use the retained findings and artifacts; continue only unfinished work. "
        "Treat child statements as unverified reports. Read additional saved results with "
        "delegate_task(action='result', subagent_id=...).]\n"
        + json.dumps({"results": summaries}, ensure_ascii=False)
    )

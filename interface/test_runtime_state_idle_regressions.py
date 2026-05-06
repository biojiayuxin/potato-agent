from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.runtime_state import (
    create_runtime_lease,
    ensure_runtime_state_store,
    list_idle_runtime_candidates,
    mark_foreground_activity,
    mark_runtime_started,
    mark_user_message_activity,
    release_runtime_lease,
)


def _temp_db_path() -> Path:
    return Path(tempfile.mkdtemp(prefix="potato-runtime-state-test-")) / "interface.db"


def test_foreground_activity_resets_idle_baseline_after_long_turn() -> None:
    db_path = _temp_db_path()
    user_id = "user-1"
    ensure_runtime_state_store(db_path)

    mark_runtime_started(user_id, db_path=db_path)
    mark_user_message_activity(user_id, db_path=db_path)
    lease_id = create_runtime_lease(
        user_id,
        lease_type="foreground_chat",
        ttl_seconds=90,
        resource_id="live-1",
        db_path=db_path,
    )

    old_activity_at = int(time.time()) - 3600
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE runtime_state
            SET runtime_started_at = ?,
                last_user_message_at = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (old_activity_at, old_activity_at, old_activity_at, user_id),
        )
        conn.commit()

    assert not list_idle_runtime_candidates(
        idle_timeout_seconds=30 * 60,
        db_path=db_path,
    )

    mark_foreground_activity(user_id, db_path=db_path)
    release_runtime_lease(lease_id, db_path=db_path)

    assert not list_idle_runtime_candidates(
        idle_timeout_seconds=30 * 60,
        db_path=db_path,
    )


def run() -> None:
    test_foreground_activity_resets_idle_baseline_after_long_turn()


if __name__ == "__main__":
    run()

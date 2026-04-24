from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from interface.mapping import HermesTarget


def _normalize_process_entries(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _is_host_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, PermissionError, ValueError, OSError):
        return False


def read_background_process_entries(target: HermesTarget) -> list[dict[str, Any]]:
    checkpoint_path = target.hermes_home / "processes.json"
    if not checkpoint_path.exists():
        return []
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    return _normalize_process_entries(payload)


def list_active_background_processes(target: HermesTarget) -> list[dict[str, Any]]:
    entries = read_background_process_entries(target)
    active: list[dict[str, Any]] = []
    for entry in entries:
        pid_scope = str(entry.get("pid_scope") or "host").strip().lower() or "host"
        if pid_scope == "host":
            if _is_host_pid_alive(entry.get("pid")):
                active.append(entry)
            continue

        # Fail-open for non-host process scopes: if Hermes still lists the process,
        # treat it as active so the interface does not kill long-running work that
        # it cannot verify from the host side.
        active.append(entry)
    return active


def has_active_background_processes(target: HermesTarget) -> bool:
    return bool(list_active_background_processes(target))

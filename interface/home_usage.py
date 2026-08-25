from __future__ import annotations

import pwd
import stat
import subprocess
from pathlib import Path

from interface.mapping import HermesTarget
from interface.process_utils import run_process_group


HOME_USAGE_TIMEOUT_SECONDS = 120.0


class HomeUsageError(RuntimeError):
    pass


def build_home_usage_command(target: HermesTarget) -> list[str]:
    try:
        account = pwd.getpwnam(target.linux_user)
        home_stat = target.home_dir.lstat()
    except (KeyError, OSError) as exc:
        raise HomeUsageError("home usage unavailable") from exc
    if stat.S_ISLNK(home_stat.st_mode) or not stat.S_ISDIR(home_stat.st_mode):
        raise HomeUsageError("home usage unavailable")
    return [
        "runuser",
        "-u",
        account.pw_name,
        "--",
        "env",
        "-i",
        f"HOME={target.home_dir}",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "ionice",
        "-c",
        "3",
        "du",
        "-s",
        "-x",
        "-B1",
        "--",
        str(target.home_dir),
    ]


def measure_home_allocated_bytes(
    target: HermesTarget,
    *,
    timeout_seconds: float = HOME_USAGE_TIMEOUT_SECONDS,
) -> int:
    command = build_home_usage_command(target)
    try:
        result = run_process_group(command, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired:
        raise
    if result.returncode != 0:
        raise HomeUsageError("home usage unavailable")
    first_line = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
    raw_bytes, separator, _discarded_path = first_line.partition("\t")
    if not separator:
        raw_bytes, separator, _discarded_path = first_line.partition(" ")
    try:
        allocated_bytes = int(raw_bytes.strip())
    except ValueError as exc:
        raise HomeUsageError("home usage unavailable") from exc
    if allocated_bytes < 0:
        raise HomeUsageError("home usage unavailable")
    return allocated_bytes

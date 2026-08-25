from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


SYSTEM_SAMPLE_INTERVAL_SECONDS = 5.0
SYSTEM_SAMPLE_STALE_SECONDS = 20.0
SYSTEMCTL_TIMEOUT_SECONDS = 2.0
CGROUP_ROOT = Path("/sys/fs/cgroup")
LOADAVG_PATH = Path("/proc/loadavg")


def _optional_int(value: str | None) -> int | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized in {"[not set]", "infinity", "n/a"}:
        return None
    try:
        parsed = int(normalized)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _effective_cpu_count(value: str | None) -> int | None:
    normalized = str(value or "").strip()
    if not normalized or normalized == "[not set]":
        return None
    cpu_ids: set[int] = set()
    for group in normalized.replace(" ", ",").split(","):
        item = group.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                return None
            if start < 0 or end < start or end - start > 1_000_000:
                return None
            cpu_ids.update(range(start, end + 1))
        else:
            try:
                cpu_id = int(item)
            except ValueError:
                return None
            if cpu_id < 0:
                return None
            cpu_ids.add(cpu_id)
    return len(cpu_ids) or None


def _read_page_cache(control_group: str | None) -> int | None:
    normalized = str(control_group or "").strip()
    if not normalized.startswith("/") or ".." in Path(normalized).parts:
        return None
    candidate = (CGROUP_ROOT / normalized.lstrip("/") / "memory.stat").resolve()
    try:
        candidate.relative_to(CGROUP_ROOT.resolve())
        lines = candidate.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    for line in lines:
        key, _, raw_value = line.partition(" ")
        if key == "file":
            return _optional_int(raw_value)
    return None


def _read_load_average() -> dict[str, float | None]:
    try:
        values = [float(value) for value in os.getloadavg()]
    except (AttributeError, OSError):
        try:
            fields = LOADAVG_PATH.read_text(encoding="ascii").split()
            values = [float(value) for value in fields[:3]]
        except (OSError, ValueError):
            values = []
    return {
        "one": values[0] if len(values) > 0 else None,
        "five": values[1] if len(values) > 1 else None,
        "fifteen": values[2] if len(values) > 2 else None,
    }


def read_root_slice_sample() -> dict[str, Any]:
    properties = (
        "CPUUsageNSec,MemoryCurrent,MemoryAvailable,MemorySwapCurrent,"
        "EffectiveCPUs,ControlGroup"
    )
    result = subprocess.run(
        [
            "systemctl",
            "show",
            "--no-pager",
            f"--property={properties}",
            "--",
            "-.slice",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=SYSTEMCTL_TIMEOUT_SECONDS,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C"},
    )
    if result.returncode != 0:
        raise RuntimeError("system resource source is unavailable")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return {
        "cpu_usage_nsec": _optional_int(values.get("CPUUsageNSec")),
        "effective_cpus": _effective_cpu_count(values.get("EffectiveCPUs")),
        "memory_current": _optional_int(values.get("MemoryCurrent")),
        "memory_available": _optional_int(values.get("MemoryAvailable")),
        "page_cache": _read_page_cache(values.get("ControlGroup")),
        "swap_current": _optional_int(values.get("MemorySwapCurrent")),
        "load": _read_load_average(),
    }


class SystemResourceSampler:
    def __init__(
        self,
        *,
        reader: Callable[[], dict[str, Any]] = read_root_slice_sample,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        stale_seconds: float = SYSTEM_SAMPLE_STALE_SECONDS,
    ) -> None:
        self._reader = reader
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._stale_seconds = float(stale_seconds)
        self._lock = threading.Lock()
        self._previous_cpu: tuple[int, float] | None = None
        self._last_success_monotonic: float | None = None
        self._last_payload: dict[str, Any] | None = None

    def sample(self) -> dict[str, Any]:
        sampled_monotonic = self._monotonic()
        sampled_at = self._wall_time()
        try:
            raw = self._reader()
        except Exception:
            with self._lock:
                return self._snapshot_after_failure(sampled_monotonic)

        cpu_usage = raw.get("cpu_usage_nsec")
        effective_cpus = raw.get("effective_cpus")
        cpu_percent: float | None = None
        warming_up = False
        with self._lock:
            if isinstance(cpu_usage, int) and isinstance(effective_cpus, int) and effective_cpus > 0:
                previous = self._previous_cpu
                self._previous_cpu = (cpu_usage, sampled_monotonic)
                if previous is None or cpu_usage < previous[0] or sampled_monotonic <= previous[1]:
                    warming_up = True
                else:
                    elapsed = sampled_monotonic - previous[1]
                    delta = cpu_usage - previous[0]
                    cpu_percent = max(
                        0.0,
                        min(100.0, delta / (elapsed * 1_000_000_000 * effective_cpus) * 100.0),
                    )
            else:
                self._previous_cpu = None

            memory_current = raw.get("memory_current")
            memory_available = raw.get("memory_available")
            memory_total = (
                memory_current + memory_available
                if isinstance(memory_current, int) and isinstance(memory_available, int)
                else None
            )
            payload = {
                "status": "warming_up" if warming_up else "ok",
                "sampled_at": sampled_at,
                "cpu_percent": cpu_percent,
                "memory": {
                    "used_bytes": memory_current if isinstance(memory_current, int) else None,
                    "available_bytes": memory_available if isinstance(memory_available, int) else None,
                    "total_bytes": memory_total,
                },
                "page_cache_bytes": raw.get("page_cache")
                if isinstance(raw.get("page_cache"), int)
                else None,
                "swap_bytes": raw.get("swap_current")
                if isinstance(raw.get("swap_current"), int)
                else None,
                "load": raw.get("load")
                if isinstance(raw.get("load"), dict)
                else {"one": None, "five": None, "fifteen": None},
            }
            self._last_payload = payload
            self._last_success_monotonic = sampled_monotonic
            return dict(payload)

    def _snapshot_after_failure(self, now_monotonic: float) -> dict[str, Any]:
        if self._last_payload is None or self._last_success_monotonic is None:
            return {
                "status": "unavailable",
                "sampled_at": None,
                "cpu_percent": None,
                "memory": {
                    "used_bytes": None,
                    "available_bytes": None,
                    "total_bytes": None,
                },
                "page_cache_bytes": None,
                "swap_bytes": None,
                "load": {"one": None, "five": None, "fifteen": None},
            }
        payload = dict(self._last_payload)
        if now_monotonic - self._last_success_monotonic > self._stale_seconds:
            payload["status"] = "stale"
        return payload

    def snapshot(self) -> dict[str, Any]:
        now_monotonic = self._monotonic()
        with self._lock:
            return self._snapshot_after_failure(now_monotonic)

    async def run(self, interval_seconds: float = SYSTEM_SAMPLE_INTERVAL_SECONDS) -> None:
        while True:
            await asyncio.to_thread(self.sample)
            await asyncio.sleep(interval_seconds)

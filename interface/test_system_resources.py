from __future__ import annotations

import asyncio
import subprocess

from interface import system_resources
from interface.system_resources import (
    SystemResourceSampler,
    _effective_cpu_count,
    read_root_slice_sample,
)


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _raw(cpu: int | None, cores: int | None = 2):
    return {
        "cpu_usage_nsec": cpu,
        "effective_cpus": cores,
        "memory_current": 100,
        "memory_available": 300,
        "page_cache": 40,
        "swap_current": None,
        "load": {"one": 0.1, "five": 0.2, "fifteen": 0.3},
    }


def test_root_slice_reader_terminates_options_before_dash_prefixed_unit(
    tmp_path, monkeypatch
) -> None:
    memory_stat = tmp_path / "memory.stat"
    memory_stat.write_text("file 40\n", encoding="utf-8")
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.10 0.20 0.30 1/100 1\n", encoding="ascii")
    monkeypatch.setattr(system_resources, "CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(system_resources, "LOADAVG_PATH", loadavg)
    monkeypatch.setattr(system_resources.os, "getloadavg", lambda: (0.1, 0.2, 0.3))

    def fake_run(command, **_kwargs):
        assert command[-2:] == ["--", "-.slice"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "CPUUsageNSec=100\n"
                "MemoryCurrent=200\n"
                "MemoryAvailable=300\n"
                "MemorySwapCurrent=[not set]\n"
                "EffectiveCPUs=0-3\n"
                "ControlGroup=/\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(system_resources.subprocess, "run", fake_run)
    sample = read_root_slice_sample()

    assert sample == {
        "cpu_usage_nsec": 100,
        "effective_cpus": 4,
        "memory_current": 200,
        "memory_available": 300,
        "page_cache": 40,
        "swap_current": None,
        "load": {"one": 0.1, "five": 0.2, "fifteen": 0.3},
    }


def test_cpu_delta_is_normalized_by_elapsed_time_and_effective_cpus() -> None:
    clock = Clock(10)
    wall = Clock(100)
    samples = iter((_raw(1_000_000_000), _raw(3_000_000_000)))
    sampler = SystemResourceSampler(
        reader=lambda: next(samples), monotonic=clock, wall_time=wall
    )
    first = sampler.sample()
    assert first["status"] == "warming_up"
    assert first["cpu_percent"] is None

    clock.value = 12
    wall.value = 102
    second = sampler.sample()
    assert second["status"] == "ok"
    assert second["cpu_percent"] == 50.0
    assert second["memory"]["total_bytes"] == 400


def test_cpu_counter_rollback_restarts_warmup() -> None:
    clock = Clock(1)
    samples = iter((_raw(5_000_000_000), _raw(1_000_000_000)))
    sampler = SystemResourceSampler(reader=lambda: next(samples), monotonic=clock)
    sampler.sample()
    clock.value = 2
    rolled_back = sampler.sample()
    assert rolled_back["status"] == "warming_up"
    assert rolled_back["cpu_percent"] is None


def test_partial_fields_are_null_without_failing_whole_sample() -> None:
    sampler = SystemResourceSampler(reader=lambda: _raw(None, None))
    payload = sampler.sample()
    assert payload["status"] == "ok"
    assert payload["cpu_percent"] is None
    assert payload["memory"]["used_bytes"] == 100
    assert payload["swap_bytes"] is None


def test_failure_retains_last_good_and_marks_it_stale_after_threshold() -> None:
    clock = Clock(1)
    calls = 0

    def reader():
        nonlocal calls
        calls += 1
        if calls == 1:
            return _raw(None)
        raise RuntimeError("offline")

    sampler = SystemResourceSampler(
        reader=reader,
        monotonic=clock,
        wall_time=lambda: 100,
        stale_seconds=20,
    )
    good = sampler.sample()
    clock.value = 10
    recent_failure = sampler.sample()
    assert recent_failure == good
    clock.value = 22
    stale = sampler.sample()
    assert stale["status"] == "stale"
    assert stale["memory"]["used_bytes"] == 100


def test_unavailable_before_first_success_and_cpuset_parsing() -> None:
    sampler = SystemResourceSampler(reader=lambda: (_ for _ in ()).throw(RuntimeError()))
    assert sampler.sample()["status"] == "unavailable"
    assert _effective_cpu_count("0-3,8,10-11") == 7
    assert _effective_cpu_count("bad") is None


def test_sampler_background_task_can_be_cancelled_cleanly() -> None:
    async def exercise() -> None:
        sampler = SystemResourceSampler(reader=lambda: _raw(None))
        task = asyncio.create_task(sampler.run(interval_seconds=0.01))
        await asyncio.sleep(0.02)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)

    asyncio.run(exercise())

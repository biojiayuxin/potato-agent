#!/usr/bin/env python3
"""Measure cold gateway.ready latency in isolated temporary Hermes homes."""

from __future__ import annotations

import argparse
import json
import math
import os
import selectors
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from _common import (
    PACKAGING_DIR,
    REPO_ROOT,
    PackagingError,
    canonical_json_bytes,
    ensure_new_output_path,
    ensure_non_production_path,
    sha256_file,
)
from verify_profile import _validate_profile_configuration


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _wait_for_json(
    process: subprocess.Popen[str],
    *,
    deadline: float,
    predicate,
) -> tuple[dict[str, Any], float]:
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise PackagingError("gateway response timed out")
            events = selector.select(timeout=remaining)
            if not events:
                raise PackagingError("gateway response timed out")
            line = process.stdout.readline()
            if not line:
                code = process.poll()
                raise PackagingError(f"gateway exited before the expected response (exit {code})")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and predicate(payload):
                return payload, time.perf_counter()
    finally:
        selector.close()


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _one_run(
    *,
    python: Path,
    source: Path | None,
    wheel: Path | None,
    profile: Path,
    home: Path,
    timeout: float,
    rpc_method: str | None,
    rpc_params: dict[str, Any],
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(home / ".hermes"),
            "HERMES_RUNTIME_PROFILE_PATH": str(profile.resolve()),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    if source is not None:
        env["PYTHONPATH"] = str(source.resolve())
    elif wheel is not None:
        env["PYTHONPATH"] = str(wheel.resolve())
    else:
        env.pop("PYTHONPATH", None)

    stderr_path = home / "gateway.stderr.log"
    executable = Path(os.path.abspath(python.expanduser()))
    started = time.perf_counter()
    with stderr_path.open("w+", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [str(executable), "-B", "-m", "tui_gateway.entry"],
            cwd=home,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            bufsize=1,
        )
        try:
            ready, ready_at = _wait_for_json(
                process,
                deadline=started + timeout,
                predicate=lambda payload: payload.get("method") == "event"
                and (payload.get("params") or {}).get("type") == "gateway.ready",
            )
            result: dict[str, Any] = {
                "ready_seconds": ready_at - started,
                "ready_event": (ready.get("params") or {}).get("type"),
            }
            if rpc_method:
                assert process.stdin is not None
                request = {
                    "jsonrpc": "2.0",
                    "id": "benchmark-1",
                    "method": rpc_method,
                    "params": rpc_params,
                }
                rpc_started = time.perf_counter()
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
                response, response_at = _wait_for_json(
                    process,
                    deadline=rpc_started + timeout,
                    predicate=lambda payload: payload.get("id") == "benchmark-1",
                )
                if "error" in response:
                    raise PackagingError(f"gateway RPC {rpc_method} failed: {response['error']}")
                result["rpc_method"] = rpc_method
                result["rpc_seconds"] = response_at - rpc_started
            return result
        except Exception as exc:
            stderr.flush()
            stderr.seek(0)
            tail = stderr.read()[-4000:].strip()
            if isinstance(exc, PackagingError):
                raise PackagingError(str(exc) + (f"; stderr: {tail}" if tail else "")) from exc
            raise
        finally:
            _terminate(process)


def benchmark(
    *,
    python: Path,
    source: Path | None,
    installed: bool,
    wheel: Path | None,
    profile: Path,
    expected_dir: Path,
    runs: int,
    timeout: float,
    rpc_method: str | None,
    rpc_params: dict[str, Any],
    max_ready_seconds: float,
    max_p95_seconds: float,
    work_dir: Path | None,
) -> dict[str, Any]:
    if runs < 1 or runs > 1000:
        raise PackagingError("--runs must be between 1 and 1000")
    if timeout <= 0:
        raise PackagingError("--timeout must be positive")
    modes = sum((source is not None, installed, wheel is not None))
    if modes == 0:
        source = REPO_ROOT / "hermes-agent"
    elif modes != 1:
        raise PackagingError("choose only one of --source, --installed, or --wheel")
    if source is not None and not source.is_dir():
        raise PackagingError(f"source directory does not exist: {source}")
    if wheel is not None and not wheel.is_file():
        raise PackagingError(f"wheel does not exist: {wheel}")
    _validate_profile_configuration(
        profile,
        expected_dir / "tools.txt",
        expected_dir / "forbidden-module-prefixes.txt",
    )

    temp_parent = None
    if work_dir is not None:
        temp_parent = ensure_non_production_path(work_dir, label="work directory")
        temp_parent.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="potato-gateway-benchmark-", dir=temp_parent) as raw:
        root = Path(raw)
        for index in range(runs):
            home = root / f"run-{index + 1:03d}"
            home.mkdir()
            samples.append(
                _one_run(
                    python=python,
                    source=source,
                    wheel=wheel,
                    profile=profile,
                    home=home,
                    timeout=timeout,
                    rpc_method=rpc_method,
                    rpc_params=rpc_params,
                )
            )

    ready_values = [sample["ready_seconds"] for sample in samples]
    ready_summary = {
        "min": min(ready_values),
        "mean": statistics.fmean(ready_values),
        "p50": _percentile(ready_values, 0.50),
        "p95": _percentile(ready_values, 0.95),
        "max": max(ready_values),
    }
    failures = []
    if max_ready_seconds > 0 and ready_summary["max"] >= max_ready_seconds:
        failures.append(
            f"ready max {ready_summary['max']:.3f}s >= {max_ready_seconds:.3f}s"
        )
    if max_p95_seconds > 0 and ready_summary["p95"] > max_p95_seconds:
        failures.append(
            f"ready p95 {ready_summary['p95']:.3f}s > {max_p95_seconds:.3f}s"
        )
    result: dict[str, Any] = {
        "schema_version": 1,
        "runs": runs,
        "mode": "source" if source is not None else "wheel" if wheel else "installed",
        "profile_sha256": sha256_file(profile),
        "ready_seconds": ready_summary,
        "samples": samples,
        "gates": {
            "max_ready_seconds": max_ready_seconds,
            "max_p95_seconds": max_p95_seconds,
            "passed": not failures,
            "failures": failures,
        },
    }
    if rpc_method:
        rpc_values = [sample["rpc_seconds"] for sample in samples]
        result["rpc_seconds"] = {
            "method": rpc_method,
            "min": min(rpc_values),
            "mean": statistics.fmean(rpc_values),
            "p50": _percentile(rpc_values, 0.50),
            "p95": _percentile(rpc_values, 0.95),
            "max": max(rpc_values),
        }
    if failures:
        raise PackagingError("gateway benchmark gate failed: " + "; ".join(failures))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--source", type=Path)
    mode.add_argument("--installed", action="store_true")
    mode.add_argument("--wheel", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--profile", type=Path, default=PACKAGING_DIR / "runtime-profile.yaml")
    parser.add_argument("--expected-dir", type=Path, default=PACKAGING_DIR / "expected")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--rpc-method")
    parser.add_argument("--rpc-params", default="{}")
    parser.add_argument("--max-ready-seconds", type=float, default=30.0)
    parser.add_argument("--max-p95-seconds", type=float, default=15.0)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        try:
            rpc_params = json.loads(args.rpc_params)
        except json.JSONDecodeError as exc:
            raise PackagingError(f"--rpc-params must be JSON: {exc}") from exc
        if not isinstance(rpc_params, dict):
            raise PackagingError("--rpc-params must decode to an object")
        result = benchmark(
            python=args.python,
            source=args.source,
            installed=args.installed,
            wheel=args.wheel,
            profile=args.profile,
            expected_dir=args.expected_dir,
            runs=args.runs,
            timeout=args.timeout,
            rpc_method=args.rpc_method,
            rpc_params=rpc_params,
            max_ready_seconds=args.max_ready_seconds,
            max_p95_seconds=args.max_p95_seconds,
            work_dir=args.work_dir,
        )
        payload = canonical_json_bytes(result)
        if args.output is not None:
            output = ensure_new_output_path(args.output, label="benchmark output")
            output.parent.mkdir(parents=True, exist_ok=True)
            partial = output.with_name(f".{output.name}.part-{uuid.uuid4().hex}")
            try:
                partial.write_bytes(payload)
                os.replace(partial, output)
            finally:
                try:
                    partial.unlink()
                except FileNotFoundError:
                    pass
        sys.stdout.buffer.write(payload)
    except (PackagingError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sys
from pathlib import Path

from benchmark_gateway import benchmark


def test_benchmark_gateway_records_ready_and_rpc_latencies(
    tmp_path: Path, fake_profile_bundle
) -> None:
    source = tmp_path / "gateway-source"
    package = source / "tui_gateway"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "entry.py").write_text(
        """\
import json
import sys

print(json.dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready"}}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}), flush=True)
""",
        encoding="utf-8",
    )
    result = benchmark(
        python=Path(sys.executable),
        source=source,
        installed=False,
        wheel=None,
        profile=fake_profile_bundle.profile,
        expected_dir=fake_profile_bundle.expected,
        runs=2,
        timeout=5,
        rpc_method="session.create",
        rpc_params={},
        max_ready_seconds=5,
        max_p95_seconds=5,
        work_dir=None,
    )
    assert result["gates"]["passed"] is True
    assert result["ready_seconds"]["max"] < 5
    assert result["rpc_seconds"]["method"] == "session.create"

from __future__ import annotations

import json
import os
import subprocess
import sys


def test_write_and_patch_dispatch_without_removed_runtime_modules(runtime_paths) -> None:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(runtime_paths.home),
            "HERMES_HOME": str(runtime_paths.hermes_home),
            "HERMES_RUNTIME_PROFILE_PATH": str(runtime_paths.profile),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "PYTHONPATH": str(runtime_paths.source),
            "TERMINAL_CWD": str(runtime_paths.home),
        }
    )
    target = runtime_paths.home / "editable.txt"
    script = f"""
import json
from pathlib import Path
import model_tools

target = Path({str(target)!r})
written = json.loads(model_tools.handle_function_call(
    "write_file",
    {{"path": str(target), "content": "before\\n"}},
    task_id="lite-edit-regression",
))
patched = json.loads(model_tools.handle_function_call(
    "patch",
    {{
        "mode": "replace",
        "path": str(target),
        "old_string": "before",
        "new_string": "after",
    }},
    task_id="lite-edit-regression",
))
print(json.dumps({{
    "origin": model_tools.__file__,
    "written": written,
    "patched": patched,
    "content": target.read_text(encoding="utf-8"),
}}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runtime_paths.home,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["origin"].startswith(str(runtime_paths.source))
    assert payload["written"]["bytes_written"] == len("before\n")
    assert payload["patched"]["success"] is True
    assert payload["content"] == "after\n"
    assert "Edit approval denied" not in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
    assert "Traceback" not in result.stderr

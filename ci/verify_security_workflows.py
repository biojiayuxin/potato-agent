#!/usr/bin/env python3
"""Fail closed on unsafe GitHub Actions workflow constructs."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
USES_RE = re.compile(r"(?m)^\s*-?\s*uses:\s*[^\s@]+@([^\s#]+)")


def main() -> int:
    workflows = sorted((*WORKFLOW_ROOT.glob("*.yml"), *WORKFLOW_ROOT.glob("*.yaml")))
    if not workflows:
        raise SystemExit("no GitHub Actions workflows found")
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        if "pull_request_target" in text:
            raise SystemExit(f"pull_request_target is forbidden: {path}")
        if not re.search(r"(?ms)^permissions:\s*\n\s+contents:\s*read\s*$", text):
            raise SystemExit(f"workflow must declare top-level contents: read: {path}")
        for reference in USES_RE.findall(text):
            if re.fullmatch(r"[0-9a-f]{40}", reference) is None:
                raise SystemExit(f"action is not pinned to a commit SHA in {path}: {reference}")
        if re.search(r"\$\{\{\s*github\.(?:ref|head_ref|base_ref|event\.)", text):
            raise SystemExit(f"untrusted GitHub context interpolation is forbidden: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

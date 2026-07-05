#!/usr/bin/env python3
"""Remove configurable suffixes from one column in a tabular mapping file."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--column", type=int, default=2)
    parser.add_argument("--mode", choices=["numeric_suffix", "rsplit", "none"], default="numeric_suffix")
    parser.add_argument("--regex", default=r"\.[0-9]+$")
    return parser.parse_args()


def transform_value(value: str, mode: str, pattern: re.Pattern[str]) -> str:
    if mode == "numeric_suffix":
        return pattern.sub("", value)
    if mode == "rsplit":
        return value.rsplit(".", 1)[0]
    return value


def main() -> int:
    args = parse_args()
    if args.column < 1:
        raise ValueError("--column must be 1-based and greater than zero")

    pattern = re.compile(args.regex)
    input_path = Path(args.input)
    lines = input_path.read_text().splitlines()

    out_lines: list[str] = []
    modified = 0
    col_idx = args.column - 1
    for row_index, line in enumerate(lines):
        if row_index == 0 or not line.strip():
            out_lines.append(line)
            continue
        parts = line.split("\t")
        if len(parts) <= col_idx:
            out_lines.append(line)
            continue
        value = parts[col_idx].strip()
        if value in ("", "-"):
            out_lines.append(line)
            continue
        new_value = transform_value(value, args.mode, pattern)
        if new_value != value:
            parts[col_idx] = new_value
            modified += 1
        out_lines.append("\t".join(parts))

    Path(args.output).write_text("\n".join(out_lines) + "\n")
    Path(args.log).write_text(
        "\n".join(
            [
                f"input={args.input}",
                f"output={args.output}",
                f"column={args.column}",
                f"mode={args.mode}",
                f"regex={args.regex}",
                f"data_rows={max(len(lines) - 1, 0)}",
                f"modified_rows={modified}",
                "",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

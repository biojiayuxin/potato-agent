#!/usr/bin/env python3
"""Run one NCBI BLAST+ search and write the result directly to a file."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Sequence


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one NCBI BLAST+ search with caller-provided paths and parameters."
    )
    parser.add_argument(
        "--program",
        required=True,
        help="BLAST executable name or path, such as blastn, blastp, blastx, or tblastn",
    )
    parser.add_argument("--query", required=True, help="query FASTA path")
    parser.add_argument(
        "--database", "--db", dest="database", required=True, help="BLAST database prefix"
    )
    parser.add_argument("--output", required=True, help="result file path")
    parser.add_argument("--outfmt", help="optional NCBI BLAST output format")
    parser.add_argument(
        "--blast-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="additional BLAST arguments; this option must be last",
    )
    return parser.parse_args(argv)


def build_command(args: argparse.Namespace) -> list[str]:
    command = [
        args.program,
        "-query",
        args.query,
        "-db",
        args.database,
        "-out",
        args.output,
    ]
    if args.outfmt is not None:
        command.extend(["-outfmt", args.outfmt])
    command.extend(args.blast_args)
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        completed = subprocess.run(build_command(args), check=False)
    except OSError as error:
        print(f"failed to start BLAST: {error}", file=sys.stderr)
        return 127

    if completed.returncode == 0:
        result_path = os.path.abspath(os.path.expanduser(args.output))
        print(f"RESULT_PATH={result_path}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

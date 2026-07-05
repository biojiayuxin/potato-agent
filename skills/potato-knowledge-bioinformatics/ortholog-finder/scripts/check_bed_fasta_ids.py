#!/usr/bin/env python3
"""Check that BED feature IDs are represented in matching FASTA headers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def read_bed_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 4:
                ids.add(fields[3])
    return ids


def read_fasta_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open() as handle:
        for line in handle:
            if line.startswith(">"):
                ids.add(line[1:].strip().split()[0])
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        nargs=3,
        metavar=("PREFIX", "BED", "FASTA"),
        action="append",
        required=True,
        help="Dataset label plus matching BED and FASTA paths. Repeat for each genome.",
    )
    parser.add_argument(
        "--fail-on-fasta-extra",
        action="store_true",
        help="Also fail when FASTA contains IDs absent from BED.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("prefix\tbed_ids\tfasta_ids\tbed_not_in_fasta\tfasta_not_in_bed")
    errors: list[str] = []

    for prefix, bed_path, fasta_path in args.dataset:
        bed = Path(bed_path)
        fasta = Path(fasta_path)
        bed_ids = read_bed_ids(bed)
        fasta_ids = read_fasta_ids(fasta)
        bed_missing = bed_ids - fasta_ids
        fasta_extra = fasta_ids - bed_ids
        print(
            prefix,
            len(bed_ids),
            len(fasta_ids),
            len(bed_missing),
            len(fasta_extra),
            sep="\t",
        )
        if bed_missing:
            examples = ", ".join(sorted(bed_missing)[:5])
            errors.append(f"{prefix}: BED IDs absent from FASTA: {examples}")
        if args.fail_on_fasta_extra and fasta_extra:
            examples = ", ".join(sorted(fasta_extra)[:5])
            errors.append(f"{prefix}: FASTA IDs absent from BED: {examples}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

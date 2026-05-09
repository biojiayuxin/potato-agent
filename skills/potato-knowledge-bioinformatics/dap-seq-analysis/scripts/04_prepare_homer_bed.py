#!/usr/bin/env python3
"""Create a summit-centered BED file for one DAP-Seq target.

Designed for Snakemake rule prepare_homer_bed. Reads MACS2 <target>_summits.bed
and writes BED intervals spanning summit +/- flank bp.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def write_summit_bed(summit_file: Path, out_bed: Path, flank: int) -> int:
    n = 0
    out_bed.parent.mkdir(parents=True, exist_ok=True)
    with summit_file.open() as src, out_bed.open("w") as dst:
        for line in src:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 5:
                raise SystemExit(f"ERROR: malformed summit line in {summit_file}: {line.rstrip()}")
            chrom = fields[0]
            # MACS2 summits.bed has summit position in column 2/3 as a 1 bp interval.
            summit = int(fields[1])
            start = max(0, summit - flank)
            end = summit + flank
            name = fields[3]
            score = fields[4]
            dst.write(f"{chrom}\t{start}\t{end}\t{name}\t{score}\t.\n")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Create summit +/- flank BED for HOMER")
    ap.add_argument("--target-id", required=True)
    ap.add_argument("--summits", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--flank", type=int, default=100)
    args = ap.parse_args()

    summit_file = Path(args.summits)
    out_bed = Path(args.out)
    if not summit_file.exists():
        raise SystemExit(f"ERROR: summit file not found for target {args.target_id}: {summit_file}")
    count = write_summit_bed(summit_file, out_bed, args.flank)
    print(f"[DAP-Seq] {args.target_id}: wrote {count} regions to {out_bed} (summit +/- {args.flank} bp)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

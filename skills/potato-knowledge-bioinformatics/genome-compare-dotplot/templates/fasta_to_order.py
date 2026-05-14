#!/usr/bin/env python3
"""Generate MUMmer order file (seq_id<TAB>length<TAB>+) from one or two FASTA files.

Usage:
    fasta_to_order.py ref.fa ref.order.tsv            # single file
    fasta_to_order.py ref.fa qry.fa ref.order.tsv qry.order.tsv  # pair
"""
import sys
from pathlib import Path


def fasta_lengths(path: Path) -> list[tuple[str, int]]:
    records = []
    sid = None
    n = 0
    with path.open() as f:
        for line in f:
            if line.startswith(">"):
                if sid is not None:
                    records.append((sid, n))
                sid = line[1:].split()[0]
                n = 0
            else:
                n += len(line.strip())
        if sid is not None:
            records.append((sid, n))
    return records


def write_order(fasta: Path, out_path: Path) -> None:
    records = fasta_lengths(fasta)
    if not records:
        raise SystemExit(f"No FASTA records found: {fasta}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as out:
        for seq_id, length in records:
            out.write(f"{seq_id}\t{length}\t+\n")
    total = sum(r[1] for r in records)
    print(f"{fasta}: {len(records)} sequences, {total} bp -> {out_path}", file=sys.stderr)


def main():
    args = [Path(a) for a in sys.argv[1:]]
    if len(args) == 2:
        write_order(args[0], args[1])
    elif len(args) == 4:
        write_order(args[0], args[2])
        write_order(args[1], args[3])
    else:
        sys.exit("Usage: fasta_to_order.py ref.fa ref.order.tsv\n"
                 "       fasta_to_order.py ref.fa qry.fa ref.order.tsv qry.order.tsv")


if __name__ == "__main__":
    main()

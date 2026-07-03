#!/usr/bin/env python3
"""Run GMAP CDS-to-genome alignment and summarize GFF3 mRNA hits."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


def run(cmd: list[str], stdout_path: Path | None = None, stderr_path: Path | None = None) -> None:
    if stdout_path and stderr_path and stdout_path == stderr_path:
        with open(stdout_path, "w") as fh:
            subprocess.run(cmd, check=True, text=True, stdout=fh, stderr=subprocess.STDOUT)
        return

    stdout_fh = open(stdout_path, "w") if stdout_path else subprocess.DEVNULL
    stderr_fh = open(stderr_path, "w") if stderr_path else subprocess.DEVNULL
    try:
        subprocess.run(cmd, check=True, text=True, stdout=stdout_fh, stderr=stderr_fh)
    finally:
        if stdout_path:
            stdout_fh.close()
        if stderr_path:
            stderr_fh.close()


def read_fasta_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                ids.append(line[1:].strip().split()[0])
    return ids


def unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def parse_attrs(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in attr_text.strip().split(";"):
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            attrs[key] = unquote(value)
    return attrs


def candidate_query_values(attrs: dict[str, str]) -> list[str]:
    values: list[str] = []
    for key in ["Name", "ID", "Target", "Parent"]:
        value = attrs.get(key)
        if not value:
            continue
        values.append(value)
        values.append(value.strip().split()[0])
    return unique_ordered([value for value in values if value])


def strip_gmap_suffixes(value: str) -> list[str]:
    candidates = [value]
    patterns = [
        r"^(?P<query>.+?)(?:[._-]path\d+)$",
        r"^(?P<query>.+?)(?:[._-]mrna\d+)$",
        r"^(?P<query>.+?)(?:[._-]mRNA\d+)$",
        r"^(?P<query>.+?)(?:[._-]transcript\d+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, value)
        if match:
            candidates.append(match.group("query"))
    return unique_ordered(candidates)


def resolve_query_id(attrs: dict[str, str], query_ids: list[str]) -> str:
    known = set(query_ids)
    raw_values = candidate_query_values(attrs)
    for value in raw_values:
        for candidate in strip_gmap_suffixes(value):
            if candidate in known:
                return candidate

    prefix_matches: list[str] = []
    for value in raw_values:
        for qid in query_ids:
            if len(value) > len(qid) and value.startswith(qid) and value[len(qid)] in ".:_-/|":
                prefix_matches.append(qid)
    if prefix_matches:
        return max(prefix_matches, key=len)

    return raw_values[0] if raw_values else "unknown"


def parse_gff3(gff3: Path, query_ids: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(gff3) as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9 or fields[2] != "mRNA":
                continue
            seqid, source, feature, start, end, score, strand, phase, attrs_text = fields
            attrs = parse_attrs(attrs_text)
            query = resolve_query_id(attrs, query_ids)
            row = {
                "query_id": query,
                "seqid": seqid,
                "start": start,
                "end": end,
                "strand": strand,
                "coverage_percent": attrs.get("coverage", ""),
                "identity_percent": attrs.get("identity", ""),
            }
            rows.append(row)
    return rows


def format_percent(value: str) -> str:
    return f"{value}%" if value else "NA"


def write_summary(rows: list[dict[str, str]], tsv: Path, txt: Path, gff3: Path) -> None:
    fieldnames = [
        "query_id", "seqid", "start", "end", "strand",
        "coverage_percent", "identity_percent",
    ]
    with open(tsv, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    with open(txt, "w") as fh:
        fh.write(f"GFF3: {gff3}\n")
        fh.write(f"Summary table: {tsv}\n")
        fh.write(f"Aligned locations: {len(rows)}\n")
        if rows:
            for index, row in enumerate(rows, start=1):
                coverage = format_percent(row["coverage_percent"])
                identity = format_percent(row["identity_percent"])
                fh.write(
                    "{index}. {query_id} -> {seqid}:{start}-{end}({strand}), "
                    "coverage={coverage}, identity={identity}\n".format(
                        index=index,
                        coverage=coverage,
                        identity=identity,
                        **row,
                    )
                )
        else:
            fh.write(
                "No aligned CDS locations were found in mRNA features of the GMAP GFF3 output.\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Align CDS FASTA to genome FASTA with GMAP and summarize GFF3 hits.")
    parser.add_argument("--genome", required=True, type=Path, help="Target genome FASTA")
    parser.add_argument("--cds", required=True, type=Path, help="CDS nucleotide FASTA")
    parser.add_argument("--outdir", required=True, type=Path, help="Output directory")
    parser.add_argument("--db-name", default="genome_gmap", help="GMAP database name")
    parser.add_argument("--threads", type=int, default=4, help="Threads for gmap_build and gmap")
    parser.add_argument("--npaths", type=int, default=1, help="Maximum GMAP paths per query. Use >1 for allele/copy discovery.")
    parser.add_argument("--reuse-db", action="store_true", help="Reuse an existing GMAP DB under outdir/db/db-name")
    args = parser.parse_args()

    if args.npaths < 1:
        print("ERROR: --npaths must be a positive integer", file=sys.stderr)
        return 2

    for exe in ["gmap", "gmap_build"]:
        if shutil.which(exe) is None:
            print(f"ERROR: required command not found on PATH: {exe}", file=sys.stderr)
            return 2

    genome = args.genome.resolve()
    cds = args.cds.resolve()
    if not genome.exists():
        print(f"ERROR: genome FASTA not found: {genome}", file=sys.stderr)
        return 2
    if not cds.exists():
        print(f"ERROR: CDS FASTA not found: {cds}", file=sys.stderr)
        return 2

    outdir = args.outdir.resolve()
    db_dir = outdir / "db"
    result_dir = outdir / "result"
    db_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    build_log = result_dir / "gmap_build.log"
    gmap_log = result_dir / "gmap.gff3.log"
    gff3 = result_dir / "cds_to_genome.gff3"
    summary_tsv = result_dir / "alignment_summary.tsv"
    summary_txt = result_dir / "alignment_summary.txt"

    db_path = db_dir / args.db_name
    if not args.reuse_db or not db_path.exists():
        run([
            "gmap_build", "-D", str(db_dir), "-d", args.db_name,
            "-t", str(args.threads), str(genome),
        ], stdout_path=build_log, stderr_path=build_log)

    run([
        "gmap", "-D", str(db_dir), "-d", args.db_name,
        "-t", str(args.threads),
        "-f", "gff3_gene", "--gff3-add-separators=0",
        f"--npaths={args.npaths}", "--nofails", str(cds),
    ], stdout_path=gff3, stderr_path=gmap_log)

    query_ids = unique_ordered(read_fasta_ids(cds))
    rows = parse_gff3(gff3, query_ids)
    write_summary(rows, summary_tsv, summary_txt, gff3)

    print(f"GFF3: {gff3}")
    print(f"Summary table: {summary_tsv}")
    print(f"Text summary: {summary_txt}")
    with open(summary_txt) as fh:
        for line in fh:
            print(line.rstrip("\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Extract genomic windows from a FASTA file.

Coordinates are 1-based closed intervals. For strand '-', the extracted sequence
is reverse-complemented so the output is oriented 5' -> 3' for the requested
feature/gene direction.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional


RC_TABLE = str.maketrans(
    "ACGTRYKMSWBDHVNacgtrykmswbdhvn",
    "TGCAYRMKSWVHDBNtgcayrmkswvhdbn",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract 1-based closed genomic windows from FASTA.")
    p.add_argument("--genome", required=True, help="Genome FASTA file")
    p.add_argument("--output", required=True, help="Output FASTA file")
    p.add_argument("--report", help="Output TSV report")
    p.add_argument("--regions", help="TSV with columns: name, seqid, start, end, strand; optional note")
    p.add_argument("--seqid", help="Sequence/chromosome ID for single-region mode")
    p.add_argument("--start", type=int, help="1-based inclusive start for single-region mode")
    p.add_argument("--end", type=int, help="1-based inclusive end for single-region mode")
    p.add_argument("--strand", choices=["+", "-"], default="+", help="Strand for single-region mode [default: +]")
    p.add_argument("--name", help="Output FASTA record name for single-region mode")
    p.add_argument("--note", default="", help="Optional note for single-region mode")
    p.add_argument("--clip", action="store_true", help="Clip out-of-bound coordinates to sequence boundaries")
    p.add_argument("--wrap", type=int, default=60, help="FASTA line width [default: 60]")
    args = p.parse_args()

    single_fields = [args.seqid is not None, args.start is not None, args.end is not None]
    if args.regions and any(single_fields):
        p.error("Use either --regions or single-region arguments, not both")
    if not args.regions and not all(single_fields):
        p.error("Provide --regions or all of --seqid --start --end")
    return args


def iter_fasta(path: str) -> Iterable[tuple[str, str]]:
    header = None
    seq_parts: List[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(seq_parts)
                header = line[1:].strip()
                seq_parts = []
            else:
                seq_parts.append(line.strip())
    if header is not None:
        yield header, "".join(seq_parts)


def load_genome(path: str) -> Dict[str, str]:
    genome: Dict[str, str] = {}
    aliases: Dict[str, str] = {}
    for header, seq in iter_fasta(path):
        primary = header.split()[0] if header.split() else header
        if primary in genome:
            raise ValueError(f"Duplicate FASTA primary ID: {primary}")
        genome[primary] = seq
        aliases[header] = primary
    if not genome:
        raise ValueError(f"No FASTA records found in {path}")
    return genome


def revcomp(seq: str) -> str:
    return seq.translate(RC_TABLE)[::-1]


def wrap(seq: str, width: int) -> str:
    if width <= 0:
        return seq
    return "\n".join(seq[i : i + width] for i in range(0, len(seq), width))


def read_regions(args: argparse.Namespace) -> List[dict]:
    if args.regions:
        with open(args.regions, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            required = {"name", "seqid", "start", "end"}
            if reader.fieldnames is None:
                raise ValueError("Regions TSV has no header")
            missing = required - set(reader.fieldnames)
            if missing:
                raise ValueError(f"Regions TSV missing required columns: {','.join(sorted(missing))}")
            regions = []
            for i, row in enumerate(reader, start=2):
                if not row.get("name") or not row.get("seqid"):
                    raise ValueError(f"Missing name/seqid at line {i}")
                strand = (row.get("strand") or "+").strip()
                if strand not in {"+", "-"}:
                    raise ValueError(f"Invalid strand at line {i}: {strand}")
                regions.append(
                    {
                        "name": row["name"].strip(),
                        "seqid": row["seqid"].strip(),
                        "start": int(row["start"]),
                        "end": int(row["end"]),
                        "strand": strand,
                        "note": row.get("note", ""),
                    }
                )
            return regions
    name = args.name or f"{args.seqid}:{args.start}-{args.end}:{args.strand}"
    return [
        {
            "name": name,
            "seqid": args.seqid,
            "start": args.start,
            "end": args.end,
            "strand": args.strand,
            "note": args.note,
        }
    ]


def extract_region(region: dict, genome: Dict[str, str], clip: bool) -> dict:
    seqid = region["seqid"]
    if seqid not in genome:
        return {**region, "status": "MISSING_SEQID", "message": f"seqid not found: {seqid}", "sequence": "", "actual_start": "", "actual_end": "", "requested_length": "", "actual_length": 0}

    chrom = genome[seqid]
    chrom_len = len(chrom)
    req_start = int(region["start"])
    req_end = int(region["end"])
    if req_start > req_end:
        return {**region, "status": "INVALID_COORDS", "message": "start > end", "sequence": "", "actual_start": "", "actual_end": "", "requested_length": "", "actual_length": 0}

    requested_length = req_end - req_start + 1
    start = req_start
    end = req_end
    clipped = False
    if start < 1 or end > chrom_len:
        if not clip:
            return {**region, "status": "OUT_OF_BOUNDS", "message": f"requested {req_start}-{req_end}, sequence length {chrom_len}; rerun with --clip to truncate", "sequence": "", "actual_start": "", "actual_end": "", "requested_length": requested_length, "actual_length": 0}
        start = max(1, start)
        end = min(chrom_len, end)
        clipped = True
        if start > end:
            return {**region, "status": "EMPTY_AFTER_CLIP", "message": f"requested {req_start}-{req_end}, sequence length {chrom_len}", "sequence": "", "actual_start": start, "actual_end": end, "requested_length": requested_length, "actual_length": 0}

    seq = chrom[start - 1 : end]
    if region.get("strand", "+") == "-":
        seq = revcomp(seq)
    status = "CLIPPED" if clipped else "OK"
    message = "coordinate clipped to sequence boundary" if clipped else ""
    return {**region, "status": status, "message": message, "sequence": seq, "actual_start": start, "actual_end": end, "requested_length": requested_length, "actual_length": len(seq)}


def main() -> int:
    args = parse_args()
    genome = load_genome(args.genome)
    regions = read_regions(args)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    results = []
    with open(args.output, "w", encoding="utf-8") as out:
        for region in regions:
            result = extract_region(region, genome, args.clip)
            results.append(result)
            if result["sequence"]:
                header = (
                    f"{result['name']} seqid={result['seqid']} requested={result['start']}-{result['end']} "
                    f"actual={result['actual_start']}-{result['actual_end']} strand={result['strand']} "
                    f"length={result['actual_length']} status={result['status']}"
                )
                if result.get("note"):
                    header += f" note={str(result['note']).replace(' ', '_')}"
                out.write(f">{header}\n{wrap(result['sequence'], args.wrap)}\n")

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "name", "seqid", "start", "end", "strand", "actual_start", "actual_end",
            "requested_length", "actual_length", "status", "message", "note",
        ]
        with open(args.report, "w", encoding="utf-8", newline="") as rep:
            writer = csv.DictWriter(rep, delimiter="\t", fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for result in results:
                writer.writerow(result)

    ok_like = sum(1 for r in results if r["status"] in {"OK", "CLIPPED"})
    failures = len(results) - ok_like
    sys.stderr.write(
        f"regions={len(results)} extracted={ok_like} failed={failures} output={args.output}\n"
    )
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

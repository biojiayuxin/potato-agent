#!/usr/bin/env python3
"""Extract FASTA records by ID.

This script is intentionally dependency-free and supports common FASTA headers
used in genome annotation outputs. It preserves original FASTA headers in the
output and writes a TSV report describing matches.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple


ATTR_KEYS = {
    "id", "ID",
    "gene", "Gene", "gene_id", "geneID", "geneId",
    "transcript", "transcript_id", "transcriptID", "transcriptId",
    "protein", "protein_id", "proteinID", "proteinId",
    "Parent", "parent", "Name", "name", "locus_tag",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract FASTA records by query IDs with primary/full/contains/smart matching."
    )
    p.add_argument("--fasta", required=True, help="Input FASTA file")
    p.add_argument("--ids", action="append", help="File containing query IDs, one per line or first column")
    p.add_argument("--id", action="append", dest="inline_ids", help="Query ID; may be supplied multiple times")
    p.add_argument("--output", required=True, help="Output FASTA file")
    p.add_argument("--report", help="Output TSV report")
    p.add_argument("--missing", help="Output missing query IDs, one per line")
    p.add_argument(
        "--match-mode",
        choices=["primary", "full", "contains", "smart"],
        default="smart",
        help="How query IDs are matched against FASTA headers [default: smart]",
    )
    p.add_argument(
        "--case-insensitive",
        action="store_true",
        help="Match IDs case-insensitively; output report keeps original query text",
    )
    p.add_argument(
        "--deduplicate-records",
        action="store_true",
        help="Write each matched FASTA record only once even if it matches multiple queries",
    )
    args = p.parse_args()
    if not args.ids and not args.inline_ids:
        p.error("Provide at least one --ids file or --id value")
    return args


def read_query_ids(id_files: Sequence[str] | None, inline_ids: Sequence[str] | None) -> List[str]:
    ids: List[str] = []
    if inline_ids:
        for x in inline_ids:
            x = x.strip()
            if x:
                ids.append(x)
    if id_files:
        for file_name in id_files:
            with open(file_name, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    ids.append(line.split()[0])
    # Preserve order while removing duplicate query IDs.
    seen: Set[str] = set()
    unique: List[str] = []
    for q in ids:
        if q not in seen:
            unique.append(q)
            seen.add(q)
    return unique


def iter_fasta(path: str) -> Iterable[Tuple[str, str]]:
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


def wrap_sequence(seq: str, width: int = 60) -> str:
    return "\n".join(seq[i : i + width] for i in range(0, len(seq), width))


def header_tokens(header: str) -> Set[str]:
    tokens: Set[str] = set()
    primary = header.split()[0] if header.split() else header
    if primary:
        tokens.add(primary)
    if header:
        tokens.add(header)

    # Common key=value, key:value, key "value" style attributes in FASTA headers.
    for key in ATTR_KEYS:
        patterns = [
            rf"(?:^|[\s;|,]){re.escape(key)}=([^\s;|,]+)",
            rf"(?:^|[\s;|,]){re.escape(key)}:([^\s;|,]+)",
            rf"(?:^|[\s;|,]){re.escape(key)}\s+([^\s;|,]+)",
        ]
        for pat in patterns:
            for m in re.finditer(pat, header):
                value = m.group(1).strip().strip('"\'')
                if value:
                    tokens.add(value)

    # GFF-like attributes sometimes appear after spaces/semicolons.
    for m in re.finditer(r"(?:^|[\s;])([A-Za-z_][A-Za-z0-9_]*)=([^\s;]+)", header):
        key, value = m.group(1), m.group(2).strip().strip('"\'')
        if key in ATTR_KEYS and value:
            tokens.add(value)

    # Some FASTA IDs use version suffixes. Add unversioned versions as auxiliary tokens.
    more = set()
    for tok in tokens:
        if re.search(r"\.\d+$", tok):
            more.add(re.sub(r"\.\d+$", "", tok))
    tokens.update(more)
    return tokens


def normalize(text: str, case_insensitive: bool) -> str:
    return text.lower() if case_insensitive else text


def main() -> int:
    args = parse_args()
    queries = read_query_ids(args.ids, args.inline_ids)
    query_norm_to_originals: Dict[str, List[str]] = defaultdict(list)
    for q in queries:
        query_norm_to_originals[normalize(q, args.case_insensitive)].append(q)
    query_norms = set(query_norm_to_originals)

    matches: Dict[str, List[Tuple[str, str, str, int]]] = defaultdict(list)
    record_index = 0

    for header, seq in iter_fasta(args.fasta):
        record_index += 1
        primary = header.split()[0] if header.split() else header
        matched_norms: Set[str] = set()

        if args.match_mode == "primary":
            key = normalize(primary, args.case_insensitive)
            if key in query_norms:
                matched_norms.add(key)
        elif args.match_mode == "full":
            key = normalize(header, args.case_insensitive)
            if key in query_norms:
                matched_norms.add(key)
        elif args.match_mode == "contains":
            hay = normalize(header, args.case_insensitive)
            for qn in query_norms:
                if qn in hay:
                    matched_norms.add(qn)
        else:  # smart
            toks = {normalize(t, args.case_insensitive) for t in header_tokens(header)}
            matched_norms.update(query_norms.intersection(toks))

        for qn in matched_norms:
            for original_q in query_norm_to_originals[qn]:
                matches[original_q].append((header, seq, primary, record_index))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written_records: Set[int] = set()
    with open(output_path, "w", encoding="utf-8") as out:
        for q in queries:
            for header, seq, _primary, idx in matches.get(q, []):
                if args.deduplicate_records and idx in written_records:
                    continue
                out.write(f">{header}\n{wrap_sequence(seq)}\n")
                written_records.add(idx)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as rep:
            rep.write("query_id\tstatus\tmatch_count\tmatched_primary_ids\tmatched_headers\tlengths\n")
            for q in queries:
                ms = matches.get(q, [])
                if not ms:
                    rep.write(f"{q}\tMISSING\t0\t\t\t\n")
                else:
                    primaries = ";".join(m[2] for m in ms)
                    headers = ";".join(m[0] for m in ms)
                    lengths = ";".join(str(len(m[1])) for m in ms)
                    status = "OK" if len(ms) == 1 else "MULTI_MATCH"
                    rep.write(f"{q}\t{status}\t{len(ms)}\t{primaries}\t{headers}\t{lengths}\n")

    missing = [q for q in queries if not matches.get(q)]
    if args.missing:
        missing_path = Path(args.missing)
        missing_path.parent.mkdir(parents=True, exist_ok=True)
        with open(missing_path, "w", encoding="utf-8") as miss:
            for q in missing:
                miss.write(q + "\n")

    total_matches = sum(len(v) for v in matches.values())
    sys.stderr.write(
        f"queries={len(queries)} matched_queries={len(queries)-len(missing)} "
        f"missing_queries={len(missing)} total_record_matches={total_matches} output={args.output}\n"
    )
    return 0 if len(missing) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

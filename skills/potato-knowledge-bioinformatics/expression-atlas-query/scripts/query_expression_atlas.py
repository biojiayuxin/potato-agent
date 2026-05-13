#!/usr/bin/env python3
"""Query local Expression_atlas gene/transcript expression matrices.

Default data root: /mnt/data/public_data/Expression_atlas
The script intentionally uses only Python standard library so it works in the
base Hermes environment.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_BASE_DIR = Path(os.environ.get("EXPRESSION_ATLAS_DIR", "/mnt/data/public_data/Expression_atlas"))
MATRIX_SUFFIXES = {".tsv", ".txt", ".csv", ".tsv.gz", ".txt.gz", ".csv.gz"}
FEATURE_ALIASES = {
    "transcript_id", "transcript", "transcriptid", "tx_id", "txid",
    "gene_id", "gene", "geneid", "gene_name", "gene_symbol", "symbol", "name",
}
META_HINTS = ("sample_tissue", "sample", "metadata", "meta")


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


def normalized_header(name: str) -> str:
    return name.strip().lstrip("\ufeff").replace("\r", "")


def lower_key(name: str) -> str:
    return normalized_header(name).strip().lower().replace(" ", "_")


def delimiter_for(path: Path, first_line: str = "") -> str:
    name = path.name.lower()
    if name.endswith(".csv") or name.endswith(".csv.gz"):
        return ","
    if first_line:
        if first_line.count("\t") >= first_line.count(","):
            return "\t"
        return ","
    return "\t"


def read_header(path: Path) -> List[str]:
    with open_text(path) as handle:
        first = handle.readline()
    if not first:
        return []
    delim = delimiter_for(path, first)
    return [normalized_header(x) for x in next(csv.reader([first], delimiter=delim))]


def is_candidate_text_table(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith("."):
        return False
    if any(part.startswith(".") for part in path.parts):
        return False
    return any(name.endswith(suf) for suf in MATRIX_SUFFIXES)


def infer_unit(path: Path) -> str:
    n = path.name.lower()
    if "tpm" in n:
        return "TPM"
    if "fpkm" in n:
        return "FPKM"
    if "rpkm" in n:
        return "RPKM"
    if "count" in n or "counts" in n:
        return "count"
    return "expression_value"


def feature_columns(header: Sequence[str]) -> List[str]:
    feats = []
    for col in header:
        if lower_key(col) in FEATURE_ALIASES:
            feats.append(col)
    return feats


def find_column(header: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    lookup = {lower_key(c): c for c in header}
    for cand in candidates:
        if cand in lookup:
            return lookup[cand]
    return None


def looks_like_expression_matrix(path: Path) -> bool:
    try:
        header = read_header(path)
    except Exception:
        return False
    if len(header) < 4:
        return False
    feats = feature_columns(header)
    if not any(lower_key(c) in {"gene_id", "gene", "geneid", "transcript_id", "transcript", "transcriptid"} for c in feats):
        return False
    lname = path.name.lower()
    if "sample_tissue" in lname or "metadata" in lname or "meta" in lname:
        return False
    return True


def looks_like_sample_metadata(path: Path) -> bool:
    try:
        header = read_header(path)
    except Exception:
        return False
    keys = {lower_key(c) for c in header}
    return ("sample_column" in keys and ("tissue" in keys or "sample_name" in keys)) or ("sample" in keys and "tissue" in keys)


def dataset_name_for(base_dir: Path, matrix_path: Path) -> Tuple[str, Path]:
    rel = matrix_path.relative_to(base_dir)
    if len(rel.parts) >= 2:
        return rel.parts[0], base_dir / rel.parts[0]
    return base_dir.name, base_dir


def find_metadata_for(dataset_dir: Path, matrix_path: Path) -> Optional[Path]:
    candidates = []
    for p in dataset_dir.rglob("*"):
        if not p.is_file() or not is_candidate_text_table(p):
            continue
        if p == matrix_path:
            continue
        lname = p.name.lower()
        score = sum(1 for h in META_HINTS if h in lname)
        if score and looks_like_sample_metadata(p):
            candidates.append((score, len(str(p)), p))
    if candidates:
        return sorted(candidates, key=lambda x: (-x[0], x[1]))[0][2]
    return None


def discover_matrices(base_dir: Path) -> List[Dict[str, Any]]:
    base_dir = base_dir.resolve()
    if not base_dir.exists():
        raise FileNotFoundError(f"Expression atlas base directory not found: {base_dir}")
    records = []
    for p in sorted(base_dir.rglob("*")):
        if not p.is_file() or not is_candidate_text_table(p):
            continue
        if not looks_like_expression_matrix(p):
            continue
        header = read_header(p)
        dataset, dataset_dir = dataset_name_for(base_dir, p)
        meta = find_metadata_for(dataset_dir, p)
        feats = feature_columns(header)
        meta_map = read_sample_metadata(meta) if meta else {}
        sample_cols = infer_sample_columns(header, feats, meta_map)
        records.append({
            "dataset": dataset,
            "dataset_dir": str(dataset_dir),
            "matrix": str(p),
            "matrix_name": p.name,
            "unit": infer_unit(p),
            "feature_columns": feats,
            "sample_columns": len(sample_cols),
            "sample_metadata": str(meta) if meta else None,
        })
    return records


def read_sample_metadata(path: Optional[Path]) -> Dict[str, Dict[str, str]]:
    if not path:
        return {}
    with open_text(path) as handle:
        first = handle.readline()
        if not first:
            return {}
        delim = delimiter_for(path, first)
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=delim)
        if reader.fieldnames is None:
            return {}
        fieldnames = [normalized_header(x) for x in reader.fieldnames]
        reader.fieldnames = fieldnames
        sample_col = find_column(fieldnames, ["sample_column", "sample", "run", "sample_id"])
        if not sample_col:
            return {}
        meta: Dict[str, Dict[str, str]] = {}
        for row in reader:
            clean = {normalized_header(k): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k is not None}
            key = clean.get(sample_col, "").strip()
            if key:
                meta[key] = clean
        return meta


def infer_sample_columns(header: Sequence[str], feats: Sequence[str], meta_map: Dict[str, Dict[str, str]]) -> List[str]:
    feat_set = set(feats)
    if meta_map:
        cols = [c for c in header if c in meta_map]
        if cols:
            return cols
    if feats:
        last_feat_idx = max(header.index(c) for c in feats if c in header)
        return [c for c in header[last_feat_idx + 1:] if c not in feat_set]
    return list(header[1:])


def dm8_variants(query: str) -> List[str]:
    q = query.strip()
    vals = {q}
    if not q:
        return []
    # Strip transcript suffix for gene-level matching.
    if re.search(r"\.\d+$", q):
        vals.add(re.sub(r"\.\d+$", "", q))

    m = re.match(r"^(DM8C)(\d{1,2})G([0-9A-Za-z]+)(?:\.(\d+))?$", q, flags=re.I)
    if m:
        chrom = m.group(2).zfill(2)
        rest = m.group(3)
        tx = f".{m.group(4)}" if m.group(4) else ""
        vals.add(f"DM8.2_chr{chrom}G{rest}{tx}")
        vals.add(f"DM8.2_chr{chrom}G{rest}")

    m = re.match(r"^DM8(?:\.2)?[_\.-]?chr(\d{1,2})G([0-9A-Za-z]+)(?:\.(\d+))?$", q, flags=re.I)
    if m:
        chrom = m.group(1).zfill(2)
        rest = m.group(2)
        tx = f".{m.group(3)}" if m.group(3) else ""
        vals.add(f"DM8C{chrom}G{rest}{tx}")
        vals.add(f"DM8C{chrom}G{rest}")
        vals.add(f"DM8.2_chr{chrom}G{rest}{tx}")
        vals.add(f"DM8.2_chr{chrom}G{rest}")

    return sorted(vals, key=lambda x: (len(x), x))


def value_key(x: str) -> str:
    return x.strip().casefold()


def row_matches(row: Dict[str, str], query: str, mode: str, feats: Sequence[str]) -> bool:
    vals = [row.get(c, "") for c in feats]
    vals = [v for v in vals if v is not None and str(v).strip()]
    if mode == "regex":
        pattern = re.compile(query, flags=re.I)
        return any(pattern.search(str(v)) for v in vals)
    variants = dm8_variants(query)
    if mode == "contains":
        qs = [value_key(v) for v in variants]
        return any(q in value_key(str(v)) for q in qs for v in vals)
    wanted = {value_key(v) for v in variants}
    return any(value_key(str(v)) in wanted for v in vals)


def to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s.upper() in {"NA", "NAN", "NULL"}:
            return None
        x = float(s)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def passes_filter(meta: Dict[str, str], tissue_filters: Sequence[str], sample_filters: Sequence[str]) -> bool:
    if tissue_filters:
        tissue = value_key(meta.get("tissue", ""))
        if tissue not in {value_key(x) for x in tissue_filters}:
            return False
    if sample_filters:
        sample_name = value_key(meta.get("sample_name", meta.get("sample", "")))
        if sample_name not in {value_key(x) for x in sample_filters}:
            return False
    return True


def stat_summary(values: Sequence[float]) -> Dict[str, Any]:
    if not values:
        return {"n_values": 0}
    nonzero = sum(1 for v in values if v != 0)
    return {
        "n_values": len(values),
        "mean": round(float(sum(values) / len(values)), 6),
        "median": round(float(statistics.median(values)), 6),
        "min": round(float(min(values)), 6),
        "max": round(float(max(values)), 6),
        "nonzero_count": nonzero,
        "nonzero_fraction": round(nonzero / len(values), 6),
    }


def summarize_records(records: Sequence[Dict[str, Any]], group_by: str, top: int) -> List[Dict[str, Any]]:
    if group_by == "none":
        return []
    groups: Dict[str, List[float]] = defaultdict(list)
    for rec in records:
        if group_by == "sample_column":
            key = rec.get("sample_column") or "unknown"
        else:
            key = rec.get(group_by) or "unknown"
        groups[str(key)].append(float(rec["value"]))
    out = []
    for key, vals in groups.items():
        item = {group_by: key}
        item.update(stat_summary(vals))
        out.append(item)
    out.sort(key=lambda d: (d.get("mean", -1), d.get("max", -1)), reverse=True)
    return out[:top]


def feature_summary(matched_rows: Sequence[Dict[str, Any]], records: Sequence[Dict[str, Any]], top: int) -> List[Dict[str, Any]]:
    by_tx: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    for rec in records:
        key = (rec.get("transcript_id", ""), rec.get("gene_id", ""), rec.get("gene_name", ""))
        by_tx[key].append(float(rec["value"]))
    items = []
    for (tx, gid, gname), vals in by_tx.items():
        item = {"transcript_id": tx, "gene_id": gid, "gene_name": gname}
        item.update(stat_summary(vals))
        items.append(item)
    items.sort(key=lambda d: (d.get("mean", -1), d.get("max", -1)), reverse=True)
    return items[:top]


def combine_records_if_needed(records: List[Dict[str, Any]], matched_rows: Sequence[Dict[str, Any]], mode: str, query: str) -> Tuple[List[Dict[str, Any]], str]:
    if mode == "none" or not records:
        return records, "raw_transcript_values"
    unique_genes = {r.get("gene_id", "") for r in matched_rows if r.get("gene_id", "")}
    unique_txs = {r.get("transcript_id", "") for r in matched_rows if r.get("transcript_id", "")}
    has_tx_suffix = bool(re.search(r"\.\d+$", query.strip()))
    effective = mode
    if mode == "auto":
        if len(unique_genes) == 1 and len(unique_txs) > 1 and not has_tx_suffix:
            effective = "sum"
        else:
            return records, "raw_transcript_values"
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        key = (rec.get("dataset", ""), rec.get("matrix", ""), rec.get("sample_column", ""))
        grouped[key].append(rec)
    combined: List[Dict[str, Any]] = []
    for (_, _, _), vals in grouped.items():
        first = dict(vals[0])
        nums = [float(v["value"]) for v in vals]
        if effective == "sum":
            val = sum(nums)
        elif effective == "mean":
            val = sum(nums) / len(nums)
        else:
            val = nums[0]
        first["value"] = round(float(val), 6)
        first["transcript_id"] = f"<combined:{len(vals)} transcripts>"
        first["gene_id"] = next(iter(unique_genes)) if len(unique_genes) == 1 else first.get("gene_id", "")
        combined.append(first)
    return combined, f"combined_transcripts_{effective}"


def query_one_matrix(record: Dict[str, Any], query: str, args: argparse.Namespace) -> Dict[str, Any]:
    matrix = Path(record["matrix"])
    meta_path = Path(record["sample_metadata"]) if record.get("sample_metadata") else None
    meta_map = read_sample_metadata(meta_path)
    with open_text(matrix) as handle:
        first = handle.readline()
        delim = delimiter_for(matrix, first)
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=delim)
        if reader.fieldnames is None:
            raise ValueError(f"Matrix has no header: {matrix}")
        header = [normalized_header(x) for x in reader.fieldnames]
        reader.fieldnames = header
        feats = feature_columns(header)
        tx_col = find_column(header, ["transcript_id", "transcript", "transcriptid", "tx_id", "txid"])
        gid_col = find_column(header, ["gene_id", "gene", "geneid"])
        gname_col = find_column(header, ["gene_name", "gene_symbol", "symbol", "name"])
        sample_cols = infer_sample_columns(header, feats, meta_map)
        matched_rows: List[Dict[str, Any]] = []
        long_records: List[Dict[str, Any]] = []
        total_matches = 0
        truncated = False
        for row in reader:
            if not row_matches(row, query, args.match, feats):
                continue
            total_matches += 1
            if len(matched_rows) >= args.max_features:
                truncated = True
                continue
            feature = {
                "transcript_id": row.get(tx_col, "") if tx_col else "",
                "gene_id": row.get(gid_col, "") if gid_col else "",
                "gene_name": row.get(gname_col, "") if gname_col else "",
            }
            matched_rows.append(feature)
            for sample_col in sample_cols:
                val = to_float(row.get(sample_col))
                if val is None:
                    continue
                meta = meta_map.get(sample_col, {"sample_column": sample_col})
                if not passes_filter(meta, args.tissue or [], args.sample_name or []):
                    continue
                rec = {
                    "dataset": record["dataset"],
                    "matrix": record["matrix_name"],
                    "unit": record["unit"],
                    "transcript_id": feature["transcript_id"],
                    "gene_id": feature["gene_id"],
                    "gene_name": feature["gene_name"],
                    "sample_column": sample_col,
                    "sample_name": meta.get("sample_name", meta.get("sample", "")),
                    "tissue": meta.get("tissue", ""),
                    "value": val,
                }
                long_records.append(rec)
    summary_records, basis = combine_records_if_needed(long_records, matched_rows, args.combine_transcripts, query)
    result = {
        "dataset": record["dataset"],
        "matrix": record["matrix_name"],
        "matrix_path": record["matrix"],
        "sample_metadata": record.get("sample_metadata"),
        "unit": record["unit"],
        "n_matched_features_total": total_matches,
        "n_matched_features_returned": len(matched_rows),
        "truncated_by_max_features": truncated,
        "summary_basis": basis,
        "matched_features": feature_summary(matched_rows, long_records, args.top),
        "summary": summarize_records(summary_records, args.summary, args.top),
        "n_expression_values": len(summary_records),
    }
    if args.include_values:
        vals_sorted = sorted(summary_records, key=lambda r: float(r["value"]), reverse=True)
        result["values"] = vals_sorted[:args.top]
    return result


def write_long_tsv(path: Path, dataset_results: Sequence[Dict[str, Any]], query: str, args: argparse.Namespace) -> None:
    # Re-query with include all values in a streaming-friendly way is overkill for normal use.
    # This function is implemented by temporarily forcing include_values to a large number via direct scan.
    # For simplicity and correctness, call the same low-level scanner and write rows immediately here.
    fields = ["dataset", "matrix", "unit", "transcript_id", "gene_id", "gene_name", "sample_column", "sample_name", "tissue", "value"]
    path.parent.mkdir(parents=True, exist_ok=True)
    # The caller stores raw records in a hidden key when output_tsv is requested.
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for res in dataset_results:
            for rec in res.pop("_raw_records_for_tsv", []):
                writer.writerow({k: rec.get(k, "") for k in fields})


def query_one_matrix_with_raw(record: Dict[str, Any], query: str, args: argparse.Namespace) -> Dict[str, Any]:
    # Same as query_one_matrix, with a second compact raw scan for TSV export only.
    result = query_one_matrix(record, query, args)
    if not args.output_tsv:
        return result
    matrix = Path(record["matrix"])
    meta_path = Path(record["sample_metadata"]) if record.get("sample_metadata") else None
    meta_map = read_sample_metadata(meta_path)
    raw_records: List[Dict[str, Any]] = []
    with open_text(matrix) as handle:
        first = handle.readline()
        delim = delimiter_for(matrix, first)
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=delim)
        header = [normalized_header(x) for x in (reader.fieldnames or [])]
        reader.fieldnames = header
        feats = feature_columns(header)
        tx_col = find_column(header, ["transcript_id", "transcript", "transcriptid", "tx_id", "txid"])
        gid_col = find_column(header, ["gene_id", "gene", "geneid"])
        gname_col = find_column(header, ["gene_name", "gene_symbol", "symbol", "name"])
        sample_cols = infer_sample_columns(header, feats, meta_map)
        collected_features = 0
        for row in reader:
            if not row_matches(row, query, args.match, feats):
                continue
            collected_features += 1
            if collected_features > args.max_features:
                break
            feature = {
                "transcript_id": row.get(tx_col, "") if tx_col else "",
                "gene_id": row.get(gid_col, "") if gid_col else "",
                "gene_name": row.get(gname_col, "") if gname_col else "",
            }
            for sample_col in sample_cols:
                val = to_float(row.get(sample_col))
                if val is None:
                    continue
                meta = meta_map.get(sample_col, {"sample_column": sample_col})
                if not passes_filter(meta, args.tissue or [], args.sample_name or []):
                    continue
                raw_records.append({
                    "dataset": record["dataset"],
                    "matrix": record["matrix_name"],
                    "unit": record["unit"],
                    "transcript_id": feature["transcript_id"],
                    "gene_id": feature["gene_id"],
                    "gene_name": feature["gene_name"],
                    "sample_column": sample_col,
                    "sample_name": meta.get("sample_name", meta.get("sample", "")),
                    "tissue": meta.get("tissue", ""),
                    "value": val,
                })
    result["_raw_records_for_tsv"] = raw_records
    return result


def cmd_list_datasets(args: argparse.Namespace) -> None:
    records = discover_matrices(Path(args.base_dir))
    if args.with_counts:
        for rec in records:
            try:
                with open_text(Path(rec["matrix"])) as handle:
                    rec["rows_including_header"] = sum(1 for _ in handle)
            except Exception as exc:
                rec["row_count_error"] = str(exc)
    print(json.dumps({"base_dir": str(Path(args.base_dir).resolve()), "datasets": records}, ensure_ascii=False, indent=args.indent))


def cmd_list_tissues(args: argparse.Namespace) -> None:
    records = discover_matrices(Path(args.base_dir))
    if args.dataset and args.dataset != "all":
        records = [r for r in records if r["dataset"] == args.dataset]
    out = []
    for rec in records:
        meta = read_sample_metadata(Path(rec["sample_metadata"])) if rec.get("sample_metadata") else {}
        tissue_counts = Counter((m.get("tissue") or "unknown") for m in meta.values())
        sample_name_counts = Counter((m.get("sample_name") or m.get("sample") or "unknown") for m in meta.values())
        out.append({
            "dataset": rec["dataset"],
            "matrix": rec["matrix_name"],
            "unit": rec["unit"],
            "sample_metadata": rec.get("sample_metadata"),
            "n_sample_columns_in_matrix": rec["sample_columns"],
            "n_sample_metadata_rows": len(meta),
            "tissues": dict(sorted(tissue_counts.items())),
            "sample_names": dict(sorted(sample_name_counts.items())),
        })
    print(json.dumps({"base_dir": str(Path(args.base_dir).resolve()), "datasets": out}, ensure_ascii=False, indent=args.indent))


def cmd_query(args: argparse.Namespace) -> None:
    records = discover_matrices(Path(args.base_dir))
    if args.dataset and args.dataset != "all":
        records = [r for r in records if r["dataset"] == args.dataset]
    if not records:
        raise SystemExit(f"No expression matrices found for dataset={args.dataset!r} under {args.base_dir}")
    results = [query_one_matrix_with_raw(r, args.query, args) for r in records]
    if args.output_tsv:
        write_long_tsv(Path(args.output_tsv), results, args.query, args)
    printable = []
    for res in results:
        res = dict(res)
        res.pop("_raw_records_for_tsv", None)
        printable.append(res)
    out = {
        "base_dir": str(Path(args.base_dir).resolve()),
        "query": args.query,
        "query_variants_tried": dm8_variants(args.query),
        "match_mode": args.match,
        "filters": {"tissue": args.tissue or [], "sample_name": args.sample_name or []},
        "output_tsv": args.output_tsv,
        "datasets": printable,
    }
    print(json.dumps(out, ensure_ascii=False, indent=args.indent))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Query local Expression_atlas expression matrices")
    p.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR), help="Expression_atlas root directory")
    p.add_argument("--indent", type=int, default=2, help="JSON indentation; use 0 for compact")
    sub = p.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-datasets", help="List discovered expression matrices")
    p_list.add_argument("--with-counts", action="store_true", help="Count matrix rows; slower for large files")
    p_list.set_defaults(func=cmd_list_datasets)

    p_tissue = sub.add_parser("list-tissues", help="Summarize sample metadata by tissue and sample_name")
    p_tissue.add_argument("--dataset", default="all", help="Dataset name, e.g. DMv8.2; default all")
    p_tissue.set_defaults(func=cmd_list_tissues)

    p_query = sub.add_parser("query", help="Query expression for a gene/transcript ID or gene name")
    p_query.add_argument("query", help="Gene/transcript query, e.g. DM8.2_chr01G00010 or DM8C01G00010")
    p_query.add_argument("--dataset", default="all", help="Dataset name, e.g. DMv8.2; default all")
    p_query.add_argument("--match", choices=["exact", "contains", "regex"], default="exact")
    p_query.add_argument("--summary", choices=["tissue", "sample_name", "sample_column", "none"], default="tissue")
    p_query.add_argument("--top", type=int, default=10, help="Top groups/features/values to print")
    p_query.add_argument("--tissue", action="append", help="Restrict to exact tissue name; can repeat")
    p_query.add_argument("--sample-name", action="append", help="Restrict to exact sample_name; can repeat")
    p_query.add_argument("--max-features", type=int, default=50, help="Maximum matching feature rows to collect")
    p_query.add_argument("--combine-transcripts", choices=["auto", "none", "sum", "mean"], default="auto", help="How to combine multiple transcripts of one gene before summary")
    p_query.add_argument("--include-values", action="store_true", help="Include top raw/combined sample values in JSON")
    p_query.add_argument("--output-tsv", help="Write matched long-format expression records to TSV")
    p_query.set_defaults(func=cmd_query)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.indent == 0:
        args.indent = None
    try:
        args.func(args)
        return 0
    except BrokenPipeError:
        return 0
    except Exception as exc:
        eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

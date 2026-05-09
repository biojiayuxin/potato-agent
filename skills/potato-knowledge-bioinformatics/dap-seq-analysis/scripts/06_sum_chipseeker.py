#!/usr/bin/env python3
"""Summarize ChIPseeker annotations for DAP-Seq/ChIP-like workflows.

Outputs produced by default for input `{sampleID}.anno.with_intergenic.txt`:
  1. `{sampleID}.anno.no_distal_intergenic.txt`
  2. `{sampleID}.anno.no_distal_intergenic.merge_by_transcript.tsv`
  3. `{sampleID}.peaks_sum.txt`

The script accepts a config file so it can be called from Snakemake with
parameters provided by config/config.yaml. Command-line options override config
values when both are provided.
"""

from __future__ import annotations

import argparse
import csv
from collections import OrderedDict
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

DEFAULT_DROP_ANNOTATION = "Distal Intergenic"
DEFAULT_FEATURE_ORDER = [
    "Total Peaks",
    "Intergenic",
    "Promoter",
    "5' UTR",
    "Exon",
    "Intron",
    "3' UTR",
    "Downstream",
    "Other",
]


def load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        raise SystemExit(f"ERROR: config file not found: {config_path}")
    if yaml is None:
        raise SystemExit("ERROR: PyYAML is required for --config")
    with config_path.open() as fh:
        return yaml.safe_load(fh) or {}


def resolve_path(path_text: str | None, base_dir: Path) -> str:
    if not path_text:
        return ""
    p = Path(path_text)
    if p.is_absolute():
        return str(p)
    return str(base_dir / p)


def default_sample_id(input_path: Path) -> str:
    name = input_path.name
    for suffix in [".anno.with_intergenic.txt", ".with_intergenic.txt", ".txt", ".tsv"]:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return input_path.stem


def parse_feature(annotation: str) -> str:
    value = (annotation or "").strip()
    lower = value.lower()
    if value == "Distal Intergenic" or "intergenic" in lower:
        return "Intergenic"
    if lower.startswith("promoter"):
        return "Promoter"
    if lower.startswith("5' utr") or lower.startswith("5 utr") or "5' utr" in lower:
        return "5' UTR"
    if lower.startswith("3' utr") or lower.startswith("3 utr") or "3' utr" in lower:
        return "3' UTR"
    if lower.startswith("exon"):
        return "Exon"
    if lower.startswith("intron"):
        return "Intron"
    if lower.startswith("downstream"):
        return "Downstream"
    return "Other"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter ChIPseeker results, merge annotations by transcriptId, and summarize peak genomic distribution."
    )
    parser.add_argument("--config", default="", help="Workflow config YAML; command-line options override config values.")
    parser.add_argument("--base-dir", default=".", help="Base directory used to resolve relative config paths.")
    parser.add_argument("--sample-id", default="", help="Sample/target ID for output naming, e.g. ERF1.")
    parser.add_argument("--input", default="", help="Input ChIPseeker TSV, e.g. ERF1.anno.with_intergenic.txt.")
    parser.add_argument("--outdir", default="", help="Output ChIPseeker result directory. Default: directory of --input.")
    parser.add_argument("--filtered-output", default="", help="Default: <outdir>/<sampleID>.anno.no_distal_intergenic.txt")
    parser.add_argument("--merged-output", default="", help="Default: <outdir>/<sampleID>.anno.no_distal_intergenic.merge_by_transcript.tsv")
    parser.add_argument("--peaks-sum-output", default="", help="Default: <outdir>/<sampleID>.peaks_sum.txt")
    parser.add_argument("--drop-annotation", default="", help="Exact annotation to remove. Default: Distal Intergenic")
    parser.add_argument("--keep-count", action="store_true", help="Include non_distal_peak_count column in merged output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).resolve()
    config = load_config(args.config)
    chip_cfg = config.get("chipseeker", {}) or {}

    input_text = args.input or chip_cfg.get("summary_input", "")
    if not input_text:
        raise SystemExit("ERROR: --input is required unless chipseeker.summary_input is set in config")
    input_path = Path(resolve_path(input_text, base_dir))
    if not input_path.exists() or input_path.stat().st_size == 0:
        raise SystemExit(f"ERROR: input file missing or empty: {input_path}")

    sample_id = args.sample_id or chip_cfg.get("sample_id", "") or default_sample_id(input_path)
    outdir_text = args.outdir or chip_cfg.get("summary_outdir", "")
    outdir = Path(resolve_path(outdir_text, base_dir)) if outdir_text else input_path.parent
    outdir.mkdir(parents=True, exist_ok=True)

    drop_annotation = args.drop_annotation or chip_cfg.get("drop_annotation", DEFAULT_DROP_ANNOTATION)
    keep_count = bool(args.keep_count or chip_cfg.get("keep_count", False))

    filtered_output = Path(resolve_path(args.filtered_output, base_dir)) if args.filtered_output else outdir / f"{sample_id}.anno.no_distal_intergenic.txt"
    merged_output = Path(resolve_path(args.merged_output, base_dir)) if args.merged_output else outdir / f"{sample_id}.anno.no_distal_intergenic.merge_by_transcript.tsv"
    peaks_sum_output = Path(resolve_path(args.peaks_sum_output, base_dir)) if args.peaks_sum_output else outdir / f"{sample_id}.peaks_sum.txt"

    with input_path.open(newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise SystemExit("ERROR: input file has no header")
        missing = {"annotation", "transcriptId"} - set(fieldnames)
        if missing:
            raise SystemExit(f"ERROR: input file missing required columns: {sorted(missing)}")
        rows = list(reader)

    kept = [row for row in rows if row.get("annotation") != drop_annotation]

    filtered_output.parent.mkdir(parents=True, exist_ok=True)
    with filtered_output.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept)

    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in kept:
        transcript_id = row.get("transcriptId", "").strip()
        if not transcript_id or transcript_id in {".", "NA", "nan"}:
            continue
        gene_id = row.get("geneId", "").strip()
        annotation = row.get("annotation", "").strip()
        if transcript_id not in groups:
            groups[transcript_id] = {"geneId": gene_id, "annotations": OrderedDict(), "peak_count": 0}
        groups[transcript_id]["peak_count"] += 1
        if annotation:
            groups[transcript_id]["annotations"].setdefault(annotation, None)

    merged_output.parent.mkdir(parents=True, exist_ok=True)
    with merged_output.open("w", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        header = ["transcriptId", "geneId", "peak_annotation_types"]
        if keep_count:
            header.append("non_distal_peak_count")
        writer.writerow(header)
        for transcript_id, record in groups.items():
            out_row = [transcript_id, record["geneId"], ",".join(record["annotations"].keys())]
            if keep_count:
                out_row.append(str(record["peak_count"]))
            writer.writerow(out_row)

    feature_counts = OrderedDict((feature, 0) for feature in DEFAULT_FEATURE_ORDER)
    feature_counts["Total Peaks"] = len(rows)
    for row in rows:
        feature = parse_feature(row.get("annotation", ""))
        feature_counts[feature] = feature_counts.get(feature, 0) + 1

    peaks_sum_output.parent.mkdir(parents=True, exist_ok=True)
    with peaks_sum_output.open("w", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["feature", "peak_count"])
        for feature, count in feature_counts.items():
            writer.writerow([feature, count])

    print(f"input\t{input_path}")
    print(f"filtered_output\t{filtered_output}")
    print(f"merged_output\t{merged_output}")
    print(f"peaks_sum_output\t{peaks_sum_output}")
    print(f"total_records\t{len(rows)}")
    print(f"removed_{drop_annotation.replace(' ', '_')}\t{len(rows) - len(kept)}")
    print(f"kept_records\t{len(kept)}")
    print(f"merged_transcripts\t{len(groups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check DAP-Seq config and sample manifest before running heavy steps.

This script is intentionally lightweight. It only validates paths, columns,
control/treatment relationships, and paired FASTQ naming consistency; it does
not decompress whole FASTQ files.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # keep the error clear for future agents
    yaml = None

REQUIRED_COLUMNS = [
    "sample_id",
    "target_id",
    "replicate",
    "read1",
    "read2",
    "control_id",
    "is_control",
]


def load_yaml(path: Path) -> dict:
    if yaml is None:
        raise SystemExit("ERROR: Python package PyYAML is required: pip install pyyaml")
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    return data


def load_samples(path: Path) -> list[dict[str, str]]:
    # Allow leading comments in template manifests while keeping TSV parsing simple.
    lines = [line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        raise SystemExit("ERROR: samples.tsv has no header/sample rows")
    reader = csv.DictReader(lines, delimiter="\t")
    missing = [col for col in REQUIRED_COLUMNS if col not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit(f"ERROR: samples.tsv missing required columns: {', '.join(missing)}")
    rows = list(reader)
    if not rows:
        raise SystemExit("ERROR: samples.tsv has no sample rows")
    return rows


def resolve_path(path_text: str, base_dir: Path) -> Path:
    p = Path(path_text)
    return p if p.is_absolute() else base_dir / p


def check_file(path_text: str, label: str, required: bool = True, base_dir: Path | None = None) -> bool:
    if not path_text or path_text == ".":
        if required:
            print(f"ERROR: {label} is empty", file=sys.stderr)
            return False
        return True
    p = resolve_path(path_text, base_dir or Path.cwd())
    if not p.exists():
        print(f"ERROR: {label} not found: {p}", file=sys.stderr)
        return False
    if not p.is_file():
        print(f"ERROR: {label} is not a file: {p}", file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate DAP-Seq config and sample manifest")
    ap.add_argument("--config", required=True, help="config.yaml")
    ap.add_argument("--samples", required=True, help="samples.tsv")
    ap.add_argument(
        "--base-dir",
        default=".",
        help="Base directory for resolving relative paths inside config/samples; default: current directory",
    )
    args = ap.parse_args()

    base_dir = Path(args.base_dir).resolve()
    config_path = resolve_path(args.config, base_dir)
    samples_path = resolve_path(args.samples, base_dir)
    ok = True

    if not check_file(str(config_path), "config"):
        return 2
    if not check_file(str(samples_path), "samples"):
        return 2

    cfg = load_yaml(config_path)
    rows = load_samples(samples_path)

    ok &= check_file(str(cfg.get("genome_fasta", "")), "genome_fasta", base_dir=base_dir)
    ok &= check_file(str(cfg.get("gff_file", "")), "gff_file", base_dir=base_dir)
    homer_cfg = cfg.get("homer", {}) or {}
    homer_genome = str(homer_cfg.get("genome_for_homer", "") or "")
    if homer_genome:
        ok &= check_file(homer_genome, "homer.genome_for_homer", base_dir=base_dir)
    annotation_table = str(cfg.get("annotation_table", "") or "")
    if annotation_table:
        ok &= check_file(annotation_table, "annotation_table", required=False, base_dir=base_dir)

    sample_ids = [r["sample_id"] for r in rows]
    if len(sample_ids) != len(set(sample_ids)):
        duplicates = sorted({x for x in sample_ids if sample_ids.count(x) > 1})
        print(f"ERROR: duplicated sample_id values: {', '.join(duplicates)}", file=sys.stderr)
        ok = False

    sample_set = set(sample_ids)
    controls = {r["sample_id"] for r in rows if r["is_control"].lower() in {"yes", "y", "true", "1"}}
    treatments = [r for r in rows if r["is_control"].lower() not in {"yes", "y", "true", "1"}]

    if not controls:
        print("ERROR: no control sample found (is_control=yes)", file=sys.stderr)
        ok = False
    if not treatments:
        print("ERROR: no treatment sample found (is_control=no)", file=sys.stderr)
        ok = False

    for row in rows:
        sid = row["sample_id"]
        ok &= check_file(row["read1"], f"read1 for {sid}", base_dir=base_dir)
        ok &= check_file(row["read2"], f"read2 for {sid}", base_dir=base_dir)
        if row["read1"] == row["read2"]:
            print(f"ERROR: read1 and read2 are identical for {sid}", file=sys.stderr)
            ok = False
        if row["is_control"].lower() not in {"yes", "y", "true", "1"}:
            ctrl = row["control_id"]
            if ctrl not in sample_set:
                print(f"ERROR: control_id for {sid} not found in sample_id column: {ctrl}", file=sys.stderr)
                ok = False
            elif ctrl not in controls:
                print(f"ERROR: control_id for {sid} is not marked as control: {ctrl}", file=sys.stderr)
                ok = False

    targets = sorted({r["target_id"] for r in treatments})
    print("DAP-Seq input check summary")
    print(f"  samples: {len(rows)}")
    print(f"  controls: {', '.join(sorted(controls))}")
    print(f"  targets: {', '.join(targets)}")
    for target in targets:
        reps = [r["sample_id"] for r in treatments if r["target_id"] == target]
        ctrls = sorted({r["control_id"] for r in treatments if r["target_id"] == target})
        print(f"  target {target}: treatment_bams={len(reps)} reps={','.join(reps)} control={','.join(ctrls)}")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

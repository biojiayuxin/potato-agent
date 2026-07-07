#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import ensure_export_dirs, load_config, read_tsv, table_path, write_tsv


REQUIRED_TABLES = [
    "networks.tsv",
    "genes.tsv",
    "network_genes.tsv",
    "modules.tsv",
    "network_gene_kme.tsv",
    "coexpression_edges_top.tsv.gz",
    "module_overlaps.tsv",
    "shared_coexpression_edges.tsv",
]


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _count_tsv_rows(path: Path) -> int:
    with _open_text(path) as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def _audit_inputs(config: dict) -> list[dict[str, str]]:
    base_dir = Path(config["base_dir"])
    rows = []
    for network_id in config["networks"]:
        expected = {
            "tom": base_dir / "05-tom" / f"{network_id}.TOM-block.1.RData",
            "blockwise": base_dir / "02-modules" / f"{network_id}.blockwiseModules.rds",
            "gene_modules": base_dir / "02-modules" / f"{network_id}.gene_modules.tsv",
            "kme": base_dir / "02-modules" / f"{network_id}.gene_module_membership_kME.tsv",
            "module_sizes": base_dir / "02-modules" / f"{network_id}.module_sizes.tsv",
            "genes_used": base_dir / "00-input_check" / f"{network_id}.genes_used.tsv",
        }
        for kind, path in expected.items():
            rows.append(
                {
                    "network_id": network_id,
                    "kind": kind,
                    "path": str(path),
                    "exists": "true" if path.is_file() else "false",
                }
            )
    return rows


def _duplicate_keys(path: Path, key_fields: list[str], *, limit: int = 10) -> list[str]:
    seen = set()
    duplicates = []
    with _open_text(path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            key = tuple(row.get(field, "") for field in key_fields)
            if key in seen:
                duplicates.append("|".join(key))
                if len(duplicates) >= limit:
                    return duplicates
            seen.add(key)
    return duplicates


def validate_exports(config_path: str | None = None) -> Path:
    config = load_config(config_path)
    ensure_export_dirs(config)
    log_dir = Path(config["output_dir"]) / "logs"
    audit_path = log_dir / "input_audit.tsv"
    write_tsv(audit_path, ["network_id", "kind", "path", "exists"], _audit_inputs(config))

    failures = []
    summary_rows = []
    for filename in REQUIRED_TABLES:
        path = table_path(config, filename)
        exists = path.is_file()
        row_count = _count_tsv_rows(path) if exists else 0
        summary_rows.append({"table": filename, "exists": exists, "rows": row_count})
        if not exists:
            failures.append(f"missing table: {path}")

    pk_checks = {
        "networks.tsv": ["network_id"],
        "genes.tsv": ["gene_id"],
        "network_genes.tsv": ["network_id", "gene_id"],
        "modules.tsv": ["network_id", "module"],
        "network_gene_kme.tsv": ["network_id", "gene_id", "module"],
        "coexpression_edges_top.tsv.gz": ["network_id", "gene_id", "neighbor_gene_id"],
        "module_overlaps.tsv": ["network_a", "module_a", "network_b", "module_b"],
        "shared_coexpression_edges.tsv": ["gene_a", "gene_b"],
    }
    for filename, fields in pk_checks.items():
        path = table_path(config, filename)
        if not path.is_file():
            continue
        duplicates = _duplicate_keys(path, fields)
        if duplicates:
            failures.append(f"{filename} duplicate keys: {', '.join(duplicates)}")

    edge_path = table_path(config, "coexpression_edges_top.tsv.gz")
    if edge_path.is_file():
        with _open_text(edge_path) as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for line_number, row in enumerate(reader, start=2):
                try:
                    rank = int(row.get("rank", ""))
                    tom = float(row.get("tom", ""))
                except ValueError:
                    failures.append(f"invalid edge rank/TOM at line {line_number}")
                    break
                if rank < 1 or tom < 0:
                    failures.append(f"invalid edge values at line {line_number}")
                    break

    summary_path = log_dir / "export_validation.tsv"
    write_tsv(summary_path, ["table", "exists", "rows"], summary_rows)
    if failures:
        failure_path = log_dir / "export_validation_failures.txt"
        failure_path.write_text("\n".join(failures) + "\n", encoding="utf-8")
        raise SystemExit(f"validation failed; see {failure_path}")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate WGCNA export inputs and tables.")
    parser.add_argument("--config", default=None, help="Path to WGCNA export config YAML.")
    args = parser.parse_args()
    output_path = validate_exports(args.config)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

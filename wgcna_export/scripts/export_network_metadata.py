#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import ensure_export_dirs, load_config, read_tsv, table_path, write_tsv


FIELDS = [
    "network_id",
    "sample_count",
    "input_genes_after_tpm_filter",
    "genes_used_for_wgcna",
    "soft_power",
    "network_type",
    "tom_type",
    "correlation_method",
    "min_module_size",
    "merge_cut_height",
]


def export_network_metadata(config_path: str | None = None) -> Path:
    config = load_config(config_path)
    ensure_export_dirs(config)
    summary_path = Path(config["base_dir"]) / "06-summary" / "all_networks.summary.tsv"
    if not summary_path.is_file():
        raise FileNotFoundError(f"missing summary table: {summary_path}")

    rows_by_network = {row["network"]: row for row in read_tsv(summary_path)}
    output_rows = []
    for network_id in config["networks"]:
        source = rows_by_network.get(network_id)
        if source is None:
            raise ValueError(f"{network_id!r} not found in {summary_path}")
        output_rows.append(
            {
                "network_id": network_id,
                "sample_count": source.get("samples", ""),
                "input_genes_after_tpm_filter": source.get("input_genes_after_TPM_filter", ""),
                "genes_used_for_wgcna": source.get("genes_used_for_WGCNA", ""),
                "soft_power": source.get("soft_power", ""),
                "network_type": config.get("network_type", "signed"),
                "tom_type": config.get("tom_type", "signed"),
                "correlation_method": config.get("correlation_method", "bicor"),
                "min_module_size": config.get("min_module_size", 30),
                "merge_cut_height": config.get("merge_cut_height", 0.25),
            }
        )

    output_path = table_path(config, "networks.tsv")
    write_tsv(output_path, FIELDS, output_rows)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export WGCNA network metadata TSV.")
    parser.add_argument("--config", default=None, help="Path to WGCNA export config YAML.")
    args = parser.parse_args()
    output_path = export_network_metadata(args.config)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

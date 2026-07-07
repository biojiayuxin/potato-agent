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

from _common import ensure_export_dirs, load_config, table_path, write_tsv


NETWORKS = ["leaf", "stem", "root", "reproductive", "tuberization"]
FIELDS = [
    "gene_a",
    "gene_b",
    "n_networks",
    "networks",
    "tom_leaf",
    "tom_stem",
    "tom_root",
    "tom_reproductive",
    "tom_tuberization",
]


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _array_literal(values: list[str]) -> str:
    return "{" + ",".join(value.replace('"', '\\"') for value in values) + "}"


def compute_shared_edges(config_path: str | None = None, *, min_networks: int = 2) -> Path:
    config = load_config(config_path)
    ensure_export_dirs(config)
    input_path = table_path(config, "coexpression_edges_top.tsv.gz")
    if not input_path.is_file():
        fallback = table_path(config, "coexpression_edges_top.tsv")
        if fallback.is_file():
            input_path = fallback
        else:
            raise FileNotFoundError(f"missing coexpression edge table: {input_path}")

    by_pair: dict[tuple[str, str], dict[str, float]] = {}
    with _open_text(input_path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            gene_id = row.get("gene_id", "")
            neighbor_gene_id = row.get("neighbor_gene_id", "")
            network_id = row.get("network_id", "")
            if not gene_id or not neighbor_gene_id or not network_id:
                continue
            gene_a, gene_b = sorted((gene_id, neighbor_gene_id))
            try:
                tom = float(row.get("tom", ""))
            except ValueError:
                continue
            network_toms = by_pair.setdefault((gene_a, gene_b), {})
            network_toms[network_id] = max(tom, network_toms.get(network_id, float("-inf")))

    rows = []
    for (gene_a, gene_b), network_toms in by_pair.items():
        networks = [network for network in NETWORKS if network in network_toms]
        if len(networks) < min_networks:
            continue
        rows.append(
            {
                "gene_a": gene_a,
                "gene_b": gene_b,
                "n_networks": len(networks),
                "networks": _array_literal(networks),
                "tom_leaf": network_toms.get("leaf", ""),
                "tom_stem": network_toms.get("stem", ""),
                "tom_root": network_toms.get("root", ""),
                "tom_reproductive": network_toms.get("reproductive", ""),
                "tom_tuberization": network_toms.get("tuberization", ""),
            }
        )
    rows.sort(key=lambda row: (-int(row["n_networks"]), row["gene_a"], row["gene_b"]))
    output_path = table_path(config, "shared_coexpression_edges.tsv")
    write_tsv(output_path, FIELDS, rows)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute WGCNA shared co-expression edges.")
    parser.add_argument("--config", default=None, help="Path to WGCNA export config YAML.")
    parser.add_argument("--min-networks", type=int, default=2)
    args = parser.parse_args()
    output_path = compute_shared_edges(args.config, min_networks=args.min_networks)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

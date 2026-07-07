#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
from itertools import combinations
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import ensure_export_dirs, load_config, read_tsv, table_path, write_tsv


FIELDS = [
    "network_a",
    "module_a",
    "network_b",
    "module_b",
    "overlap_genes",
    "size_a",
    "size_b",
    "jaccard",
    "overlap_ratio_a",
    "overlap_ratio_b",
    "p_value",
    "q_value",
]


def log_choose(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeom_sf(overlap: int, size_a: int, size_b: int, universe: int) -> float:
    upper = min(size_a, size_b)
    if overlap > upper:
        return 0.0
    denominator = log_choose(universe, size_b)
    terms = []
    for k in range(overlap, upper + 1):
        terms.append(log_choose(size_a, k) + log_choose(universe - size_a, size_b - k) - denominator)
    if not terms:
        return 1.0
    max_term = max(terms)
    return min(1.0, math.exp(max_term) * sum(math.exp(term - max_term) for term in terms))


def bh_fdr(p_values: list[float]) -> list[float]:
    n = len(p_values)
    ranked = sorted(enumerate(p_values), key=lambda item: item[1])
    q_values = [1.0] * n
    running = 1.0
    for rank, (index, p_value) in reversed(list(enumerate(ranked, start=1))):
        running = min(running, p_value * n / rank)
        q_values[index] = min(1.0, running)
    return q_values


def load_modules(base_dir: Path, network_id: str) -> dict[str, set[str]]:
    path = base_dir / "02-modules" / f"{network_id}.gene_modules.tsv"
    if not path.is_file():
        raise FileNotFoundError(f"missing module table: {path}")
    modules: dict[str, set[str]] = {}
    for row in read_tsv(path):
        gene_id = row.get("gene_id", "")
        module = row.get("module", "")
        if not gene_id or not module:
            continue
        modules.setdefault(module, set()).add(gene_id)
    return modules


def compute_module_overlaps(
    config_path: str | None = None,
    *,
    min_overlap: int = 5,
    max_q_value: float = 0.05,
) -> Path:
    config = load_config(config_path)
    ensure_export_dirs(config)
    base_dir = Path(config["base_dir"])
    module_sets = {
        network_id: load_modules(base_dir, network_id)
        for network_id in config["networks"]
    }
    universe_genes = set()
    for network_modules in module_sets.values():
        for genes in network_modules.values():
            universe_genes.update(genes)
    universe = len(universe_genes)
    if universe == 0:
        raise ValueError("module universe is empty")

    raw_rows = []
    p_values = []
    for network_a, network_b in combinations(config["networks"], 2):
        for module_a, genes_a in module_sets[network_a].items():
            for module_b, genes_b in module_sets[network_b].items():
                overlap = len(genes_a & genes_b)
                if overlap < min_overlap:
                    continue
                size_a = len(genes_a)
                size_b = len(genes_b)
                union = len(genes_a | genes_b)
                p_value = hypergeom_sf(overlap, size_a, size_b, universe)
                p_values.append(p_value)
                raw_rows.append(
                    {
                        "network_a": network_a,
                        "module_a": module_a,
                        "network_b": network_b,
                        "module_b": module_b,
                        "overlap_genes": overlap,
                        "size_a": size_a,
                        "size_b": size_b,
                        "jaccard": overlap / union if union else 0,
                        "overlap_ratio_a": overlap / size_a if size_a else 0,
                        "overlap_ratio_b": overlap / size_b if size_b else 0,
                        "p_value": p_value,
                        "q_value": 1.0,
                    }
                )

    for row, q_value in zip(raw_rows, bh_fdr(p_values), strict=False):
        row["q_value"] = q_value

    rows = [
        row for row in raw_rows
        if row["q_value"] <= max_q_value
    ]
    rows.sort(key=lambda row: (row["q_value"], -row["overlap_genes"], row["network_a"], row["module_a"]))
    output_path = table_path(config, "module_overlaps.tsv")
    write_tsv(output_path, FIELDS, rows)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute cross-network WGCNA module overlaps.")
    parser.add_argument("--config", default=None, help="Path to WGCNA export config YAML.")
    parser.add_argument("--min-overlap", type=int, default=5)
    parser.add_argument("--max-q-value", type=float, default=0.05)
    args = parser.parse_args()
    output_path = compute_module_overlaps(
        args.config,
        min_overlap=args.min_overlap,
        max_q_value=args.max_q_value,
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run potato GO enrichment from a raw ID file in one step.

Usage example:
    python3 GO_enrichment.py \
        --genome DMv8.2 \
        --input /path/to/input_ids.txt \
        --output /path/to/GO_enrichment.tsv

Input: one ID per line. IDs can be GeneID, representative transcript ID, or alternative transcript ID.
The script converts IDs to representative transcript IDs internally, then runs GO enrichment.
The agent must determine --genome before calling this script by checking the first 10 non-empty input IDs.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

DATA_ROOT = Path("/mnt/data/public_data/GO_KEGG_data")
OBO_FILE = DATA_ROOT / "shared_data" / "go-basic.obo"
GO_BACKGROUND_FILE_NAME = "GO.txt"
MAPPING_FILE_NAME = "GeneID_RepreID_AltID.tsv"

GENOME_DIRS = {
    "DMv8.2": ["DMv82"],
    "DMv8.1": ["DMv81"],
    "DMv6.1": ["DMv61", "DMv6.1", "DMv6_1"],
    "E4-63": ["E4-63", "E4_63", "St_E4-63"],
}

REQUIRED_PACKAGES = {
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "numpy": "numpy",
    "goatools": "goatools",
    "scipy": "scipy",
    "statsmodels": "statsmodels",
}

OUTPUT_COLUMNS = [
    "GO_ID", "Description", "Namespace", "FDR", "p_value", "Enrichment_Ratio",
    "query_count (k)", "query_total (n)", "background_count (K)", "background_total (N)", "genes",
]


def ensure_dependencies() -> None:
    """Check required Python packages; install missing packages with the running Python if needed."""
    missing = []
    for import_name, package_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        print("Missing Python packages: " + ", ".join(missing), file=sys.stderr)
        cmd = [sys.executable, "-m", "pip", "install", "--user", *missing]
        print("Installing with: " + " ".join(cmd), file=sys.stderr)
        subprocess.check_call(cmd)
        importlib.invalidate_caches()

    still_missing = []
    for import_name, package_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            still_missing.append(package_name)
    if still_missing:
        raise RuntimeError("Required Python packages still missing: " + ", ".join(still_missing))


def read_ids(id_file: Path) -> list[str]:
    if not id_file.is_file():
        raise FileNotFoundError(f"Input ID file not found: {id_file}")
    ids = []
    with id_file.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            query_id = line.strip()
            if query_id:
                ids.append(query_id)
    if not ids:
        raise ValueError(f"Input ID file has no non-empty IDs: {id_file}")
    return ids


def resolve_genome_dir(genome: str) -> Path:
    candidates = [DATA_ROOT / dirname for dirname in GENOME_DIRS[genome]]
    for path in candidates:
        if path.is_dir():
            return path
    raise FileNotFoundError(
        f"GO/KEGG data directory for {genome} not found. Expected one of: "
        + ", ".join(str(p) for p in candidates)
    )


def resolve_resources(genome: str) -> tuple[Path, Path, Path]:
    genome_dir = resolve_genome_dir(genome)
    mapping_file = genome_dir / MAPPING_FILE_NAME
    background_file = genome_dir / GO_BACKGROUND_FILE_NAME
    missing = [p for p in [mapping_file, background_file, OBO_FILE] if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing required GO enrichment data files:\n" + "\n".join(str(p) for p in missing)
        )
    return mapping_file, background_file, OBO_FILE


def load_id_mapping(mapping_file: Path) -> dict[str, str]:
    """Load GeneID/Repre TransID/Alt TransID as any_input_id -> representative transcript ID."""
    id_to_repre: dict[str, str] = {}
    required_cols = ["GeneID", "Repre TransID", "Alt TransID"]

    def add_mapping(query_id: str, repre_id: str, line_no: int) -> None:
        query_id = query_id.strip()
        repre_id = repre_id.strip()
        if not query_id:
            return
        previous = id_to_repre.get(query_id)
        if previous is not None and previous != repre_id:
            raise ValueError(
                f"Conflicting mapping for {query_id!r} at mapping line {line_no}: "
                f"{previous!r} vs {repre_id!r}"
            )
        id_to_repre[query_id] = repre_id

    with mapping_file.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"Mapping file is empty: {mapping_file}")
        missing = [col for col in required_cols if col not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"Mapping file lacks required columns: {', '.join(missing)}; found: {reader.fieldnames}"
            )
        for line_no, row in enumerate(reader, start=2):
            gene_id = (row.get("GeneID") or "").strip()
            repre_id = (row.get("Repre TransID") or "").strip()
            alt_ids = (row.get("Alt TransID") or "").strip()
            if not gene_id or not repre_id:
                raise ValueError(f"Empty GeneID or Repre TransID at mapping line {line_no}")
            add_mapping(gene_id, repre_id, line_no)
            add_mapping(repre_id, repre_id, line_no)
            if alt_ids:
                for alt_id in alt_ids.split(","):
                    add_mapping(alt_id, repre_id, line_no)
    return id_to_repre


def convert_to_representative_ids(input_ids: list[str], id_to_repre: dict[str, str]) -> list[str]:
    converted = []
    unmapped = []
    for idx, query_id in enumerate(input_ids, start=1):
        repre_id = id_to_repre.get(query_id)
        if repre_id is None:
            unmapped.append((idx, query_id))
        else:
            converted.append(repre_id)

    if unmapped:
        examples = "; ".join(f"line {line_no}: {query_id}" for line_no, query_id in unmapped[:10])
        raise ValueError(f"Unmapped IDs: {len(unmapped)}. Examples: {examples}")
    return converted


def write_representative_ids(converted_ids: list[str], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="") as handle:
        for repre_id in converted_ids:
            handle.write(repre_id + "\n")


def parse_go_background(background_file: Path):
    go2gene = defaultdict(set)
    gene2go = defaultdict(set)
    malformed = 0

    with background_file.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                malformed += 1
                continue
            gene_id, go_id = parts[0].strip(), parts[1].strip()
            if not gene_id or not go_id:
                malformed += 1
                continue
            gene2go[gene_id].add(go_id)
            go2gene[go_id].add(gene_id)

    if malformed:
        print(f"Warning: skipped malformed background lines: {malformed}", file=sys.stderr)
    return go2gene, gene2go


def create_empty_pdf(output_prefix: str, message: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    pdf_file = f"{output_prefix}.pdf"
    with PdfPages(pdf_file) as pdf:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=16, transform=ax.transAxes)
        ax.set_axis_off()
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def plot_go_enrichment(results_df, output_prefix: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    namespaces = ["biological_process", "molecular_function", "cellular_component"]
    namespace_names = {
        "biological_process": "Biological Process",
        "molecular_function": "Molecular Function",
        "cellular_component": "Cellular Component",
    }

    fig, axes = plt.subplots(3, 1, figsize=(9, 18))
    for idx, namespace in enumerate(namespaces):
        ax = axes[idx]
        subset = results_df[results_df["Namespace"] == namespace].copy().sort_values("FDR").head(10)
        if subset.empty:
            ax.text(0.5, 0.5, f"No {namespace_names[namespace]}\nresults", ha="center", va="center", transform=ax.transAxes)
            ax.set_title(namespace_names[namespace])
            ax.set_axis_off()
            continue

        subset = subset.iloc[::-1]
        y = np.arange(len(subset))
        x = subset["Enrichment_Ratio"].astype(float)
        sizes = subset["query_count (k)"].astype(float) * 25
        colors = -np.log10(subset["FDR"].astype(float).replace(0, np.nextafter(0, 1)))
        scatter = ax.scatter(x, y, s=sizes, c=colors, cmap="viridis", alpha=0.75, edgecolors="black", linewidth=0.5)
        ax.set_yticks(y)
        ax.set_yticklabels(subset["Description"], fontsize=8)
        ax.set_xlabel("Enrichment Ratio")
        ax.set_title(namespace_names[namespace])
        ax.grid(True, alpha=0.3)
        cbar = plt.colorbar(scatter, ax=ax)
        cbar.set_label("-log10(FDR)")

    plt.tight_layout()
    pdf_file = f"{output_prefix}.pdf"
    plt.savefig(pdf_file, dpi=300, bbox_inches="tight")
    plt.close(fig)


def run_go_enrichment(genome: str, input_file: Path, output_file: Path, p_value_cutoff: float) -> None:
    ensure_dependencies()

    import pandas as pd
    from goatools.obo_parser import GODag
    from scipy.stats import hypergeom
    from statsmodels.stats.multitest import multipletests

    input_ids = read_ids(input_file)
    mapping_file, background_file, obo_file = resolve_resources(genome)
    id_to_repre = load_id_mapping(mapping_file)
    representative_ids = convert_to_representative_ids(input_ids, id_to_repre)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_prefix = os.path.splitext(str(output_file))[0]
    repre_output = Path(f"{output_prefix}.repreTransID.txt")
    write_representative_ids(representative_ids, repre_output)

    print(f"Genome: {genome}")
    print(f"Mapping file: {mapping_file}")
    print(f"GO background: {background_file}")
    print(f"OBO file: {obo_file}")
    print(f"Input IDs: {len(input_ids)}")
    print(f"Representative transcript IDs: {len(representative_ids)}")
    print(f"Representative ID file: {repre_output}")

    print(f"--- Loading OBO file: {obo_file} ---")
    go_dag = GODag(str(obo_file))

    print(f"--- Parsing GO background: {background_file} ---")
    go2gene_bg, gene2go_bg = parse_go_background(background_file)
    background_total = len(gene2go_bg)
    if background_total == 0:
        raise ValueError(f"GO background has no annotated genes/transcripts: {background_file}")

    query_genes = {query_id for query_id in representative_ids if query_id in gene2go_bg}
    query_total = len(query_genes)
    if query_total == 0:
        raise ValueError("None of the representative transcript IDs were found in the GO background annotation file.")

    print(f"Unique representative IDs: {len(set(representative_ids))}")
    print(f"Total genes/transcripts in background (N): {background_total}")
    print(f"Query IDs with GO annotation (n): {query_total}")

    results = []
    p_values = []
    for go_id in sorted(go2gene_bg):
        if go_id not in go_dag:
            continue
        background_genes_for_go = go2gene_bg[go_id]
        background_count = len(background_genes_for_go)
        query_genes_for_go = query_genes.intersection(background_genes_for_go)
        query_count = len(query_genes_for_go)
        if query_count == 0:
            continue

        p_value = hypergeom.sf(query_count - 1, background_total, background_count, query_total)
        term_record = go_dag[go_id]
        results.append({
            "GO_ID": go_id,
            "Description": term_record.name,
            "Namespace": term_record.namespace,
            "p_value": p_value,
            "query_count (k)": query_count,
            "query_total (n)": query_total,
            "background_count (K)": background_count,
            "background_total (N)": background_total,
            "genes": ",".join(sorted(query_genes_for_go)),
        })
        p_values.append(p_value)

    if not results:
        empty_df = pd.DataFrame(columns=OUTPUT_COLUMNS)
        empty_df.to_csv(output_file, sep="\t", index=False)
        create_empty_pdf(output_prefix, "No GO enrichment results found")
        print(f"Output TSV: {output_file}")
        print(f"Output PDF: {output_prefix}.pdf")
        print("Result rows: 0")
        return

    _, fdr_values, _, _ = multipletests(p_values, alpha=p_value_cutoff, method="fdr_bh")
    results_df = pd.DataFrame(results)
    results_df["FDR"] = fdr_values
    results_df["Enrichment_Ratio"] = (
        (results_df["query_count (k)"] / results_df["query_total (n)"]) /
        (results_df["background_count (K)"] / results_df["background_total (N)"])
    )
    all_results = results_df[OUTPUT_COLUMNS].sort_values(["p_value", "GO_ID"])
    all_results.to_csv(output_file, sep="\t", index=False)

    significant_results = all_results[all_results["FDR"] < p_value_cutoff].copy()
    if significant_results.empty:
        create_empty_pdf(output_prefix, "No significant GO terms found")
    else:
        plot_go_enrichment(significant_results, output_prefix)

    print(f"Output TSV: {output_file}")
    print(f"Output PDF: {output_prefix}.pdf")
    print(f"Result rows: {len(all_results)}")
    print(f"Significant rows (FDR < {p_value_cutoff}): {len(significant_results)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run potato GO enrichment from raw IDs using public GO_KEGG_data resources.")
    parser.add_argument("--genome", required=True, choices=sorted(GENOME_DIRS), help="Genome version determined by the agent.")
    parser.add_argument("-i", "--input", required=True, type=Path, help="Input ID file, one ID per line; gene/transcript IDs are accepted.")
    parser.add_argument("-out", "--output", required=True, type=Path, help="Output GO enrichment TSV file.")
    parser.add_argument("-p", "--p_cutoff", type=float, default=0.05, help="FDR cutoff for significant terms [default: 0.05].")
    args = parser.parse_args()

    try:
        run_go_enrichment(args.genome, args.input, args.output, args.p_cutoff)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

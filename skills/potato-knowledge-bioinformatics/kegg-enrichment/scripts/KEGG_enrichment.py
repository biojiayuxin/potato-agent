#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run potato KEGG pathway enrichment from a raw ID file in one step.

Usage example:
    python3 KEGG_enrichment.py \
        --genome DMv8.2 \
        --input /path/to/input_ids.txt \
        --output /path/to/KEGG_enrichment.tsv

Input: one ID per line. IDs can be GeneID, representative transcript ID, or alternative transcript ID.
The script converts IDs to representative transcript IDs internally, then runs KEGG enrichment.
The agent must determine --genome before calling this script by checking the first 10 non-empty input IDs.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

DATA_ROOT = Path("/mnt/data/public_data/GO_KEGG_data")
TERM2GENE_FILE_NAME = "term2gene.txt"
TERM2NAME_FILE = DATA_ROOT / "shared_data" / "term2name.txt"
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
    "scipy": "scipy",
    "statsmodels": "statsmodels",
}

OUTPUT_COLUMNS = [
    "Pathway_ID", "Description", "FDR", "p_value", "Enrichment_Ratio",
    "query_count (k)", "query_total (n)", "background_count (K)", "background_total (N)",
    "GeneRatio", "BgRatio", "genes",
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
    ids: list[str] = []
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


def resolve_resources(genome: str) -> tuple[Path, Path, Path | None]:
    genome_dir = resolve_genome_dir(genome)
    term2gene_file = genome_dir / TERM2GENE_FILE_NAME
    mapping_file = genome_dir / MAPPING_FILE_NAME
    missing = [p for p in [term2gene_file, TERM2NAME_FILE] if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing required KEGG enrichment data files:\n" + "\n".join(str(p) for p in missing)
        )
    return term2gene_file, TERM2NAME_FILE, mapping_file if mapping_file.is_file() else None


def parse_term2_files(term2gene_file: Path, term2name_file: Path):
    """Parse TERM2GENE and TERM2NAME files."""
    pathway2gene: dict[str, set[str]] = defaultdict(set)
    pathway2desc: dict[str, str] = {}
    all_background_genes: set[str] = set()
    malformed_term2gene = 0
    malformed_term2name = 0

    print(f"--- Parsing TERM2GENE: {term2gene_file} ---")
    with term2gene_file.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                malformed_term2gene += 1
                continue
            term = parts[0].strip()
            gene = parts[1].strip()
            if not term or not gene:
                malformed_term2gene += 1
                continue
            pathway2gene[term].add(gene)
            all_background_genes.add(gene)

    print(f"--- Parsing TERM2NAME: {term2name_file} ---")
    with term2name_file.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                malformed_term2name += 1
                continue
            term = parts[0].strip()
            name = parts[1].strip()
            if not term:
                malformed_term2name += 1
                continue
            pathway2desc[term] = name or "N/A"

    if malformed_term2gene:
        print(f"Warning: skipped malformed TERM2GENE lines: {malformed_term2gene}", file=sys.stderr)
    if malformed_term2name:
        print(f"Warning: skipped malformed TERM2NAME lines: {malformed_term2name}", file=sys.stderr)
    if not all_background_genes:
        raise ValueError(f"No background genes/transcripts parsed from: {term2gene_file}")

    print(
        f"--- Parsed {len(pathway2gene)} pathways and "
        f"{len(all_background_genes)} unique background genes/transcripts. ---"
    )
    return pathway2gene, pathway2desc, all_background_genes


def strip_transcript_suffix(query_id: str) -> str:
    """Remove a final numeric transcript suffix such as .1, .2, .4 when present."""
    return re.sub(r"\.\d+$", "", query_id)


def load_explicit_id_mapping(mapping_file: Path) -> dict[str, str]:
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


def build_background_unique_mapping(background_genes: set[str]) -> tuple[dict[str, str], set[str]]:
    """
    Build a conservative gene-base -> representative transcript mapping from TERM2GENE IDs.

    This supports genomes whose explicit GeneID_RepreID_AltID.tsv is not currently present.
    A base ID is accepted only if it maps to exactly one transcript ID in the KEGG background.
    """
    base_to_transcripts: dict[str, set[str]] = defaultdict(set)
    for transcript_id in background_genes:
        base_id = strip_transcript_suffix(transcript_id)
        if base_id and base_id != transcript_id:
            base_to_transcripts[base_id].add(transcript_id)

    unique = {base: next(iter(ids)) for base, ids in base_to_transcripts.items() if len(ids) == 1}
    ambiguous = {base for base, ids in base_to_transcripts.items() if len(ids) > 1}
    return unique, ambiguous


def convert_to_representative_ids(
    input_ids: list[str],
    background_genes: set[str],
    mapping_file: Path | None,
) -> list[str]:
    """Convert raw IDs to representative transcript IDs used by the KEGG background."""
    explicit_mapping: dict[str, str] = {}
    if mapping_file is not None:
        print(f"--- Loading ID mapping: {mapping_file} ---")
        explicit_mapping = load_explicit_id_mapping(mapping_file)
    else:
        print("--- No GeneID_RepreID_AltID.tsv found; using exact background IDs and unique suffix-stripped mapping. ---")

    unique_base_mapping, ambiguous_bases = build_background_unique_mapping(background_genes)

    converted: list[str] = []
    unmapped: list[tuple[int, str]] = []
    ambiguous: list[tuple[int, str, str]] = []
    source_counts = {"explicit": 0, "exact-background": 0, "unique-background-base": 0, "unique-background-base-from-transcript": 0}

    for idx, query_id in enumerate(input_ids, start=1):
        if query_id in explicit_mapping:
            converted.append(explicit_mapping[query_id])
            source_counts["explicit"] += 1
            continue
        if query_id in background_genes:
            converted.append(query_id)
            source_counts["exact-background"] += 1
            continue
        if query_id in unique_base_mapping:
            converted.append(unique_base_mapping[query_id])
            source_counts["unique-background-base"] += 1
            continue

        base_id = strip_transcript_suffix(query_id)
        if base_id in unique_base_mapping:
            converted.append(unique_base_mapping[base_id])
            source_counts["unique-background-base-from-transcript"] += 1
            continue
        if query_id in ambiguous_bases or base_id in ambiguous_bases:
            ambiguous.append((idx, query_id, base_id))
            continue
        unmapped.append((idx, query_id))

    if ambiguous or unmapped:
        messages = []
        if ambiguous:
            examples = "; ".join(
                f"line {line_no}: {query_id} (base {base_id})" for line_no, query_id, base_id in ambiguous[:10]
            )
            messages.append(
                f"Ambiguous IDs: {len(ambiguous)}. Provide representative transcript IDs or a mapping file. Examples: {examples}"
            )
        if unmapped:
            examples = "; ".join(f"line {line_no}: {query_id}" for line_no, query_id in unmapped[:10])
            messages.append(f"Unmapped IDs: {len(unmapped)}. Examples: {examples}")
        raise ValueError("; ".join(messages))

    print("ID conversion source counts: " + ", ".join(f"{k}={v}" for k, v in source_counts.items() if v))
    return converted


def write_representative_ids(converted_ids: list[str], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="") as handle:
        for repre_id in converted_ids:
            handle.write(repre_id + "\n")


def create_empty_pdf(output_prefix: str, message: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    pdf_file = Path(f"{output_prefix}.pdf")
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(pdf_file) as pdf:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=16, transform=ax.transAxes)
        ax.set_axis_off()
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def plot_kegg_enrichment(results_df, output_prefix: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    subset = results_df.copy().sort_values("FDR").head(10)
    if subset.empty:
        create_empty_pdf(output_prefix, "No significant KEGG pathways found")
        return

    subset = subset.iloc[::-1]
    y = np.arange(len(subset))
    x = subset["Enrichment_Ratio"].astype(float)
    counts = subset["query_count (k)"].astype(float)
    if counts.max() == counts.min():
        sizes = [220] * len(counts)
    else:
        sizes = 80 + (320 - 80) * (counts - counts.min()) / (counts.max() - counts.min())
    colors = -np.log10(subset["FDR"].astype(float).replace(0, np.nextafter(0, 1)))

    fig, ax = plt.subplots(1, 1, figsize=(9, max(5, 0.42 * len(subset) + 2)))
    scatter = ax.scatter(x, y, s=sizes, c=colors, cmap="viridis", alpha=0.75, edgecolors="black", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(subset["Description"], fontsize=8)
    ax.set_xlabel("Enrichment Ratio")
    ax.set_title("KEGG Pathway Enrichment")
    ax.grid(True, alpha=0.3)
    cbar = plt.colorbar(scatter, ax=ax)
    cbar.set_label("-log10(FDR)")
    fig.text(
        0.02, 0.98, "Top 10 significant KEGG pathways by FDR are plotted",
        verticalalignment="top", bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.45),
    )
    plt.tight_layout(pad=3.0)
    pdf_file = Path(f"{output_prefix}.pdf")
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(pdf_file, dpi=300, bbox_inches="tight")
    plt.close(fig)


def run_kegg_enrichment(genome: str, input_file: Path, output_file: Path, p_value_cutoff: float) -> None:
    ensure_dependencies()

    import pandas as pd
    from scipy.stats import fisher_exact
    from statsmodels.stats.multitest import multipletests

    input_ids = read_ids(input_file)
    term2gene_file, term2name_file, mapping_file = resolve_resources(genome)
    pathway2gene, pathway2desc, all_background_genes = parse_term2_files(term2gene_file, term2name_file)
    representative_ids = convert_to_representative_ids(input_ids, all_background_genes, mapping_file)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_prefix = os.path.splitext(str(output_file))[0]
    repre_output = Path(f"{output_prefix}.repreTransID.txt")
    write_representative_ids(representative_ids, repre_output)

    background_total = len(all_background_genes)
    query_genes = {query_id for query_id in representative_ids if query_id in all_background_genes}
    query_total = len(query_genes)
    if query_total == 0:
        raise ValueError("None of the representative transcript IDs were found in the KEGG background annotation file.")

    print(f"Genome: {genome}")
    print(f"TERM2GENE: {term2gene_file}")
    print(f"TERM2NAME: {term2name_file}")
    print(f"ID mapping: {mapping_file if mapping_file is not None else 'not available; fallback mapping used'}")
    print(f"Input IDs: {len(input_ids)}")
    print(f"Representative transcript IDs: {len(representative_ids)}")
    print(f"Representative ID file: {repre_output}")
    print(f"Unique representative IDs: {len(set(representative_ids))}")
    print(f"Total genes/transcripts in background (N): {background_total}")
    print(f"Query IDs with KEGG annotation (n): {query_total}")

    results = []
    p_values = []
    for pathway_id in sorted(pathway2gene):
        background_genes_for_pathway = pathway2gene[pathway_id]
        background_count = len(background_genes_for_pathway)
        query_genes_for_pathway = query_genes.intersection(background_genes_for_pathway)
        query_count = len(query_genes_for_pathway)
        if query_count == 0:
            continue

        table = [
            [query_count, query_total - query_count],
            [background_count - query_count, background_total - background_count - (query_total - query_count)],
        ]
        _, p_value = fisher_exact(table, alternative="greater")

        enrichment_ratio = (
            (query_count / query_total) / (background_count / background_total)
            if query_total and background_count and background_total else 0
        )
        results.append({
            "Pathway_ID": pathway_id,
            "Description": pathway2desc.get(pathway_id, "N/A"),
            "p_value": p_value,
            "query_count (k)": query_count,
            "query_total (n)": query_total,
            "background_count (K)": background_count,
            "background_total (N)": background_total,
            "GeneRatio": f"{query_count}/{query_total}",
            "BgRatio": f"{background_count}/{background_total}",
            "genes": ",".join(sorted(query_genes_for_pathway)),
            "Enrichment_Ratio": enrichment_ratio,
        })
        p_values.append(p_value)

    if not results:
        empty_df = pd.DataFrame(columns=OUTPUT_COLUMNS)
        empty_df.to_csv(output_file, sep="\t", index=False)
        create_empty_pdf(output_prefix, "No KEGG enrichment results found")
        print(f"Output TSV: {output_file}")
        print(f"Output PDF: {output_prefix}.pdf")
        print("Result rows: 0")
        return

    _, fdr_values, _, _ = multipletests(p_values, alpha=p_value_cutoff, method="fdr_bh")
    results_df = pd.DataFrame(results)
    results_df["FDR"] = fdr_values
    all_results = results_df[OUTPUT_COLUMNS].sort_values(["p_value", "Pathway_ID"])
    all_results.to_csv(output_file, sep="\t", index=False)

    significant_results = all_results[all_results["FDR"] < p_value_cutoff].copy()
    if significant_results.empty:
        create_empty_pdf(output_prefix, "No significant KEGG pathways found")
    else:
        plot_kegg_enrichment(significant_results, output_prefix)

    print(f"Output TSV: {output_file}")
    print(f"Output PDF: {output_prefix}.pdf")
    print(f"Result rows: {len(all_results)}")
    print(f"Significant rows (FDR < {p_value_cutoff}): {len(significant_results)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run potato KEGG enrichment from raw IDs using public GO_KEGG_data resources.")
    parser.add_argument("--genome", required=True, choices=sorted(GENOME_DIRS), help="Genome version determined by the agent.")
    parser.add_argument("-i", "--input", required=True, type=Path, help="Input ID file, one ID per line; gene/transcript IDs are accepted.")
    parser.add_argument("-out", "--output", required=True, type=Path, help="Output KEGG enrichment TSV file.")
    parser.add_argument("-p", "--p_cutoff", type=float, default=0.05, help="FDR cutoff for significant pathways [default: 0.05].")
    args = parser.parse_args()

    try:
        run_kegg_enrichment(args.genome, args.input, args.output, args.p_cutoff)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

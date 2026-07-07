#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import clean_float, ensure_export_dirs, load_config, read_tsv, table_path, truth, write_tsv


GENES_FIELDS = ["gene_id", "gene_name", "chromosome", "start_pos", "end_pos", "annotation"]
NETWORK_GENES_FIELDS = [
    "network_id",
    "gene_id",
    "module",
    "variance_log2tpm",
    "kme_own_module",
    "is_grey",
]
MODULE_FIELDS = ["network_id", "module", "module_size", "is_grey"]
KME_FIELDS = ["network_id", "gene_id", "module", "kme"]


def _required_file(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"missing required input: {path}")
    return path


def _index_by_gene(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["gene_id"]: row for row in rows if row.get("gene_id")}


def _variance_column(row: dict[str, str]) -> str:
    for key in ("variance_log2TPM", "variance_log2tpm"):
        if key in row:
            return row.get(key, "")
    return ""


def export_gene_module_tables(config_path: str | None = None) -> list[Path]:
    config = load_config(config_path)
    ensure_export_dirs(config)
    base_dir = Path(config["base_dir"])
    genes: dict[str, dict[str, str]] = {}
    network_gene_rows: list[dict[str, str]] = []
    module_rows: list[dict[str, str]] = []
    kme_rows: list[dict[str, str]] = []

    for network_id in config["networks"]:
        module_path = _required_file(base_dir / "02-modules" / f"{network_id}.gene_modules.tsv")
        size_path = _required_file(base_dir / "02-modules" / f"{network_id}.module_sizes.tsv")
        genes_used_path = _required_file(base_dir / "00-input_check" / f"{network_id}.genes_used.tsv")
        kme_path = _required_file(base_dir / "02-modules" / f"{network_id}.gene_module_membership_kME.tsv")

        module_assignments = _index_by_gene(read_tsv(module_path))
        genes_used = _index_by_gene(read_tsv(genes_used_path))
        kme_by_gene = _index_by_gene(read_tsv(kme_path))

        for row in read_tsv(size_path):
            module = row.get("module", "")
            module_rows.append(
                {
                    "network_id": network_id,
                    "module": module,
                    "module_size": row.get("gene_count", ""),
                    "is_grey": truth(module == "grey"),
                }
            )

        for gene_id, assignment in module_assignments.items():
            gene_name = assignment.get("gene_name", "") or genes_used.get(gene_id, {}).get("gene_name", "")
            genes.setdefault(
                gene_id,
                {
                    "gene_id": gene_id,
                    "gene_name": gene_name,
                    "chromosome": "",
                    "start_pos": "",
                    "end_pos": "",
                    "annotation": "",
                },
            )
            if gene_name and not genes[gene_id].get("gene_name"):
                genes[gene_id]["gene_name"] = gene_name

            module = assignment.get("module", "")
            kme_record = kme_by_gene.get(gene_id, {})
            kme_own = clean_float(kme_record.get(f"kME_{module}"))
            network_gene_rows.append(
                {
                    "network_id": network_id,
                    "gene_id": gene_id,
                    "module": module,
                    "variance_log2tpm": clean_float(_variance_column(genes_used.get(gene_id, {}))),
                    "kme_own_module": kme_own,
                    "is_grey": truth(module == "grey"),
                }
            )

            for column, value in kme_record.items():
                if not column.startswith("kME_"):
                    continue
                kme_rows.append(
                    {
                        "network_id": network_id,
                        "gene_id": gene_id,
                        "module": column.removeprefix("kME_"),
                        "kme": clean_float(value),
                    }
                )

    outputs = [
        table_path(config, "genes.tsv"),
        table_path(config, "network_genes.tsv"),
        table_path(config, "modules.tsv"),
        table_path(config, "network_gene_kme.tsv"),
    ]
    write_tsv(outputs[0], GENES_FIELDS, (genes[key] for key in sorted(genes)))
    write_tsv(outputs[1], NETWORK_GENES_FIELDS, network_gene_rows)
    write_tsv(outputs[2], MODULE_FIELDS, module_rows)
    write_tsv(outputs[3], KME_FIELDS, kme_rows)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Export WGCNA gene/module/kME TSV tables.")
    parser.add_argument("--config", default=None, help="Path to WGCNA export config YAML.")
    args = parser.parse_args()
    for output_path in export_gene_module_tables(args.config):
        print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

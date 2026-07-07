#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import load_config, table_path

try:
    import psycopg
except ImportError:  # pragma: no cover - depends on deployment environment
    psycopg = None


REPO_EXPORT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = REPO_EXPORT_ROOT / "schema.sql"

LOAD_ORDER = [
    (
        "networks",
        "networks.tsv",
        [
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
        ],
    ),
    (
        "genes",
        "genes.tsv",
        ["gene_id", "gene_name", "chromosome", "start_pos", "end_pos", "annotation"],
    ),
    (
        "modules",
        "modules.tsv",
        ["network_id", "module", "module_size", "is_grey"],
    ),
    (
        "network_genes",
        "network_genes.tsv",
        ["network_id", "gene_id", "module", "variance_log2tpm", "kme_own_module", "is_grey"],
    ),
    (
        "network_gene_kme",
        "network_gene_kme.tsv",
        ["network_id", "gene_id", "module", "kme"],
    ),
    (
        "coexpression_edges_top",
        "coexpression_edges_top.tsv.gz",
        [
            "network_id",
            "gene_id",
            "neighbor_gene_id",
            "tom",
            "tom_percentile",
            "rank",
            "same_module",
            "gene_module",
            "neighbor_module",
        ],
    ),
    (
        "module_overlaps",
        "module_overlaps.tsv",
        [
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
        ],
    ),
    (
        "shared_coexpression_edges",
        "shared_coexpression_edges.tsv",
        [
            "gene_a",
            "gene_b",
            "n_networks",
            "networks",
            "tom_leaf",
            "tom_stem",
            "tom_root",
            "tom_reproductive",
            "tom_tuberization",
        ],
    ),
]


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _copy_table(conn, table: str, filename: str, columns: list[str], path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"missing export table for {table}: {path}")
    column_sql = ", ".join(columns)
    copy_sql = (
        f"COPY {table} ({column_sql}) "
        "FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER E'\\t', NULL '')"
    )
    with conn.cursor() as cur:
        with cur.copy(copy_sql) as copy:
            with _open_text(path) as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    copy.write(chunk)
    print(f"loaded {filename} -> {table}")


def load_to_postgresql(
    *,
    config_path: str | None,
    database_url: str | None,
    schema_path: str | None,
    truncate: bool,
) -> None:
    if psycopg is None:
        raise SystemExit("psycopg is not installed; install interface requirements first")
    config = load_config(config_path)
    selected_database_url = database_url or os.getenv("WGCNA_DATABASE_URL", "").strip()
    if not selected_database_url:
        raise SystemExit("missing database URL; set WGCNA_DATABASE_URL or pass --database-url")
    selected_schema_path = Path(schema_path or DEFAULT_SCHEMA_PATH).resolve()
    if not selected_schema_path.is_file():
        raise FileNotFoundError(f"schema not found: {selected_schema_path}")

    with psycopg.connect(selected_database_url) as conn:
        conn.execute(selected_schema_path.read_text(encoding="utf-8"))
        if truncate:
            tables = ", ".join(table for table, _, _ in reversed(LOAD_ORDER))
            conn.execute(f"truncate table {tables} cascade")
        for table, filename, columns in LOAD_ORDER:
            _copy_table(conn, table, filename, columns, table_path(config, filename))
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Load WGCNA export TSVs into PostgreSQL.")
    parser.add_argument("--config", default=None, help="Path to WGCNA export config YAML.")
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection URL.")
    parser.add_argument("--schema", default=None, help="Path to schema.sql.")
    parser.add_argument("--truncate", action="store_true", help="Truncate WGCNA tables before loading.")
    args = parser.parse_args()
    load_to_postgresql(
        config_path=args.config,
        database_url=args.database_url,
        schema_path=args.schema,
        truncate=args.truncate,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

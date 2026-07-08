from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_ROOT = Path("/mnt/data/public_data/Expression_atlas/DMv8.2")
DEFAULT_OUTPUT_DB = Path("/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite")
SAMPLE_LIST_NAME = "sample_tissue_list.tsv"
TPM_MATRIX_NAME = "transcript_tpm_matrix_merged.tsv"
DATASET_ID = "DMv8.2"
SCOPES = ("tissue", "sample_name", "sample_tissue")
DEFAULT_EXCLUDED_SAMPLE_NAMES = ("PG0003", "PG0009", "PG0019")


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _round_float(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return round(float(value), 6)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def read_sample_rows(
    path: Path,
    *,
    excluded_sample_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    if not path.is_file():
        raise FileNotFoundError(f"sample list not found: {path}")

    rows: list[dict[str, Any]] = []
    excluded_counts: dict[str, int] = defaultdict(int)
    source_count = 0
    excluded = excluded_sample_names or set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"sample_column", "sample_name", "tissue"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"{path} must contain columns: {', '.join(sorted(required))}")
        for index, row in enumerate(reader):
            sample_column = _clean(row.get("sample_column"))
            sample_name = _clean(row.get("sample_name"))
            tissue = _clean(row.get("tissue"))
            if not sample_column or not sample_name or not tissue:
                raise ValueError(f"invalid sample metadata at row {index + 2}")
            source_count += 1
            if sample_name in excluded:
                excluded_counts[sample_name] += 1
                continue
            rows.append(
                {
                    "sample_idx": len(rows),
                    "sample_column": sample_column,
                    "sample_name": sample_name,
                    "tissue": tissue,
                }
            )

    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        sample_column = row["sample_column"]
        if sample_column in seen:
            duplicates.add(sample_column)
        seen.add(sample_column)
    if duplicates:
        raise ValueError(f"duplicate sample columns: {', '.join(sorted(duplicates)[:5])}")

    return rows, dict(excluded_counts), source_count


def assign_sample_orders(sample_rows: list[dict[str, Any]]) -> None:
    tissue_order: dict[str, int] = {}
    sample_name_order: dict[str, int] = {}
    for row in sample_rows:
        tissue = row["tissue"]
        sample_name = row["sample_name"]
        tissue_order.setdefault(tissue, len(tissue_order))
        sample_name_order.setdefault(sample_name, len(sample_name_order))
        row["tissue_order"] = tissue_order[tissue]
        row["sample_name_order"] = sample_name_order[sample_name]


def build_group_rows(sample_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups_by_scope: dict[str, list[dict[str, Any]]] = {}
    group_maps: dict[str, dict[str, dict[str, Any]]] = {scope: {} for scope in SCOPES}

    for row in sample_rows:
        definitions = {
            "tissue": {
                "group_id": row["tissue"],
                "label": row["tissue"],
                "sample_name": "",
                "tissue": row["tissue"],
                "sort_key": (row["tissue_order"],),
            },
            "sample_name": {
                "group_id": row["sample_name"],
                "label": row["sample_name"],
                "sample_name": row["sample_name"],
                "tissue": "",
                "sort_key": (row["sample_name_order"],),
            },
            "sample_tissue": {
                "group_id": f"{row['sample_name']}|{row['tissue']}",
                "label": f"{row['sample_name']} - {row['tissue']}",
                "sample_name": row["sample_name"],
                "tissue": row["tissue"],
                "sort_key": (row["sample_name_order"], row["tissue_order"]),
            },
        }
        for scope, definition in definitions.items():
            group = group_maps[scope].setdefault(
                definition["group_id"],
                {
                    "scope": scope,
                    "group_id": definition["group_id"],
                    "label": definition["label"],
                    "sample_name": definition["sample_name"],
                    "tissue": definition["tissue"],
                    "sort_key": definition["sort_key"],
                    "sample_indices": [],
                },
            )
            group["sample_indices"].append(int(row["sample_idx"]))

    for scope, groups in group_maps.items():
        ordered = sorted(groups.values(), key=lambda item: item["sort_key"])
        for index, group in enumerate(ordered):
            group["group_idx"] = index
            group["n_samples"] = len(group["sample_indices"])
        groups_by_scope[scope] = ordered
    return groups_by_scope


def open_output_database(output_db: Path) -> tuple[sqlite3.Connection, Path]:
    output_db.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_db.name}.",
        suffix=".tmp",
        dir=output_db.parent,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    conn = sqlite3.connect(temp_path)
    return conn, temp_path


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        pragma journal_mode = off;
        pragma synchronous = off;
        pragma temp_store = memory;

        create table metadata(
          key text primary key,
          value text not null
        );

        create table samples(
          sample_idx integer primary key,
          sample_column text unique not null,
          sample_name text not null,
          tissue text not null,
          sample_order integer not null,
          tissue_order integer not null
        );

        create table genes(
          gene_id text primary key,
          transcript_id text not null,
          gene_name text not null default '',
          transcript_count integer not null default 1
        );

        create table expression_vectors(
          gene_id text primary key references genes(gene_id),
          tpm_json text not null,
          max_tpm real not null,
          mean_tpm real not null,
          detected_samples integer not null
        );

        create table groups(
          scope text not null,
          group_idx integer not null,
          group_id text not null,
          label text not null,
          sample_name text not null default '',
          tissue text not null default '',
          n_samples integer not null,
          sample_indices_json text not null,
          primary key(scope, group_idx)
        );

        create unique index groups_scope_group_id_idx on groups(scope, group_id);

        create table group_expression_vectors(
          gene_id text not null references genes(gene_id),
          scope text not null,
          mean_tpm_json text not null,
          sd_tpm_json text not null,
          n_json text not null,
          primary key(gene_id, scope)
        );
        """
    )


def insert_metadata(
    conn: sqlite3.Connection,
    *,
    source_root: Path,
    sample_count: int,
    source_sample_count: int,
    excluded_sample_names: set[str],
    excluded_counts: dict[str, int],
    group_rows: dict[str, list[dict[str, Any]]],
) -> None:
    metadata = {
        "dataset_id": DATASET_ID,
        "source_root": str(source_root),
        "source_sample_count": str(source_sample_count),
        "sample_count": str(sample_count),
        "excluded_sample_names": _json(sorted(excluded_sample_names)),
        "excluded_sample_counts": _json(excluded_counts),
        "excluded_sample_count": str(sum(excluded_counts.values())),
        "scope_order": _json(list(SCOPES)),
        "tissue_count": str(len(group_rows["tissue"])),
        "sample_name_count": str(len(group_rows["sample_name"])),
        "sample_tissue_count": str(len(group_rows["sample_tissue"])),
    }
    conn.executemany(
        "insert into metadata(key, value) values (?, ?)",
        sorted(metadata.items()),
    )


def insert_samples(conn: sqlite3.Connection, sample_rows: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        insert into samples(
          sample_idx,
          sample_column,
          sample_name,
          tissue,
          sample_order,
          tissue_order
        ) values (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["sample_idx"],
                row["sample_column"],
                row["sample_name"],
                row["tissue"],
                row["sample_name_order"],
                row["tissue_order"],
            )
            for row in sample_rows
        ],
    )


def insert_groups(conn: sqlite3.Connection, group_rows: dict[str, list[dict[str, Any]]]) -> None:
    rows = []
    for scope, groups in group_rows.items():
        for group in groups:
            rows.append(
                (
                    scope,
                    group["group_idx"],
                    group["group_id"],
                    group["label"],
                    group["sample_name"],
                    group["tissue"],
                    group["n_samples"],
                    _json(group["sample_indices"]),
                )
            )
    conn.executemany(
        """
        insert into groups(
          scope,
          group_idx,
          group_id,
          label,
          sample_name,
          tissue,
          n_samples,
          sample_indices_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def validate_matrix_header(matrix_path: Path, sample_rows: list[dict[str, Any]]) -> tuple[list[str], list[int]]:
    with matrix_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"empty matrix file: {matrix_path}") from exc

    if len(header) < 4:
        raise ValueError("matrix must contain transcript_id, gene_id, gene_name, and samples")
    sample_columns = [_clean(value) for value in header[3:]]
    header_by_sample = {sample_column: index + 3 for index, sample_column in enumerate(sample_columns)}
    if len(header_by_sample) != len(sample_columns):
        raise ValueError("matrix sample columns contain duplicates")
    missing = [
        row["sample_column"]
        for row in sample_rows
        if row["sample_column"] not in header_by_sample
    ]
    if missing:
        raise ValueError(
            "matrix sample columns do not contain retained sample metadata; "
            f"missing={missing[:5]}"
        )
    return header, [header_by_sample[row["sample_column"]] for row in sample_rows]


def group_stats(
    values: list[float],
    groups_by_scope: dict[str, list[dict[str, Any]]],
) -> dict[str, tuple[list[float], list[float], list[int]]]:
    stats: dict[str, tuple[list[float], list[float], list[int]]] = {}
    for scope, groups in groups_by_scope.items():
        means: list[float] = []
        sds: list[float] = []
        counts: list[int] = []
        for group in groups:
            group_values = [values[index] for index in group["sample_indices"]]
            means.append(_round_float(_mean(group_values)))
            sds.append(_round_float(_stdev(group_values)))
            counts.append(len(group_values))
        stats[scope] = (means, sds, counts)
    return stats


def ingest_matrix(
    conn: sqlite3.Connection,
    *,
    matrix_path: Path,
    sample_rows: list[dict[str, Any]],
    groups_by_scope: dict[str, list[dict[str, Any]]],
    commit_every: int = 1000,
) -> int:
    _, value_column_indices = validate_matrix_header(matrix_path, sample_rows)
    gene_counts: dict[str, int] = defaultdict(int)
    transcript_by_gene: dict[str, str] = {}
    gene_name_by_gene: dict[str, str] = {}
    values_by_gene: dict[str, list[float]] = {}
    expression_rows = []
    group_rows = []

    with matrix_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader)
        expected_width = len(header)
        for line_number, row in enumerate(reader, start=2):
            if len(row) != expected_width:
                raise ValueError(
                    f"matrix row {line_number} has {len(row)} columns; expected {expected_width}"
                )
            transcript_id = _clean(row[0])
            gene_id = _clean(row[1])
            gene_name = _clean(row[2])
            if not transcript_id or not gene_id:
                raise ValueError(f"matrix row {line_number} is missing transcript_id or gene_id")
            try:
                values = [
                    _round_float(float(row[index] or 0.0))
                    for index in value_column_indices
                ]
            except ValueError as exc:
                raise ValueError(f"invalid TPM value at matrix row {line_number}") from exc

            gene_counts[gene_id] += 1
            if gene_id not in transcript_by_gene:
                transcript_by_gene[gene_id] = transcript_id
            elif transcript_id not in transcript_by_gene[gene_id].split(","):
                transcript_by_gene[gene_id] = f"{transcript_by_gene[gene_id]},{transcript_id}"
            if gene_name:
                gene_name_by_gene.setdefault(gene_id, gene_name)

            existing = values_by_gene.get(gene_id)
            if existing is None:
                values_by_gene[gene_id] = values
            else:
                for index, value in enumerate(values):
                    existing[index] = _round_float(existing[index] + value)

    row_count = 0
    for gene_id, values in sorted(values_by_gene.items()):
        expression_rows.append(
            (
                gene_id,
                _json(values),
                max(values) if values else 0.0,
                _round_float(_mean(values)),
                sum(1 for value in values if value > 0),
            )
        )
        for scope, (means, sds, counts) in group_stats(values, groups_by_scope).items():
            group_rows.append((gene_id, scope, _json(means), _json(sds), _json(counts)))

        row_count += 1
        if row_count % commit_every == 0:
            conn.executemany(
                """
                insert into expression_vectors(
                  gene_id,
                  tpm_json,
                  max_tpm,
                  mean_tpm,
                  detected_samples
                ) values (?, ?, ?, ?, ?)
                """,
                expression_rows,
            )
            conn.executemany(
                """
                insert into group_expression_vectors(
                  gene_id,
                  scope,
                  mean_tpm_json,
                  sd_tpm_json,
                  n_json
                ) values (?, ?, ?, ?, ?)
                """,
                group_rows,
            )
            expression_rows.clear()
            group_rows.clear()
            conn.commit()

    if expression_rows:
        conn.executemany(
            """
            insert into expression_vectors(
              gene_id,
              tpm_json,
              max_tpm,
              mean_tpm,
              detected_samples
            ) values (?, ?, ?, ?, ?)
            """,
            expression_rows,
        )
    if group_rows:
        conn.executemany(
            """
            insert into group_expression_vectors(
              gene_id,
              scope,
              mean_tpm_json,
              sd_tpm_json,
              n_json
            ) values (?, ?, ?, ?, ?)
            """,
            group_rows,
        )

    conn.executemany(
        """
        insert into genes(gene_id, transcript_id, gene_name, transcript_count)
        values (?, ?, ?, ?)
        """,
        [
            (
                gene_id,
                transcript_by_gene.get(gene_id, ""),
                gene_name_by_gene.get(gene_id, ""),
                count,
            )
            for gene_id, count in sorted(gene_counts.items())
        ],
    )
    conn.execute("insert or replace into metadata(key, value) values ('gene_count', ?)", (str(len(gene_counts)),))
    conn.commit()
    return len(gene_counts)


def finalize_database(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create index genes_gene_name_idx on genes(gene_name);
        create index expression_vectors_mean_tpm_idx on expression_vectors(mean_tpm);
        create index group_expression_vectors_scope_idx on group_expression_vectors(scope);
        analyze;
        """
    )
    conn.commit()


def build_database(source_root: Path, output_db: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    sample_path = source_root / SAMPLE_LIST_NAME
    matrix_path = source_root / TPM_MATRIX_NAME
    if not matrix_path.is_file():
        raise FileNotFoundError(f"TPM matrix not found: {matrix_path}")

    excluded_sample_names = set(DEFAULT_EXCLUDED_SAMPLE_NAMES)
    sample_rows, excluded_counts, source_sample_count = read_sample_rows(
        sample_path,
        excluded_sample_names=excluded_sample_names,
    )
    assign_sample_orders(sample_rows)
    group_rows = build_group_rows(sample_rows)
    conn, temp_path = open_output_database(output_db)
    try:
        create_schema(conn)
        insert_metadata(
            conn,
            source_root=source_root,
            sample_count=len(sample_rows),
            source_sample_count=source_sample_count,
            excluded_sample_names=excluded_sample_names,
            excluded_counts=excluded_counts,
            group_rows=group_rows,
        )
        insert_samples(conn, sample_rows)
        insert_groups(conn, group_rows)
        gene_count = ingest_matrix(
            conn,
            matrix_path=matrix_path,
            sample_rows=sample_rows,
            groups_by_scope=group_rows,
        )
        finalize_database(conn)
        conn.close()
        temp_path.replace(output_db)
    except Exception:
        conn.close()
        temp_path.unlink(missing_ok=True)
        raise

    return {
        "dataset_id": DATASET_ID,
        "output_db": str(output_db),
        "gene_count": gene_count,
        "sample_count": len(sample_rows),
        "source_sample_count": source_sample_count,
        "excluded_sample_count": sum(excluded_counts.values()),
        "excluded_sample_counts": excluded_counts,
        "scopes": {scope: len(rows) for scope, rows in group_rows.items()},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Bulk RNA-Seq SQLite query database.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help=f"directory containing {SAMPLE_LIST_NAME} and {TPM_MATRIX_NAME}",
    )
    parser.add_argument(
        "--output-db",
        type=Path,
        default=Path(os.getenv("BULK_RNASEQ_DB_PATH") or DEFAULT_OUTPUT_DB),
        help="SQLite database path to create",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_database(args.source_root, args.output_db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


STATIC_ROOT = Path(__file__).resolve().parent / "static" / "bulk_rnaseq"
DEFAULT_DB_PATH = Path("/srv/bulk_rnaseq/current/bulk_rnaseq.sqlite")
MAX_QUERY_GENES = 50
MAX_SEARCH_LIMIT = 100
SCOPES = {"tissue", "sample_name", "sample_tissue", "sample"}
TRANSFORMS = {"log2_tpm", "tpm", "row_zscore"}

router = APIRouter()


def get_database_path() -> Path:
    return Path(os.getenv("BULK_RNASEQ_DB_PATH") or DEFAULT_DB_PATH).resolve()


def connect_db() -> sqlite3.Connection:
    db_path = get_database_path()
    if not db_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Bulk RNA-Seq database not found at {db_path}",
        )
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Failed to open Bulk RNA-Seq database: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _db_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(status_code=500, detail=f"Bulk RNA-Seq database query failed: {exc}")


def _json_loads(value: str) -> Any:
    return json.loads(value or "null")


def _float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def _round_float(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return round(float(value), 6)


def clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def parse_gene_list(value: str) -> list[str]:
    genes: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,;]+", value.strip()):
        gene_id = token.strip()
        if not gene_id or gene_id in seen:
            continue
        genes.append(gene_id)
        seen.add(gene_id)
    if not genes:
        raise HTTPException(status_code=400, detail="At least one gene is required")
    if len(genes) > MAX_QUERY_GENES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_QUERY_GENES} query genes are supported",
        )
    return genes


def normalize_scope(scope: str) -> str:
    normalized = (scope or "sample_tissue").strip()
    if normalized not in SCOPES:
        raise HTTPException(status_code=400, detail=f"Unknown Bulk RNA-Seq scope: {normalized}")
    return normalized


def normalize_transform(transform: str) -> str:
    normalized = (transform or "log2_tpm").strip()
    if normalized not in TRANSFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown Bulk RNA-Seq transform: {normalized}")
    return normalized


def load_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {
            str(row["key"]): str(row["value"])
            for row in conn.execute("select key, value from metadata").fetchall()
        }
    except sqlite3.Error:
        return {}


def load_status() -> dict[str, Any]:
    try:
        with connect_db() as conn:
            metadata = load_metadata(conn)
            counts = {
                "genes": int(conn.execute("select count(*) from genes").fetchone()[0]),
                "samples": int(conn.execute("select count(*) from samples").fetchone()[0]),
            }
            group_counts = {
                str(row["scope"]): int(row["n"])
                for row in conn.execute(
                    "select scope, count(*) as n from groups group by scope order by scope"
                ).fetchall()
            }
            return {
                "configured": True,
                "dataset": metadata.get("dataset_id", ""),
                "databasePath": str(get_database_path()),
                "counts": counts,
                "groups": group_counts,
                "defaultScope": "sample_tissue",
                "defaultTransform": "log2_tpm",
            }
    except Exception as exc:
        raise _db_error(exc) from exc


def search_genes(q: str, limit: int) -> dict[str, Any]:
    query = q.strip()
    limit = clamp_int(limit, minimum=1, maximum=MAX_SEARCH_LIMIT)
    try:
        with connect_db() as conn:
            if query:
                pattern = f"%{query}%"
                rows = conn.execute(
                    """
                    select g.gene_id, g.transcript_id, g.gene_name, e.mean_tpm, e.max_tpm
                    from genes g
                    join expression_vectors e on e.gene_id = g.gene_id
                    where g.gene_id like ? or coalesce(g.gene_name, '') like ?
                    order by g.gene_id
                    limit ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select g.gene_id, g.transcript_id, g.gene_name, e.mean_tpm, e.max_tpm
                    from genes g
                    join expression_vectors e on e.gene_id = g.gene_id
                    order by g.gene_id
                    limit ?
                    """,
                    (limit,),
                ).fetchall()
        return {
            "query": query,
            "genes": [
                {
                    "geneId": row["gene_id"],
                    "transcriptId": row["transcript_id"],
                    "geneName": row["gene_name"] or "",
                    "meanTpm": _float(row["mean_tpm"]),
                    "maxTpm": _float(row["max_tpm"]),
                }
                for row in rows
            ],
        }
    except Exception as exc:
        raise _db_error(exc) from exc


def _row_to_gene(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "geneId": row["gene_id"],
        "transcriptId": row["transcript_id"],
        "geneName": row["gene_name"] or "",
        "transcriptCount": int(row["transcript_count"]),
        "meanTpm": _float(row["mean_tpm"]),
        "maxTpm": _float(row["max_tpm"]),
        "detectedSamples": int(row["detected_samples"]),
    }


def load_gene_rows(conn: sqlite3.Connection, genes: list[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in genes)
    rows = conn.execute(
        f"""
        select
          g.gene_id,
          g.transcript_id,
          g.gene_name,
          g.transcript_count,
          e.mean_tpm,
          e.max_tpm,
          e.detected_samples
        from genes g
        join expression_vectors e on e.gene_id = g.gene_id
        where g.gene_id in ({placeholders})
        """,
        genes,
    ).fetchall()
    by_gene = {str(row["gene_id"]): _row_to_gene(row) for row in rows}
    missing = [gene for gene in genes if gene not in by_gene]
    if missing:
        raise HTTPException(status_code=404, detail=f"Genes not found: {', '.join(missing)}")
    return [by_gene[gene] for gene in genes]


def load_columns(conn: sqlite3.Connection, scope: str) -> list[dict[str, Any]]:
    if scope == "sample":
        rows = conn.execute(
            """
            select sample_idx, sample_column, sample_name, tissue
            from samples
            order by sample_idx
            """
        ).fetchall()
        return [
            {
                "id": row["sample_column"],
                "label": row["sample_column"],
                "sampleColumn": row["sample_column"],
                "sampleName": row["sample_name"],
                "tissue": row["tissue"],
                "nSamples": 1,
                "sampleIndices": [int(row["sample_idx"])],
            }
            for row in rows
        ]

    rows = conn.execute(
        """
        select group_idx, group_id, label, sample_name, tissue, n_samples, sample_indices_json
        from groups
        where scope = ?
        order by group_idx
        """,
        (scope,),
    ).fetchall()
    return [
        {
            "id": row["group_id"],
            "label": row["label"],
            "sampleName": row["sample_name"] or "",
            "tissue": row["tissue"] or "",
            "nSamples": int(row["n_samples"]),
            "sampleIndices": _json_loads(row["sample_indices_json"]),
        }
        for row in rows
    ]


def load_sample_metadata(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select sample_idx, sample_column, sample_name, tissue
        from samples
        order by sample_idx
        """
    ).fetchall()
    return [
        {
            "sampleIdx": int(row["sample_idx"]),
            "sampleColumn": row["sample_column"],
            "sampleName": row["sample_name"],
            "tissue": row["tissue"],
        }
        for row in rows
    ]


def load_raw_sample_values(conn: sqlite3.Connection, gene_id: str) -> list[float]:
    row = conn.execute(
        "select tpm_json from expression_vectors where gene_id = ?",
        (gene_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{gene_id} not found")
    return [_float(value) for value in _json_loads(row["tpm_json"])]


def load_raw_values_for_scope(
    conn: sqlite3.Connection,
    genes: list[str],
    scope: str,
) -> tuple[list[list[float]], list[list[float]], list[list[int]]]:
    raw_values: list[list[float]] = []
    sd_values: list[list[float]] = []
    n_values: list[list[int]] = []

    if scope == "sample":
        for gene_id in genes:
            values = load_raw_sample_values(conn, gene_id)
            raw_values.append(values)
            sd_values.append([0.0 for _ in values])
            n_values.append([1 for _ in values])
        return raw_values, sd_values, n_values

    placeholders = ",".join("?" for _ in genes)
    rows = conn.execute(
        f"""
        select gene_id, mean_tpm_json, sd_tpm_json, n_json
        from group_expression_vectors
        where scope = ? and gene_id in ({placeholders})
        """,
        [scope, *genes],
    ).fetchall()
    by_gene = {str(row["gene_id"]): row for row in rows}
    for gene_id in genes:
        row = by_gene.get(gene_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"{gene_id} not found in scope {scope}")
        raw_values.append([_float(value) for value in _json_loads(row["mean_tpm_json"])])
        sd_values.append([_float(value) for value in _json_loads(row["sd_tpm_json"])])
        n_values.append([int(value) for value in _json_loads(row["n_json"])])
    return raw_values, sd_values, n_values


def transform_values(raw_values: list[list[float]], transform: str) -> list[list[float]]:
    if transform == "tpm":
        return [[_round_float(value) for value in row] for row in raw_values]
    logged = [[math.log2(max(0.0, value) + 1.0) for value in row] for row in raw_values]
    if transform == "log2_tpm":
        return [[_round_float(value) for value in row] for row in logged]

    transformed: list[list[float]] = []
    for row in logged:
        if not row:
            transformed.append([])
            continue
        mean = sum(row) / len(row)
        if len(row) > 1:
            variance = sum((value - mean) ** 2 for value in row) / (len(row) - 1)
            sd = math.sqrt(variance)
        else:
            sd = 0.0
        if sd <= 0:
            transformed.append([0.0 for _ in row])
        else:
            transformed.append([_round_float((value - mean) / sd) for value in row])
    return transformed


def build_replicate_payload(
    *,
    columns: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    sample_values: list[float],
) -> list[dict[str, Any]]:
    sample_by_idx = {sample["sampleIdx"]: sample for sample in samples}
    payload = []
    for column_index, column in enumerate(columns):
        replicates = []
        for sample_idx in column["sampleIndices"]:
            sample = sample_by_idx.get(int(sample_idx))
            if sample is None:
                continue
            replicates.append(
                {
                    **sample,
                    "tpm": _round_float(sample_values[int(sample_idx)]),
                }
            )
        payload.append(
            {
                "columnIndex": column_index,
                "columnId": column["id"],
                "samples": replicates,
            }
        )
    return payload


def _matrix_min_max(values: list[list[float]]) -> tuple[float, float]:
    flattened = [value for row in values for value in row]
    if not flattened:
        return 0.0, 0.0
    return min(flattened), max(flattened)


def load_expression(genes: str, scope: str, transform: str) -> dict[str, Any]:
    query_genes = parse_gene_list(genes)
    selected_scope = normalize_scope(scope)
    selected_transform = normalize_transform(transform)

    try:
        with connect_db() as conn:
            metadata = load_metadata(conn)
            gene_rows = load_gene_rows(conn, query_genes)
            columns = load_columns(conn, selected_scope)
            raw_values, sd_values, n_values = load_raw_values_for_scope(
                conn,
                query_genes,
                selected_scope,
            )
            values = transform_values(raw_values, selected_transform)
            value_min, value_max = _matrix_min_max(values)
            raw_min, raw_max = _matrix_min_max(raw_values)
            payload: dict[str, Any] = {
                "dataset": metadata.get("dataset_id", ""),
                "scope": selected_scope,
                "transform": selected_transform,
                "genes": gene_rows,
                "columns": columns,
                "values": values,
                "rawValues": raw_values,
                "sdValues": sd_values,
                "nValues": n_values,
                "summary": {
                    "geneCount": len(query_genes),
                    "columnCount": len(columns),
                    "valueMin": _round_float(value_min),
                    "valueMax": _round_float(value_max),
                    "rawMin": _round_float(raw_min),
                    "rawMax": _round_float(raw_max),
                },
            }
            if len(query_genes) == 1:
                sample_values = load_raw_sample_values(conn, query_genes[0])
                payload["samples"] = load_sample_metadata(conn)
                payload["replicates"] = build_replicate_payload(
                    columns=columns,
                    samples=payload["samples"],
                    sample_values=sample_values,
                )
            return payload
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/bulk-rnaseq", include_in_schema=False)
async def serve_bulk_rnaseq_index() -> FileResponse:
    index_path = STATIC_ROOT / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Bulk RNA-Seq frontend not found")
    return FileResponse(index_path)


@router.get("/api/bulk-rnaseq/status")
async def api_bulk_rnaseq_status() -> dict[str, Any]:
    return await asyncio.to_thread(load_status)


@router.get("/api/bulk-rnaseq/genes")
async def api_bulk_rnaseq_genes(q: str = "", limit: int = 20) -> dict[str, Any]:
    return await asyncio.to_thread(search_genes, q, limit)


@router.get("/api/bulk-rnaseq/expression")
async def api_bulk_rnaseq_expression(
    genes: str = "",
    scope: str = "sample_tissue",
    transform: str = "log2_tpm",
) -> dict[str, Any]:
    return await asyncio.to_thread(load_expression, genes, scope, transform)

from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


DEFAULT_DATA_ROOT = Path("/srv/spatial_data/current")
STATIC_ROOT = Path(__file__).resolve().parent / "static" / "spatial"
CACHE_VERSION = 2
DOTPLOT_CLUSTER_COLUMN = "seurat_clusters"
TISSUE_COLUMN = "celltype"
TISSUES_TABLE = "tissues"
TISSUE_ASSIGNMENTS_TABLE = "tissue_cell_assignments"
DATA_PREFIX = "/api/spatial/data"

router = APIRouter()


def get_data_root() -> Path:
    return Path(os.getenv("SPATIAL_VIEWER_DATA_ROOT") or DEFAULT_DATA_ROOT).resolve()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_relative_path(path: str | None) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    raw = raw.lstrip("/")
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise HTTPException(status_code=400, detail="Invalid path")
        parts.append(part)
    return "/".join(parts)


def _resolve_under(root: Path, relative_path: str | None) -> Path:
    clean = _normalize_relative_path(relative_path)
    target = (root / clean).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return target


def _resolve_config_path(data_root: Path, value: str | None) -> Path:
    return _resolve_under(data_root, value or "")


def _public_data_path(dataset_id: str, relative_path: str | None = "") -> str:
    clean = _normalize_relative_path(relative_path)
    suffix = f"/{clean}" if clean else ""
    return f"{DATA_PREFIX}/{dataset_id}{suffix}"


def _legacy_data_path_to_public(dataset: dict[str, Any], value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("/data/"):
        return f"{DATA_PREFIX}/_root/data/{_normalize_relative_path(raw.removeprefix('/data/'))}"
    if raw == "/data":
        return f"{DATA_PREFIX}/_root/data"
    if raw.startswith("/dataset-data/"):
        parts = raw.strip("/").split("/", 2)
        dataset_id = parts[1] if len(parts) >= 2 else dataset["id"]
        if len(parts) >= 3:
            return _public_data_path(dataset_id, parts[2])
        return _public_data_path(dataset_id)
    return _public_data_path(dataset["id"], raw)


def load_dataset_catalog() -> dict[str, Any]:
    data_root = get_data_root()
    config_path = data_root / "datasets.json"
    if not config_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Spatial dataset catalog not found at {config_path}",
        )

    try:
        payload = _read_json(config_path)
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"Failed to read dataset catalog: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid dataset catalog JSON: {exc}") from exc

    datasets = []
    for dataset_payload in payload.get("datasets", []):
        dataset = dict(dataset_payload)
        dataset_id = str(dataset.get("id") or "").strip()
        data_root_value = str(dataset.get("dataRoot") or "").strip()
        if not dataset_id or not data_root_value:
            continue
        dataset["id"] = dataset_id
        dataset["data_root"] = _resolve_config_path(data_root, data_root_value)
        dataset["db_path"] = dataset["data_root"] / "expression.sqlite"
        if dataset.get("colors"):
            dataset["colors_path"] = _resolve_config_path(data_root, str(dataset.get("colors")))
        elif (dataset["data_root"] / "colors.txt").is_file():
            dataset["colors_path"] = dataset["data_root"] / "colors.txt"
        else:
            dataset["colors_path"] = data_root / "colors.txt"
        samples = []
        for sample_payload in dataset.get("samples", []):
            sample = dict(sample_payload)
            if sample.get("contoursPath"):
                sample["_contours_public_path"] = _legacy_data_path_to_public(
                    dataset, str(sample.get("contoursPath"))
                )
            samples.append(sample)
        dataset["samples"] = samples
        dataset["sample_ids"] = [str(sample["id"]) for sample in samples if sample.get("id")]
        datasets.append(dataset)

    default_id = str(payload.get("defaultDataset") or (datasets[0]["id"] if datasets else ""))
    return {
        "defaultDataset": default_id,
        "datasets": datasets,
        "datasetById": {dataset["id"]: dataset for dataset in datasets},
    }


def get_dataset(dataset_id: str | None = None) -> dict[str, Any]:
    catalog = load_dataset_catalog()
    selected_id = (dataset_id or catalog["defaultDataset"]).strip()
    dataset = catalog["datasetById"].get(selected_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"dataset {selected_id!r} not found")
    return dataset


def public_dataset_payload() -> dict[str, Any]:
    catalog = load_dataset_catalog()
    payload_datasets = []
    for dataset in catalog["datasets"]:
        data_path = _public_data_path(dataset["id"])
        samples = []
        for sample in dataset["samples"]:
            sample_payload = {
                "id": sample["id"],
                "label": sample.get("label", sample["id"]),
                "columns": int(sample.get("columns") or 0),
            }
            if sample.get("_contours_public_path"):
                sample_payload["contoursPath"] = sample["_contours_public_path"]
            samples.append(sample_payload)

        payload_datasets.append(
            {
                "id": dataset["id"],
                "label": dataset.get("label", dataset["id"]),
                "dataPath": data_path,
                "defaultSample": dataset.get("defaultSample")
                or (dataset["sample_ids"][0] if dataset["sample_ids"] else ""),
                "defaultGene": dataset.get("defaultGene", ""),
                "samples": samples,
            }
        )

    return {"defaultDataset": catalog["defaultDataset"], "datasets": payload_datasets}


def sample_config(dataset: dict[str, Any], sample_id: str) -> dict[str, Any]:
    for sample in dataset["samples"]:
        if sample["id"] == sample_id:
            return sample
    raise HTTPException(
        status_code=404,
        detail=f"sample {sample_id!r} not found in dataset {dataset['id']!r}",
    )


def connect_db(dataset: dict[str, Any]) -> sqlite3.Connection:
    if not dataset["db_path"].is_file():
        raise HTTPException(
            status_code=503,
            detail=f"expression database not found for dataset {dataset['id']}",
        )
    return sqlite3.connect(f"file:{dataset['db_path']}?mode=ro", uri=True)


def normalize_hex_color(value: str) -> str | None:
    color = value.strip()
    if len(color) == 7 and color[0] == "#":
        digits = color[1:]
    elif len(color) == 6:
        digits = color
        color = f"#{color}"
    else:
        return None

    if all(char in "0123456789abcdefABCDEF" for char in digits):
        return color.upper()
    return None


def load_category_colors(dataset: dict[str, Any]) -> dict[str, Any]:
    payload = {"formatVersion": 1, "clusters": {}, "tissues": {}}
    colors_path = dataset["colors_path"]
    if not colors_path.is_file():
        return payload

    section = None
    for raw_line in colors_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip().lower()
            if "cluster" in heading:
                section = "clusters"
            elif "tissue" in heading:
                section = "tissues"
            continue
        if section not in payload:
            continue
        try:
            label, color_value = line.rsplit(None, 1)
        except ValueError:
            continue
        color = normalize_hex_color(color_value)
        if color is not None:
            payload[section][label.strip()] = color
    return payload


def get_gene_names(dataset: dict[str, Any]) -> list[str]:
    try:
        with connect_db(dataset) as conn:
            rows = conn.execute("SELECT gene FROM genes ORDER BY gene_id").fetchall()
        if rows:
            return [str(row[0]) for row in rows]
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
    except sqlite3.Error as exc:
        pass

    genes_path = dataset["data_root"] / "genes.json"
    if genes_path.is_file():
        return _read_json(genes_path)

    raise HTTPException(
        status_code=503,
        detail=f"gene list unavailable for dataset {dataset['id']}; build expression.sqlite first",
    )


def load_expression_from_db(dataset: dict[str, Any], sample: str, gene: str) -> dict[str, Any]:
    try:
        with connect_db(dataset) as conn:
            gene_row = conn.execute(
                "SELECT gene_id FROM genes WHERE gene = ?",
                (gene,),
            ).fetchone()
            if gene_row is None:
                raise ValueError(f"{gene} not found")

            gene_id = int(gene_row[0])
            stats = conn.execute(
                """
                SELECT vmin, vmax, nonzero
                FROM sample_genes
                WHERE sample = ? AND gene_id = ?
                """,
                (sample, gene_id),
            ).fetchone()
            if stats is None:
                raise ValueError(f"{gene} not found in {sample}")

            rows = conn.execute(
                """
                SELECT cell_id, value
                FROM expression_values
                WHERE sample = ? AND gene_id = ?
                ORDER BY cell_id
                """,
                (sample, gene_id),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"expression SQLite error: {exc}") from exc

    return {
        "formatVersion": CACHE_VERSION,
        "source": "sqlite",
        "sample": sample,
        "gene": gene,
        "vmin": float(stats[0]),
        "vmax": float(stats[1]),
        "nonzero": int(stats[2]),
        "values": [[int(cell_id), float(value)] for cell_id, value in rows],
    }


def db_table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def get_metadata_value(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return default
    return str(row[0]) if row is not None else default


def load_dotplot_from_db(dataset: dict[str, Any], gene: str) -> dict[str, Any]:
    try:
        with connect_db(dataset) as conn:
            if not (
                db_table_exists(conn, "dotplot_clusters")
                and db_table_exists(conn, "dotplot_gene_cluster_stats")
            ):
                raise HTTPException(
                    status_code=503,
                    detail="dotplot tables not found; run web_viewer/import_dotplot_stats.py",
                )

            gene_row = conn.execute("SELECT gene_id FROM genes WHERE gene = ?", (gene,)).fetchone()
            if gene_row is None:
                raise ValueError(f"{gene} not found")

            gene_id = int(gene_row[0])
            cluster_column = get_metadata_value(
                conn,
                "dotplot_cluster_column",
                DOTPLOT_CLUSTER_COLUMN,
            )
            rows = conn.execute(
                """
                SELECT
                  c.cluster_id,
                  c.cluster_order,
                  c.cell_count,
                  s.avg_expr,
                  s.pct_expr,
                  s.expressing_count
                FROM dotplot_clusters c
                JOIN dotplot_gene_cluster_stats s
                  ON s.cluster_id = c.cluster_id
                 AND s.gene_id = ?
                ORDER BY c.cluster_order
                """,
                (gene_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"dotplot SQLite error: {exc}") from exc

    if not rows:
        raise ValueError(f"dotplot stats for {gene} not found")

    avg_values = [float(row[3]) for row in rows]
    logged_avg = [math.log1p(value) for value in avg_values]
    if len(logged_avg) > 1:
        mean_logged = sum(logged_avg) / len(logged_avg)
        variance = sum((value - mean_logged) ** 2 for value in logged_avg) / (
            len(logged_avg) - 1
        )
        sd_logged = math.sqrt(variance)
    else:
        sd_logged = 0.0
    scaled_avg = [
        max(-2.5, min(2.5, (value - mean_logged) / sd_logged)) if sd_logged > 0 else 0.0
        for value in logged_avg
    ]

    return {
        "gene": gene,
        "clusterColumn": cluster_column,
        "clusters": [
            {
                "id": str(cluster_id),
                "label": str(cluster_id),
                "order": int(cluster_order),
                "cellCount": int(cell_count),
                "avgExpr": float(avg_expr),
                "avgExprScaled": float(scaled_avg[index]),
                "pctExpr": float(pct_expr),
                "expressingCount": int(expressing_count),
            }
            for index, (
                cluster_id,
                cluster_order,
                cell_count,
                avg_expr,
                pct_expr,
                expressing_count,
            ) in enumerate(rows)
        ],
    }


def load_tissues_from_db(dataset: dict[str, Any]) -> dict[str, Any]:
    try:
        with connect_db(dataset) as conn:
            if not (
                db_table_exists(conn, TISSUES_TABLE)
                and db_table_exists(conn, TISSUE_ASSIGNMENTS_TABLE)
            ):
                raise HTTPException(
                    status_code=503,
                    detail="tissue tables not found; run web_viewer/import_tissues.py",
                )

            tissue_column = get_metadata_value(conn, "tissue_column", TISSUE_COLUMN)
            source = get_metadata_value(conn, "tissue_source", "")
            tissue_rows = conn.execute(
                f"""
                SELECT
                  tissue_id,
                  tissue_label,
                  tissue_order,
                  cell_count,
                  assigned_cell_count
                FROM {TISSUES_TABLE}
                ORDER BY tissue_order, tissue_label
                """
            ).fetchall()
            assignment_rows = conn.execute(
                f"""
                SELECT sample, cell_id, tissue_id
                FROM {TISSUE_ASSIGNMENTS_TABLE}
                ORDER BY sample, cell_id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"tissue SQLite error: {exc}") from exc

    samples = {
        sample: {"sample": sample, "assignedCellCount": 0, "cells": []}
        for sample in dataset["sample_ids"]
    }
    for sample, cell_id, tissue_id in assignment_rows:
        sample_payload = samples.setdefault(
            str(sample),
            {"sample": str(sample), "assignedCellCount": 0, "cells": []},
        )
        sample_payload["cells"].append([int(cell_id), str(tissue_id)])
        sample_payload["assignedCellCount"] += 1

    return {
        "formatVersion": 1,
        "source": source,
        "tissueColumn": tissue_column,
        "tissues": [
            {
                "id": str(tissue_id),
                "label": str(tissue_label),
                "order": int(tissue_order),
                "cellCount": int(cell_count),
                "assignedCellCount": int(assigned_cell_count),
            }
            for tissue_id, tissue_label, tissue_order, cell_count, assigned_cell_count in tissue_rows
        ],
        "samples": samples,
    }


def get_json_document(dataset: dict[str, Any], name: str, fallback_path: Path) -> Any | None:
    try:
        with connect_db(dataset) as conn:
            if db_table_exists(conn, "json_documents"):
                row = conn.execute(
                    "SELECT payload FROM json_documents WHERE name = ?",
                    (name,),
                ).fetchone()
                if row is not None:
                    return json.loads(row[0])
    except HTTPException:
        pass
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"JSON document SQLite error: {exc}") from exc

    if fallback_path.is_file():
        return _read_json(fallback_path)
    return None


@router.get("/spatial", include_in_schema=False)
async def serve_spatial_index() -> FileResponse:
    index_path = STATIC_ROOT / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Spatial viewer frontend not found")
    return FileResponse(index_path)


@router.get("/api/spatial/datasets")
async def api_datasets() -> dict[str, Any]:
    return public_dataset_payload()


@router.get("/api/spatial/genes")
async def api_genes(dataset: str = "") -> dict[str, Any]:
    selected = get_dataset(dataset.strip() or None)
    return {"dataset": selected["id"], "genes": get_gene_names(selected)}


@router.get("/api/spatial/colors")
async def api_colors(dataset: str = "") -> dict[str, Any]:
    selected = get_dataset(dataset.strip() or None)
    return load_category_colors(selected)


@router.get("/api/spatial/replicates")
async def api_replicates(dataset: str = "") -> Any:
    selected = get_dataset(dataset.strip() or None)
    replicates = get_json_document(
        selected,
        "replicates",
        selected["data_root"] / "replicates.json",
    )
    if replicates is None:
        raise HTTPException(status_code=404, detail="replicates not found")
    return replicates


@router.get("/api/spatial/tissues")
async def api_tissues(dataset: str = "") -> dict[str, Any]:
    return load_tissues_from_db(get_dataset(dataset.strip() or None))


@router.get("/api/spatial/gene")
async def api_gene(dataset: str = "", gene: str = "") -> dict[str, Any]:
    selected = get_dataset(dataset.strip() or None)
    gene = gene.strip()
    if not gene:
        raise HTTPException(status_code=400, detail="missing gene")
    genes = get_gene_names(selected)
    if gene not in genes:
        raise HTTPException(status_code=404, detail=f"{gene} not found")

    try:
        samples = {
            sample: load_expression_from_db(selected, sample, gene)
            for sample in selected["sample_ids"]
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "dataset": selected["id"],
        "gene": gene,
        "range": {
            "vmin": min(sample_payload["vmin"] for sample_payload in samples.values()),
            "vmax": max(sample_payload["vmax"] for sample_payload in samples.values()),
        },
        "samples": samples,
    }


@router.get("/api/spatial/dotplot")
async def api_dotplot(dataset: str = "", gene: str = "") -> dict[str, Any]:
    selected = get_dataset(dataset.strip() or None)
    gene = gene.strip()
    if not gene:
        raise HTTPException(status_code=400, detail="missing gene")
    try:
        return load_dotplot_from_db(selected, gene)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/spatial/data/{dataset_id}/{file_path:path}", include_in_schema=False)
async def api_spatial_data(dataset_id: str, file_path: str) -> FileResponse:
    if dataset_id == "_root":
        target = _resolve_under(get_data_root(), file_path)
    else:
        dataset = get_dataset(dataset_id)
        target = _resolve_under(dataset["data_root"], file_path)
    if target.suffix.lower() == ".sqlite":
        raise HTTPException(status_code=404, detail="file not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target)

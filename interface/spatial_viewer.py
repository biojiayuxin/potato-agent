from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Iterable
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
DEFAULT_CLUSTER_NAME_PATH = STATIC_ROOT / "cluster_name.txt"
CLUSTER_NAME_PATH = Path(
    os.getenv("SPATIAL_CLUSTER_NAME_PATH") or DEFAULT_CLUSTER_NAME_PATH
)

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


def load_cluster_name_groups(path: Path | None = None) -> dict[str, dict[str, str]]:
    selected_path = path or CLUSTER_NAME_PATH
    if not selected_path.is_file():
        return {}

    groups: dict[str, dict[str, str]] = {}
    current_group = ""
    try:
        lines = selected_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("###"):
            current_group = line.lstrip("#").strip()
            if current_group:
                groups.setdefault(current_group, {})
            continue
        if line.startswith("#") or not current_group:
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        cluster_id, cluster_name = parts[0].strip(), parts[1].strip()
        if cluster_id and cluster_name:
            groups.setdefault(current_group, {})[cluster_id] = cluster_name

    return groups


def cluster_names_for_dataset(dataset: dict[str, Any]) -> dict[str, str]:
    groups = load_cluster_name_groups()
    if not groups:
        return {}

    candidates = [
        str(dataset.get("id") or "").strip(),
        str(dataset.get("label") or "").strip(),
    ]
    sample_ids = [str(sample_id) for sample_id in dataset.get("sample_ids", [])]
    if sample_ids:
        candidates.append(" + ".join(sample_ids))

    normalized_groups = {group.casefold(): names for group, names in groups.items()}
    for candidate in candidates:
        if candidate and candidate.casefold() in normalized_groups:
            return normalized_groups[candidate.casefold()]
    return {}


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
    cluster_names = cluster_names_for_dataset(dataset)

    return {
        "gene": gene,
        "clusterColumn": cluster_column,
        "clusters": [
            {
                "id": str(cluster_id),
                "label": str(cluster_id),
                "name": cluster_names.get(str(cluster_id), ""),
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


def _coerce_cell_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _group_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _assignment_pairs_from_payload(payload: Any) -> list[tuple[int, str]]:
    if isinstance(payload, dict):
        if "cells" in payload:
            raw_cells = payload.get("cells") or []
            items: Iterable[Any]
            items = raw_cells.items() if isinstance(raw_cells, dict) else raw_cells
        else:
            items = payload.items()
    elif isinstance(payload, list):
        items = payload
    else:
        return []

    cells: list[tuple[int, str]] = []
    for item in items:
        if isinstance(item, dict):
            cell_raw = (
                item.get("cell_id")
                if "cell_id" in item
                else item.get("cellId", item.get("id"))
            )
            group_raw = (
                item.get("cluster_id")
                if "cluster_id" in item
                else item.get(
                    "clusterId",
                    item.get("tissue_id", item.get("tissueId", item.get("groupId"))),
                )
            )
        else:
            try:
                cell_raw, group_raw = item[0], item[1]
            except (TypeError, IndexError):
                continue

        cell_id = _coerce_cell_id(cell_raw)
        if cell_id is None or group_raw is None:
            continue
        cells.append((cell_id, str(group_raw)))
    return cells


def _append_group_definition(
    groups: list[dict[str, Any]],
    seen: set[str],
    group_id: str,
    label: str | None = None,
    order: int | None = None,
) -> None:
    if group_id in seen:
        return
    seen.add(group_id)
    groups.append(
        {
            "id": group_id,
            "label": label or group_id,
            "order": int(order) if order is not None else len(groups),
        }
    )


def _cluster_group_definitions(
    dataset: dict[str, Any],
    payload: dict[str, Any],
    assignments: dict[str, list[tuple[int, str]]],
) -> list[dict[str, Any]]:
    cluster_names = cluster_names_for_dataset(dataset)
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()

    raw_clusters = payload.get("clusters") or []
    if isinstance(raw_clusters, list):
        for index, raw_cluster in enumerate(raw_clusters):
            if isinstance(raw_cluster, dict):
                raw_id = raw_cluster.get(
                    "id",
                    raw_cluster.get("cluster_id", raw_cluster.get("clusterId")),
                )
                if raw_id is None:
                    continue
                group_id = str(raw_id)
                label = (
                    cluster_names.get(group_id)
                    or raw_cluster.get("name")
                    or raw_cluster.get("label")
                    or group_id
                )
                raw_order = raw_cluster.get(
                    "order",
                    raw_cluster.get("cluster_order", raw_cluster.get("clusterOrder", index)),
                )
                try:
                    order = int(raw_order)
                except (TypeError, ValueError):
                    order = index
            else:
                group_id = str(raw_cluster)
                label = cluster_names.get(group_id) or group_id
                order = index
            _append_group_definition(groups, seen, group_id, str(label), order)

    assigned_group_ids = {
        group_id for cells in assignments.values() for _, group_id in cells if group_id
    }
    for group_id in sorted(assigned_group_ids - seen, key=_group_sort_key):
        _append_group_definition(groups, seen, group_id, cluster_names.get(group_id) or group_id)

    return sorted(groups, key=lambda group: (int(group["order"]), _group_sort_key(group["id"])))


def load_cluster_assignment_data(dataset: dict[str, Any]) -> dict[str, Any]:
    payload = get_json_document(
        dataset,
        "clusters",
        dataset["data_root"] / "clusters.json",
    )
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail="cluster assignments not found; run web_viewer/export_clusters.py",
        )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="cluster assignment JSON is not an object")

    raw_samples = payload.get("samples") or {}
    assignments = {sample: [] for sample in dataset["sample_ids"]}
    if isinstance(raw_samples, dict):
        for sample, sample_payload in raw_samples.items():
            assignments[str(sample)] = _assignment_pairs_from_payload(sample_payload)

    return {
        "groups": _cluster_group_definitions(dataset, payload, assignments),
        "assignments": assignments,
    }


def load_tissue_assignment_data(
    conn: sqlite3.Connection,
    dataset: dict[str, Any],
) -> dict[str, Any]:
    if not (
        db_table_exists(conn, TISSUES_TABLE)
        and db_table_exists(conn, TISSUE_ASSIGNMENTS_TABLE)
    ):
        raise HTTPException(
            status_code=503,
            detail="tissue tables not found; run web_viewer/import_tissues.py",
        )

    tissue_column = get_metadata_value(conn, "tissue_column", TISSUE_COLUMN)
    tissue_rows = conn.execute(
        f"""
        SELECT
          tissue_id,
          tissue_label,
          tissue_order
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

    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tissue_id, tissue_label, tissue_order in tissue_rows:
        _append_group_definition(
            groups,
            seen,
            str(tissue_id),
            str(tissue_label or tissue_id),
            int(tissue_order),
        )

    assignments = {sample: [] for sample in dataset["sample_ids"]}
    for sample, cell_id, tissue_id in assignment_rows:
        sample_id = str(sample)
        group_id = str(tissue_id)
        assignments.setdefault(sample_id, []).append((int(cell_id), group_id))
        if group_id not in seen:
            _append_group_definition(groups, seen, group_id, group_id)

    return {
        "column": tissue_column,
        "groups": sorted(groups, key=lambda group: (int(group["order"]), _group_sort_key(group["id"]))),
        "assignments": assignments,
    }


def load_gene_expression_lookup(
    conn: sqlite3.Connection,
    dataset: dict[str, Any],
    gene: str,
) -> dict[str, Any]:
    gene_row = conn.execute("SELECT gene_id FROM genes WHERE gene = ?", (gene,)).fetchone()
    if gene_row is None:
        raise ValueError(f"{gene} not found")

    gene_id = int(gene_row[0])
    sample_ids = list(dataset["sample_ids"])
    expression_by_sample: dict[str, dict[int, float]] = {sample: {} for sample in sample_ids}
    range_values = [0.0]

    if sample_ids:
        placeholders = ",".join("?" for _ in sample_ids)
        stats_rows = conn.execute(
            f"""
            SELECT sample, vmin, vmax
            FROM sample_genes
            WHERE gene_id = ? AND sample IN ({placeholders})
            """,
            (gene_id, *sample_ids),
        ).fetchall()
        for _, vmin, vmax in stats_rows:
            range_values.extend([float(vmin), float(vmax)])

        value_rows = conn.execute(
            f"""
            SELECT sample, cell_id, value
            FROM expression_values
            WHERE gene_id = ? AND sample IN ({placeholders})
            ORDER BY sample, cell_id
            """,
            (gene_id, *sample_ids),
        ).fetchall()
    else:
        value_rows = []

    for sample, cell_id, value in value_rows:
        expression_by_sample.setdefault(str(sample), {})[int(cell_id)] = float(value)
        range_values.append(float(value))

    return {
        "geneId": gene_id,
        "range": {"vmin": min(range_values), "vmax": max(range_values)},
        "values": expression_by_sample,
    }


def _empty_expression_accumulator(
    scope: str,
    sample: str | None,
    group: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scope": scope,
        "sample": sample,
        "groupId": str(group["id"]),
        "groupLabel": str(group["label"]),
        "cellCount": 0,
        "expressingCount": 0,
        "sumExpr": 0.0,
        "maxExpr": 0.0,
    }


def _add_expression_value(accumulator: dict[str, Any], value: float) -> None:
    accumulator["cellCount"] += 1
    accumulator["sumExpr"] += value
    accumulator["maxExpr"] = max(float(accumulator["maxExpr"]), value)
    if value > 0:
        accumulator["expressingCount"] += 1


def _finalize_expression_accumulator(accumulator: dict[str, Any]) -> dict[str, Any]:
    cell_count = int(accumulator["cellCount"])
    expressing_count = int(accumulator["expressingCount"])
    sum_expr = float(accumulator["sumExpr"])
    return {
        "scope": accumulator["scope"],
        "sample": accumulator["sample"],
        "groupId": accumulator["groupId"],
        "groupLabel": accumulator["groupLabel"],
        "cellCount": cell_count,
        "expressingCount": expressing_count,
        "pctExpr": (expressing_count * 100.0 / cell_count) if cell_count else 0.0,
        "avgExpr": (sum_expr / cell_count) if cell_count else 0.0,
        "avgExprExpressing": (sum_expr / expressing_count) if expressing_count else 0.0,
        "sumExpr": sum_expr,
        "maxExpr": float(accumulator["maxExpr"]),
    }


def aggregate_expression_by_group(
    groups: list[dict[str, Any]],
    assignments: dict[str, list[tuple[int, str]]],
    sample_ids: list[str],
    expression_by_sample: dict[str, dict[int, float]],
) -> list[dict[str, Any]]:
    groups_by_id = {str(group["id"]): group for group in groups}
    dataset_accumulators = {
        group_id: _empty_expression_accumulator("dataset", None, group)
        for group_id, group in groups_by_id.items()
    }
    sample_accumulators = {
        (sample, group_id): _empty_expression_accumulator("sample", sample, group)
        for sample in sample_ids
        for group_id, group in groups_by_id.items()
    }

    for sample, cells in assignments.items():
        if sample not in sample_ids:
            continue
        sample_values = expression_by_sample.get(sample, {})
        for cell_id, group_id in cells:
            group = groups_by_id.get(group_id)
            if group is None:
                continue
            value = float(sample_values.get(cell_id, 0.0))
            _add_expression_value(dataset_accumulators[group_id], value)
            _add_expression_value(sample_accumulators[(sample, group_id)], value)

    rows: list[dict[str, Any]] = []
    for group in groups:
        group_id = str(group["id"])
        rows.append(_finalize_expression_accumulator(dataset_accumulators[group_id]))
        for sample in sample_ids:
            rows.append(_finalize_expression_accumulator(sample_accumulators[(sample, group_id)]))
    return rows


def load_agent_expression_statistics(dataset: dict[str, Any], gene: str) -> dict[str, Any]:
    try:
        with connect_db(dataset) as conn:
            expression_data = load_gene_expression_lookup(conn, dataset, gene)
            cluster_column = get_metadata_value(
                conn,
                "dotplot_cluster_column",
                DOTPLOT_CLUSTER_COLUMN,
            )
            tissue_data = load_tissue_assignment_data(conn, dataset)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"expression SQLite error: {exc}") from exc

    cluster_data = load_cluster_assignment_data(dataset)
    sample_ids = list(dataset["sample_ids"])
    expression_by_sample = expression_data["values"]
    return {
        "dataset": dataset["id"],
        "gene": gene,
        "samples": sample_ids,
        "range": expression_data["range"],
        "clusterColumn": cluster_column,
        "tissueColumn": tissue_data["column"],
        "clusterExpression": aggregate_expression_by_group(
            cluster_data["groups"],
            cluster_data["assignments"],
            sample_ids,
            expression_by_sample,
        ),
        "tissueExpression": aggregate_expression_by_group(
            tissue_data["groups"],
            tissue_data["assignments"],
            sample_ids,
            expression_by_sample,
        ),
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


@router.get("/api/spatial/cluster-names")
async def api_cluster_names(dataset: str = "") -> dict[str, Any]:
    selected = get_dataset(dataset.strip() or None)
    return {
        "dataset": selected["id"],
        "names": cluster_names_for_dataset(selected),
    }


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


@router.get("/api/spatial/agent/expression")
async def api_agent_expression(dataset: str = "", gene: str = "") -> dict[str, Any]:
    dataset_id = dataset.strip()
    gene = gene.strip()
    if not dataset_id:
        raise HTTPException(status_code=400, detail="missing dataset")
    if not gene:
        raise HTTPException(status_code=400, detail="missing gene")

    selected = get_dataset(dataset_id)
    try:
        return load_agent_expression_statistics(selected, gene)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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

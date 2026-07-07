from __future__ import annotations

import asyncio
import os
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

try:  # psycopg is optional until WGCNA deployment is configured.
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised by environments without psycopg
    psycopg = None
    dict_row = None


STATIC_ROOT = Path(__file__).resolve().parent / "static" / "wgcna"
DEFAULT_NETWORKS = ("leaf", "stem", "root", "reproductive", "tuberization")
NETWORK_SET = set(DEFAULT_NETWORKS)
MAX_QUERY_GENES = 20
MAX_TOP_N = 500
MAX_EDGE_CAP = 10000

router = APIRouter()


def get_database_url() -> str:
    return os.getenv("WGCNA_DATABASE_URL", "").strip()


def connect_db():
    database_url = get_database_url()
    if not database_url:
        raise HTTPException(
            status_code=503,
            detail="WGCNA_DATABASE_URL is not configured",
        )
    if psycopg is None or dict_row is None:
        raise HTTPException(
            status_code=503,
            detail="psycopg is not installed in the interface environment",
        )
    try:
        return psycopg.connect(database_url, row_factory=dict_row)
    except Exception as exc:  # pragma: no cover - depends on deployment DB
        raise HTTPException(status_code=503, detail=f"Failed to connect to WGCNA database: {exc}") from exc


def _db_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(status_code=500, detail=f"WGCNA database query failed: {exc}")


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _bool(value: Any) -> bool:
    return bool(value)


def parse_gene_list(value: str) -> list[str]:
    genes = []
    seen = set()
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


def normalize_networks(value: str) -> list[str]:
    raw = value.strip()
    if not raw or raw.lower() == "all":
        return list(DEFAULT_NETWORKS)
    networks = []
    seen = set()
    for token in re.split(r"[\s,;]+", raw):
        network = token.strip()
        if not network or network in seen:
            continue
        if network not in NETWORK_SET:
            raise HTTPException(status_code=400, detail=f"Unknown WGCNA network: {network}")
        networks.append(network)
        seen.add(network)
    return networks or list(DEFAULT_NETWORKS)


def clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _row_pair_clause(pairs: Iterable[tuple[str, str]]) -> tuple[str, list[str]]:
    placeholders = []
    params: list[str] = []
    for left, right in pairs:
        placeholders.append("(%s,%s)")
        params.extend([left, right])
    if not placeholders:
        return "(NULL,NULL)", []
    return ",".join(placeholders), params


def _network_gene_payload(row: dict[str, Any], *, is_query_gene: bool = False) -> dict[str, Any]:
    return {
        "id": f"{row['network_id']}:{row['gene_id']}",
        "gene_id": row["gene_id"],
        "gene_name": row.get("gene_name") or "",
        "network_id": row["network_id"],
        "module": row.get("module") or "",
        "module_size": _int(row.get("module_size")),
        "variance_log2tpm": _float(row.get("variance_log2tpm")),
        "kme_own_module": _float(row.get("kme_own_module")),
        "is_grey": _bool(row.get("is_grey")),
        "is_query_gene": is_query_gene,
        "annotation": row.get("annotation") or "",
        "chromosome": row.get("chromosome") or "",
        "start_pos": _int(row.get("start_pos")),
        "end_pos": _int(row.get("end_pos")),
    }


def _fetch_network_genes(conn, networks: list[str], genes: list[str]) -> list[dict[str, Any]]:
    if not networks or not genes:
        return []
    return list(
        conn.execute(
            """
            select
              ng.network_id,
              ng.gene_id,
              ng.module,
              ng.variance_log2tpm,
              ng.kme_own_module,
              ng.is_grey,
              g.gene_name,
              g.chromosome,
              g.start_pos,
              g.end_pos,
              g.annotation,
              m.module_size
            from network_genes ng
            left join genes g on g.gene_id = ng.gene_id
            left join modules m on m.network_id = ng.network_id and m.module = ng.module
            where ng.network_id = any(%s) and ng.gene_id = any(%s)
            """,
            (networks, genes),
        ).fetchall()
    )


def _fetch_query_edges(
    conn,
    *,
    network_id: str,
    gene_id: str,
    top_n: int,
    tom_min: float | None,
    same_module_only: bool,
) -> list[dict[str, Any]]:
    where = ["network_id = %s", "gene_id = %s"]
    params: list[Any] = [network_id, gene_id]
    if tom_min is not None:
        where.append("tom >= %s")
        params.append(tom_min)
    if same_module_only:
        where.append("same_module = true")
    params.append(top_n)
    return list(
        conn.execute(
            f"""
            select
              network_id,
              gene_id,
              neighbor_gene_id,
              tom,
              tom_percentile,
              rank,
              same_module,
              gene_module,
              neighbor_module
            from coexpression_edges_top
            where {' and '.join(where)}
            order by rank asc, tom desc
            limit %s
            """,
            params,
        ).fetchall()
    )


def _fetch_neighbor_edges(
    conn,
    *,
    network_id: str,
    genes: list[str],
    tom_min: float | None,
    same_module_only: bool,
    limit: int,
) -> list[dict[str, Any]]:
    if len(genes) < 2 or limit <= 0:
        return []
    where = [
        "network_id = %s",
        "gene_id = any(%s)",
        "neighbor_gene_id = any(%s)",
        "gene_id <> neighbor_gene_id",
    ]
    params: list[Any] = [network_id, genes, genes]
    if tom_min is not None:
        where.append("tom >= %s")
        params.append(tom_min)
    if same_module_only:
        where.append("same_module = true")
    params.append(limit)
    return list(
        conn.execute(
            f"""
            select
              network_id,
              gene_id,
              neighbor_gene_id,
              tom,
              tom_percentile,
              rank,
              same_module,
              gene_module,
              neighbor_module
            from coexpression_edges_top
            where {' and '.join(where)}
            order by tom desc, rank asc
            limit %s
            """,
            params,
        ).fetchall()
    )


def _edge_pair(gene_a: str, gene_b: str) -> tuple[str, str]:
    return tuple(sorted((gene_a, gene_b)))  # type: ignore[return-value]


def _add_tom_edge(
    edges: dict[str, dict[str, Any]],
    row: dict[str, Any],
    *,
    max_edges: int,
) -> None:
    if len(edges) >= max_edges:
        return
    network_id = str(row["network_id"])
    gene_a, gene_b = _edge_pair(str(row["gene_id"]), str(row["neighbor_gene_id"]))
    edge_id = f"tom:{network_id}:{gene_a}--{gene_b}"
    current = edges.get(edge_id)
    next_rank = _int(row.get("rank")) or 0
    next_tom = _float(row.get("tom")) or 0.0
    if current is not None:
        current_rank = _int(current["data"].get("rank")) or 0
        current_tom = _float(current["data"].get("tom")) or 0.0
        if current_rank and next_rank and next_rank >= current_rank and next_tom <= current_tom:
            return
    edges[edge_id] = {
        "data": {
            "id": edge_id,
            "source": f"{network_id}:{gene_a}",
            "target": f"{network_id}:{gene_b}",
            "edge_type": "tom_edge",
            "network_id": network_id,
            "gene_a": gene_a,
            "gene_b": gene_b,
            "tom": next_tom,
            "tom_percentile": _float(row.get("tom_percentile")),
            "rank": next_rank,
            "same_module": _bool(row.get("same_module")),
            "gene_module": row.get("gene_module") or "",
            "neighbor_module": row.get("neighbor_module") or "",
            "shared_coexpression": False,
            "shared_networks": [],
            "shared_n_networks": 0,
        }
    }


def _shared_networks(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return [item.strip().strip('"') for item in text.split(",") if item.strip()]


def _mark_shared_edges(conn, edges: dict[str, dict[str, Any]]) -> None:
    pairs = sorted({
        (edge["data"]["gene_a"], edge["data"]["gene_b"])
        for edge in edges.values()
        if edge["data"].get("edge_type") == "tom_edge"
    })
    if not pairs:
        return
    clause, params = _row_pair_clause(pairs)
    rows = conn.execute(
        f"""
        select
          gene_a,
          gene_b,
          n_networks,
          networks,
          tom_leaf,
          tom_stem,
          tom_root,
          tom_reproductive,
          tom_tuberization
        from shared_coexpression_edges
        where (gene_a, gene_b) in ({clause})
        """,
        params,
    ).fetchall()
    shared_by_pair = {
        (str(row["gene_a"]), str(row["gene_b"])): row
        for row in rows
    }
    for edge in edges.values():
        data = edge["data"]
        key = (data["gene_a"], data["gene_b"])
        row = shared_by_pair.get(key)
        if row is None:
            continue
        data["shared_coexpression"] = True
        data["shared_n_networks"] = _int(row.get("n_networks")) or 0
        data["shared_networks"] = _shared_networks(row.get("networks"))
        data["shared_tom_by_network"] = {
            "leaf": _float(row.get("tom_leaf")),
            "stem": _float(row.get("tom_stem")),
            "root": _float(row.get("tom_root")),
            "reproductive": _float(row.get("tom_reproductive")),
            "tuberization": _float(row.get("tom_tuberization")),
        }


def _remove_query_nodes_without_tom_edges(
    nodes: dict[str, dict[str, Any]],
    tom_edges: dict[str, dict[str, Any]],
    query_genes: list[str],
) -> int:
    query_gene_set = set(query_genes)
    connected_query_pairs: set[tuple[str, str]] = set()
    for edge in tom_edges.values():
        data = edge.get("data", {})
        if data.get("edge_type") != "tom_edge":
            continue
        network_id = str(data.get("network_id") or "")
        for gene_key in ("gene_a", "gene_b"):
            gene_id = str(data.get(gene_key) or "")
            if gene_id in query_gene_set and network_id:
                connected_query_pairs.add((network_id, gene_id))

    removed = 0
    for node_id, node in list(nodes.items()):
        data = node.get("data", {})
        if not data.get("is_query_gene"):
            continue
        key = (str(data.get("network_id") or ""), str(data.get("gene_id") or ""))
        if key not in connected_query_pairs:
            del nodes[node_id]
            removed += 1
    return removed


def _fetch_module_overlaps(
    conn,
    candidate_modules: set[tuple[str, str]],
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    filtered = sorted(
        (network, module)
        for network, module in candidate_modules
        if network and module and module != "grey"
    )
    if not filtered:
        return []
    clause, params = _row_pair_clause(filtered)
    rows = conn.execute(
        f"""
        select
          network_a,
          module_a,
          network_b,
          module_b,
          overlap_genes,
          size_a,
          size_b,
          jaccard,
          overlap_ratio_a,
          overlap_ratio_b,
          p_value,
          q_value
        from module_overlaps
        where (network_a, module_a) in ({clause})
           or (network_b, module_b) in ({clause})
        order by q_value asc nulls last, overlap_genes desc
        limit %s
        """,
        params + params + [limit],
    ).fetchall()
    return [
        {
            "network_a": row["network_a"],
            "module_a": row["module_a"],
            "network_b": row["network_b"],
            "module_b": row["module_b"],
            "overlap_genes": _int(row.get("overlap_genes")),
            "size_a": _int(row.get("size_a")),
            "size_b": _int(row.get("size_b")),
            "jaccard": _float(row.get("jaccard")),
            "overlap_ratio_a": _float(row.get("overlap_ratio_a")),
            "overlap_ratio_b": _float(row.get("overlap_ratio_b")),
            "p_value": _float(row.get("p_value")),
            "q_value": _float(row.get("q_value")),
        }
        for row in rows
    ]


def load_status() -> dict[str, Any]:
    try:
        with connect_db() as conn:
            networks = list(
                conn.execute(
                    """
                    select network_id, sample_count, genes_used_for_wgcna, soft_power
                    from networks
                    order by array_position(%s::text[], network_id)
                    """,
                    (list(DEFAULT_NETWORKS),),
                ).fetchall()
            )
            counts = {}
            for table in (
                "genes",
                "network_genes",
                "modules",
                "coexpression_edges_top",
                "module_overlaps",
                "shared_coexpression_edges",
            ):
                counts[table] = _int(conn.execute(f"select count(*) as n from {table}").fetchone()["n"])
            return {
                "configured": True,
                "networks": networks,
                "counts": counts,
                "default_networks": list(DEFAULT_NETWORKS),
            }
    except Exception as exc:
        raise _db_error(exc) from exc


def search_genes(q: str, limit: int) -> dict[str, Any]:
    query = q.strip()
    limit = clamp_int(limit, minimum=1, maximum=100)
    try:
        with connect_db() as conn:
            if query:
                pattern = f"%{query}%"
                rows = conn.execute(
                    """
                    select gene_id, gene_name, annotation
                    from genes
                    where gene_id ilike %s or coalesce(gene_name, '') ilike %s
                    order by gene_id
                    limit %s
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select gene_id, gene_name, annotation
                    from genes
                    order by gene_id
                    limit %s
                    """,
                    (limit,),
                ).fetchall()
            return {"query": query, "genes": list(rows)}
    except Exception as exc:
        raise _db_error(exc) from exc


def load_gene_detail(gene_id: str) -> dict[str, Any]:
    gene_id = gene_id.strip()
    if not gene_id:
        raise HTTPException(status_code=400, detail="missing gene_id")
    try:
        with connect_db() as conn:
            gene = conn.execute(
                """
                select gene_id, gene_name, chromosome, start_pos, end_pos, annotation
                from genes
                where gene_id = %s
                """,
                (gene_id,),
            ).fetchone()
            if gene is None:
                raise HTTPException(status_code=404, detail=f"{gene_id} not found")
            network_rows = _fetch_network_genes(conn, list(DEFAULT_NETWORKS), [gene_id])
            return {
                "gene": dict(gene),
                "networks": [
                    _network_gene_payload(row, is_query_gene=True)
                    for row in network_rows
                ],
            }
    except Exception as exc:
        raise _db_error(exc) from exc


def load_module_detail(network_id: str, module: str) -> dict[str, Any]:
    network_id = network_id.strip()
    module = module.strip()
    if network_id not in NETWORK_SET:
        raise HTTPException(status_code=400, detail=f"Unknown WGCNA network: {network_id}")
    if not module:
        raise HTTPException(status_code=400, detail="missing module")
    try:
        with connect_db() as conn:
            module_row = conn.execute(
                """
                select network_id, module, module_size, is_grey
                from modules
                where network_id = %s and module = %s
                """,
                (network_id, module),
            ).fetchone()
            if module_row is None:
                raise HTTPException(status_code=404, detail="module not found")
            top_genes = conn.execute(
                """
                select
                  ng.gene_id,
                  g.gene_name,
                  ng.kme_own_module,
                  ng.variance_log2tpm
                from network_genes ng
                left join genes g on g.gene_id = ng.gene_id
                where ng.network_id = %s and ng.module = %s
                order by abs(ng.kme_own_module) desc nulls last, ng.gene_id
                limit 50
                """,
                (network_id, module),
            ).fetchall()
            overlaps = _fetch_module_overlaps(conn, {(network_id, module)}, limit=50)
            return {
                "module": {
                    "network_id": module_row["network_id"],
                    "module": module_row["module"],
                    "module_size": _int(module_row.get("module_size")),
                    "is_grey": _bool(module_row.get("is_grey")),
                },
                "top_genes": list(top_genes),
                "overlaps": overlaps,
            }
    except Exception as exc:
        raise _db_error(exc) from exc


def load_coexpression(
    *,
    genes: str,
    networks: str,
    top_n: int,
    tom_min: float | None,
    same_module_only: bool,
    include_neighbor_edges: bool,
    include_cross_network: bool,
    include_module_overlaps: bool,
    include_shared_edges: bool,
    max_total_edges: int,
) -> dict[str, Any]:
    query_genes = parse_gene_list(genes)
    selected_networks = normalize_networks(networks)
    top_n = clamp_int(top_n, minimum=1, maximum=MAX_TOP_N)
    max_total_edges = clamp_int(max_total_edges, minimum=1, maximum=MAX_EDGE_CAP)
    warnings: list[str] = []

    try:
        with connect_db() as conn:
            query_gene_rows = _fetch_network_genes(conn, selected_networks, query_genes)
            query_gene_row_keys = {
                (str(row["network_id"]), str(row["gene_id"]))
                for row in query_gene_rows
            }
            present_query_genes = {str(row["gene_id"]) for row in query_gene_rows}
            for gene_id in query_genes:
                if gene_id not in present_query_genes:
                    warnings.append(f"{gene_id} was not found in the selected WGCNA networks")
                else:
                    present_networks = {
                        network for network, row_gene in query_gene_row_keys if row_gene == gene_id
                    }
                    missing_networks = [
                        network for network in selected_networks if network not in present_networks
                    ]
                    if missing_networks:
                        warnings.append(
                            f"{gene_id} is absent from: {', '.join(missing_networks)}"
                        )

            edge_rows: list[dict[str, Any]] = []
            for network_id, gene_id in sorted(query_gene_row_keys):
                edge_rows.extend(
                    _fetch_query_edges(
                        conn,
                        network_id=network_id,
                        gene_id=gene_id,
                        top_n=top_n,
                        tom_min=tom_min,
                        same_module_only=same_module_only,
                    )
                )

            node_pairs = set(query_gene_row_keys)
            for row in edge_rows:
                node_pairs.add((str(row["network_id"]), str(row["gene_id"])))
                node_pairs.add((str(row["network_id"]), str(row["neighbor_gene_id"])))

            metadata_rows = _fetch_network_genes(
                conn,
                selected_networks,
                sorted({gene_id for _, gene_id in node_pairs}),
            )
            metadata_by_pair = {
                (str(row["network_id"]), str(row["gene_id"])): row
                for row in metadata_rows
            }

            nodes: dict[str, dict[str, Any]] = {}
            for network_id, gene_id in sorted(node_pairs):
                row = metadata_by_pair.get((network_id, gene_id))
                if row is None:
                    row = {
                        "network_id": network_id,
                        "gene_id": gene_id,
                        "module": "",
                        "is_grey": False,
                    }
                data = _network_gene_payload(
                    row,
                    is_query_gene=gene_id in query_genes,
                )
                nodes[data["id"]] = {"data": data}

            tom_edges: dict[str, dict[str, Any]] = {}
            for row in edge_rows:
                _add_tom_edge(tom_edges, row, max_edges=max_total_edges)

            if include_neighbor_edges and len(tom_edges) < max_total_edges:
                genes_by_network: dict[str, list[str]] = defaultdict(list)
                for network_id, gene_id in node_pairs:
                    genes_by_network[network_id].append(gene_id)
                remaining = max_total_edges - len(tom_edges)
                for network_id in selected_networks:
                    if remaining <= 0:
                        break
                    neighbor_rows = _fetch_neighbor_edges(
                        conn,
                        network_id=network_id,
                        genes=sorted(set(genes_by_network.get(network_id, []))),
                        tom_min=tom_min,
                        same_module_only=same_module_only,
                        limit=min(remaining * 3, max_total_edges),
                    )
                    for row in neighbor_rows:
                        before = len(tom_edges)
                        _add_tom_edge(tom_edges, row, max_edges=max_total_edges)
                        remaining -= max(0, len(tom_edges) - before)
                        if remaining <= 0:
                            break

            if include_shared_edges:
                _mark_shared_edges(conn, tom_edges)

            _remove_query_nodes_without_tom_edges(nodes, tom_edges, query_genes)

            all_edges = list(tom_edges.values())
            if include_cross_network:
                nodes_by_gene: dict[str, list[str]] = defaultdict(list)
                for node in nodes.values():
                    nodes_by_gene[node["data"]["gene_id"]].append(node["data"]["network_id"])
                for gene_id, gene_networks in sorted(nodes_by_gene.items()):
                    ordered = [
                        network for network in DEFAULT_NETWORKS if network in set(gene_networks)
                    ]
                    for source_network, target_network in zip(ordered, ordered[1:]):
                        edge_id = f"same_gene:{gene_id}:{source_network}--{target_network}"
                        all_edges.append(
                            {
                                "data": {
                                    "id": edge_id,
                                    "source": f"{source_network}:{gene_id}",
                                    "target": f"{target_network}:{gene_id}",
                                    "edge_type": "same_gene",
                                    "gene_id": gene_id,
                                }
                            }
                        )

            query_modules = {
                (str(row["network_id"]), str(row.get("module") or ""))
                for row in query_gene_rows
            }
            module_overlaps = (
                _fetch_module_overlaps(conn, query_modules)
                if include_module_overlaps
                else []
            )

            return {
                "query_genes": query_genes,
                "warnings": warnings,
                "summary": {
                    "networks": selected_networks,
                    "node_count": len(nodes),
                    "edge_count": len(all_edges),
                    "tom_edge_count": len(tom_edges),
                    "module_overlap_count": len(module_overlaps),
                },
                "elements": {
                    "nodes": list(nodes.values()),
                    "edges": all_edges,
                },
                "module_overlaps": module_overlaps,
            }
    except Exception as exc:
        raise _db_error(exc) from exc


@router.get("/wgcna", include_in_schema=False)
async def serve_wgcna_index() -> FileResponse:
    index_path = STATIC_ROOT / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="WGCNA frontend not found")
    return FileResponse(index_path)


@router.get("/api/wgcna/status")
async def api_wgcna_status() -> dict[str, Any]:
    return await asyncio.to_thread(load_status)


@router.get("/api/wgcna/genes")
async def api_wgcna_genes(q: str = "", limit: int = 20) -> dict[str, Any]:
    return await asyncio.to_thread(search_genes, q, limit)


@router.get("/api/wgcna/gene/{gene_id}")
async def api_wgcna_gene(gene_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(load_gene_detail, gene_id)


@router.get("/api/wgcna/module/{network_id}/{module}")
async def api_wgcna_module(network_id: str, module: str) -> dict[str, Any]:
    return await asyncio.to_thread(load_module_detail, network_id, module)


@router.get("/api/wgcna/coexpression")
async def api_wgcna_coexpression(
    genes: str = "",
    networks: str = "all",
    top_n: int = 50,
    tom_min: float | None = None,
    same_module_only: bool = True,
    include_neighbor_edges: bool = True,
    include_cross_network: bool = True,
    include_module_overlaps: bool = True,
    include_shared_edges: bool = True,
    max_total_edges: int = 3000,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        load_coexpression,
        genes=genes,
        networks=networks,
        top_n=top_n,
        tom_min=tom_min,
        same_module_only=same_module_only,
        include_neighbor_edges=include_neighbor_edges,
        include_cross_network=include_cross_network,
        include_module_overlaps=include_module_overlaps,
        include_shared_edges=include_shared_edges,
        max_total_edges=max_total_edges,
    )

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


DEFAULT_DB_PATH = Path("/srv/pan_genome/current/pan_genome.sqlite")
EXPECTED_SCHEMA_VERSION = 2
DEFAULT_MEMBER_LIMIT = 100
MAX_MEMBER_LIMIT = 1_000
MAX_MEMBER_OFFSET = 100_000
DEFAULT_ORTHOGROUP_LIMIT = 100
MAX_ORTHOGROUP_LIMIT = 1_000
MAX_ORTHOGROUP_OFFSET = 250_000
MAX_GENE_MATCHES = 100
MAX_IDENTIFIER_LENGTH = 1_024
ORTHOGROUP_CATEGORIES = (
    "core",
    "soft-core",
    "dispensable",
    "private",
)
CATEGORY_ALIASES = {
    "core": "core",
    "soft-core": "soft-core",
    "softcore": "soft-core",
    "soft_core": "soft-core",
    "dispensable": "dispensable",
    "private": "private",
}
DATABASE_UNAVAILABLE_DETAIL = "Pan-genome database is unavailable."
QUERY_FAILED_DETAIL = "Pan-genome query failed."
REQUIRED_TABLES = frozenset(
    {
        "pan_genome_metadata",
        "genomes",
        "orthogroups",
        "orthogroup_members",
    }
)

LOGGER = logging.getLogger("potato_interface.pan_genome")
router = APIRouter()


def get_database_path() -> Path:
    return Path(os.getenv("PAN_GENOME_DB_PATH") or DEFAULT_DB_PATH).resolve()


def _database_unavailable(exc: Exception | None = None) -> HTTPException:
    if exc is None:
        LOGGER.error("Pan-genome database is unavailable")
    else:
        LOGGER.exception("Pan-genome database is unavailable", exc_info=exc)
    return HTTPException(status_code=503, detail=DATABASE_UNAVAILABLE_DETAIL)


def _query_failed(exc: Exception) -> HTTPException:
    LOGGER.exception("Pan-genome query failed", exc_info=exc)
    return HTTPException(status_code=500, detail=QUERY_FAILED_DETAIL)


def connect_db() -> sqlite3.Connection:
    db_path = get_database_path()
    if not db_path.is_file():
        raise _database_unavailable()

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            f"{db_path.as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        tables = {str(row["name"]) for row in table_rows}
        if not REQUIRED_TABLES.issubset(tables):
            raise ValueError("pan-genome schema is incomplete")
        metadata = conn.execute(
            """
            SELECT schema_version, counts_json
            FROM pan_genome_metadata
            WHERE singleton = 1
            """
        ).fetchone()
        if (
            metadata is None
            or int(metadata["schema_version"]) != EXPECTED_SCHEMA_VERSION
        ):
            raise ValueError("unsupported pan-genome schema version")
        counts = json.loads(str(metadata["counts_json"]))
        if not isinstance(counts, dict):
            raise ValueError("invalid pan-genome counts metadata")
        category_counts = counts.get("orthogroup_categories")
        if not isinstance(category_counts, dict) or set(category_counts) != set(
            ORTHOGROUP_CATEGORIES
        ):
            raise ValueError("invalid pan-genome category counts metadata")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in category_counts.values()
        ):
            raise ValueError("invalid pan-genome category counts metadata")
        if sum(category_counts.values()) != int(counts.get("orthogroups", -1)):
            raise ValueError("pan-genome category counts do not match total")
        return conn
    except Exception as exc:
        if conn is not None:
            conn.close()
        raise _database_unavailable(exc) from exc


def _validate_identifier(value: str, label: str) -> str:
    value = value.strip()
    if not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise HTTPException(status_code=400, detail=f"Invalid {label}.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise HTTPException(status_code=400, detail=f"Invalid {label}.")
    return value


def _validate_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > MAX_MEMBER_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"Limit must be between 1 and {MAX_MEMBER_LIMIT}.",
        )
    if offset < 0 or offset > MAX_MEMBER_OFFSET:
        raise HTTPException(
            status_code=400,
            detail=f"Offset must be between 0 and {MAX_MEMBER_OFFSET}.",
        )


def _validate_orthogroup_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > MAX_ORTHOGROUP_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"Limit must be between 1 and {MAX_ORTHOGROUP_LIMIT}.",
        )
    if offset < 0 or offset > MAX_ORTHOGROUP_OFFSET:
        raise HTTPException(
            status_code=400,
            detail=f"Offset must be between 0 and {MAX_ORTHOGROUP_OFFSET}.",
        )


def _normalize_category(category: str) -> str:
    normalized = category.strip().casefold()
    if not normalized:
        return ""
    canonical = CATEGORY_ALIASES.get(normalized)
    if canonical is None:
        raise HTTPException(status_code=400, detail="Invalid orthogroup category.")
    return canonical


def _get_genome_row(conn: sqlite3.Connection, genome: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT genome_pk, name, display_order, gene_count, orthogroup_count
        FROM genomes
        WHERE name = ? COLLATE NOCASE
        """,
        (genome,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Genome not found.")
    return row


def _get_orthogroup_row(conn: sqlite3.Connection, orthogroup: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT orthogroup_pk, name, display_order, gene_count, genome_count,
               category
        FROM orthogroups
        WHERE name = ? COLLATE NOCASE
        """,
        (orthogroup,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Orthogroup not found.")
    return row


def load_metadata() -> dict[str, Any]:
    try:
        with closing(connect_db()) as conn:
            row = conn.execute(
                """
                SELECT schema_version, dataset_version, built_at,
                       source_name, source_sha256, source_bytes, counts_json
                FROM pan_genome_metadata
                WHERE singleton = 1
                """
            ).fetchone()
            if row is None:
                raise ValueError("pan-genome metadata is missing")
            counts = json.loads(str(row["counts_json"]))
            return {
                "schemaVersion": int(row["schema_version"]),
                "datasetVersion": str(row["dataset_version"]),
                "builtAt": str(row["built_at"]),
                "source": {
                    "name": str(row["source_name"]),
                    "sha256": str(row["source_sha256"]),
                    "bytes": int(row["source_bytes"]),
                },
                "counts": {
                    "genomes": int(counts.get("genomes", 0)),
                    "orthogroups": int(counts.get("orthogroups", 0)),
                    "geneMemberships": int(counts.get("gene_memberships", 0)),
                    "occupiedCells": int(counts.get("occupied_cells", 0)),
                    "orthogroupCategories": {
                        category: int(counts["orthogroup_categories"][category])
                        for category in ORTHOGROUP_CATEGORIES
                    },
                },
                "classification": {
                    "basis": "accessionPresence",
                    "categories": {
                        "core": {"minAccessions": 135, "maxAccessions": 135},
                        "soft-core": {
                            "minAccessions": 122,
                            "maxAccessions": 134,
                        },
                        "dispensable": {
                            "minAccessions": 2,
                            "maxAccessions": 121,
                        },
                        "private": {"minAccessions": 1, "maxAccessions": 1},
                    },
                },
                "pagination": {
                    "defaultMemberLimit": DEFAULT_MEMBER_LIMIT,
                    "maxMemberLimit": MAX_MEMBER_LIMIT,
                    "maxMemberOffset": MAX_MEMBER_OFFSET,
                    "defaultOrthogroupLimit": DEFAULT_ORTHOGROUP_LIMIT,
                    "maxOrthogroupLimit": MAX_ORTHOGROUP_LIMIT,
                    "maxOrthogroupOffset": MAX_ORTHOGROUP_OFFSET,
                },
            }
    except HTTPException:
        raise
    except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError) as exc:
        raise _query_failed(exc) from exc


def list_genomes() -> dict[str, Any]:
    try:
        with closing(connect_db()) as conn:
            rows = conn.execute(
                """
                SELECT name, display_order, gene_count, orthogroup_count
                FROM genomes
                ORDER BY display_order
                """
            ).fetchall()
            return {
                "genomes": [
                    {
                        "name": str(row["name"]),
                        "displayOrder": int(row["display_order"]),
                        "geneCount": int(row["gene_count"]),
                        "orthogroupCount": int(row["orthogroup_count"]),
                    }
                    for row in rows
                ],
                "count": len(rows),
            }
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise _query_failed(exc) from exc


def lookup_gene(gene_id: str, genome: str = "") -> dict[str, Any]:
    gene_id = _validate_identifier(gene_id, "gene ID")
    genome = genome.strip()
    if genome:
        genome = _validate_identifier(genome, "genome")
    try:
        with closing(connect_db()) as conn:
            parameters: list[Any] = [gene_id]
            where = "m.gene_id = ? COLLATE NOCASE"
            if genome:
                genome_row = _get_genome_row(conn, genome)
                where += " AND m.genome_pk = ?"
                parameters.append(int(genome_row["genome_pk"]))
            parameters.append(MAX_GENE_MATCHES + 1)
            rows = conn.execute(
                f"""
                SELECT g.name AS genome, m.gene_id, o.name AS orthogroup,
                       o.category
                FROM orthogroup_members AS m
                JOIN genomes AS g ON g.genome_pk = m.genome_pk
                JOIN orthogroups AS o ON o.orthogroup_pk = m.orthogroup_pk
                WHERE {where}
                ORDER BY g.display_order, o.display_order
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            if not rows:
                raise HTTPException(status_code=404, detail="Gene not found.")
            truncated = len(rows) > MAX_GENE_MATCHES
            matches = rows[:MAX_GENE_MATCHES]
            return {
                "query": {"geneId": gene_id, "genome": genome or None},
                "matches": [
                    {
                        "genome": str(row["genome"]),
                        "geneId": str(row["gene_id"]),
                        "orthogroup": str(row["orthogroup"]),
                        "category": str(row["category"]),
                    }
                    for row in matches
                ],
                "matchCount": len(matches),
                "truncated": truncated,
            }
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise _query_failed(exc) from exc


def list_orthogroups(
    *,
    category: str = "",
    limit: int = DEFAULT_ORTHOGROUP_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    category = _normalize_category(category)
    _validate_orthogroup_pagination(limit, offset)
    try:
        with closing(connect_db()) as conn:
            where = ""
            parameters: list[Any] = []
            if category:
                where = "WHERE category = ?"
                parameters.append(category)
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM orthogroups {where}", parameters
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT name, gene_count, genome_count, category
                FROM orthogroups
                {where}
                ORDER BY display_order
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
            return {
                "category": category or None,
                "orthogroups": [
                    {
                        "orthogroup": str(row["name"]),
                        "geneCount": int(row["gene_count"]),
                        "genomeCount": int(row["genome_count"]),
                        "category": str(row["category"]),
                    }
                    for row in rows
                ],
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "returned": len(rows),
                    "hasMore": offset + len(rows) < total,
                },
            }
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise _query_failed(exc) from exc


def load_orthogroup(orthogroup: str) -> dict[str, Any]:
    orthogroup = _validate_identifier(orthogroup, "orthogroup")
    try:
        with closing(connect_db()) as conn:
            group = _get_orthogroup_row(conn, orthogroup)
            rows = conn.execute(
                """
                SELECT g.name AS genome, COUNT(*) AS gene_count
                FROM orthogroup_members AS m
                JOIN genomes AS g ON g.genome_pk = m.genome_pk
                WHERE m.orthogroup_pk = ?
                GROUP BY m.genome_pk
                ORDER BY g.display_order
                """,
                (int(group["orthogroup_pk"]),),
            ).fetchall()
            return {
                "orthogroup": str(group["name"]),
                "geneCount": int(group["gene_count"]),
                "genomeCount": int(group["genome_count"]),
                "category": str(group["category"]),
                "genomes": [
                    {
                        "name": str(row["genome"]),
                        "geneCount": int(row["gene_count"]),
                    }
                    for row in rows
                ],
            }
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise _query_failed(exc) from exc


def load_orthogroup_members(
    orthogroup: str,
    *,
    genome: str = "",
    limit: int = DEFAULT_MEMBER_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    orthogroup = _validate_identifier(orthogroup, "orthogroup")
    genome = genome.strip()
    if genome:
        genome = _validate_identifier(genome, "genome")
    _validate_pagination(limit, offset)
    try:
        with closing(connect_db()) as conn:
            group = _get_orthogroup_row(conn, orthogroup)
            parameters: list[Any] = [int(group["orthogroup_pk"])]
            where = "m.orthogroup_pk = ?"
            if genome:
                genome_row = _get_genome_row(conn, genome)
                where += " AND m.genome_pk = ?"
                parameters.append(int(genome_row["genome_pk"]))
                genome = str(genome_row["name"])
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM orthogroup_members AS m WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT g.name AS genome, m.gene_id
                FROM orthogroup_members AS m
                JOIN genomes AS g ON g.genome_pk = m.genome_pk
                WHERE {where}
                ORDER BY g.display_order, m.gene_id COLLATE NOCASE
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
            return {
                "orthogroup": str(group["name"]),
                "category": str(group["category"]),
                "genome": genome or None,
                "members": [
                    {"genome": str(row["genome"]), "geneId": str(row["gene_id"])}
                    for row in rows
                ],
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "returned": len(rows),
                    "hasMore": offset + len(rows) < total,
                },
            }
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise _query_failed(exc) from exc


@router.get("/api/pan-genome/metadata")
async def api_pan_genome_metadata() -> dict[str, Any]:
    return await asyncio.to_thread(load_metadata)


@router.get("/api/pan-genome/genomes")
async def api_pan_genome_genomes() -> dict[str, Any]:
    return await asyncio.to_thread(list_genomes)


@router.get("/api/pan-genome/genes/lookup")
async def api_pan_genome_gene_lookup(
    gene_id: str = "", genome: str = ""
) -> dict[str, Any]:
    return await asyncio.to_thread(lookup_gene, gene_id, genome)


@router.get("/api/pan-genome/orthogroups")
async def api_pan_genome_orthogroups(
    category: str = "",
    limit: int = DEFAULT_ORTHOGROUP_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        list_orthogroups,
        category=category,
        limit=limit,
        offset=offset,
    )


@router.get("/api/pan-genome/orthogroups/{orthogroup}")
async def api_pan_genome_orthogroup(orthogroup: str) -> dict[str, Any]:
    return await asyncio.to_thread(load_orthogroup, orthogroup)


@router.get("/api/pan-genome/orthogroups/{orthogroup}/members")
async def api_pan_genome_orthogroup_members(
    orthogroup: str,
    genome: str = "",
    limit: int = DEFAULT_MEMBER_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        load_orthogroup_members,
        orthogroup,
        genome=genome,
        limit=limit,
        offset=offset,
    )

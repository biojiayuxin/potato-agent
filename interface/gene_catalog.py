from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import unicodedata
import zlib
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


DEFAULT_DB_PATH = Path("/srv/gene_catalog/current/gene_catalog.sqlite")
EXPECTED_SCHEMA_VERSION = 1
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
MAX_SEARCH_OFFSET = 10_000
MAX_SEARCH_QUERY_LENGTH = 256
SEARCH_DESCRIPTION_EXCERPT_LENGTH = 280
MAX_GENE_ID_LENGTH = 256
CATALOG_UNAVAILABLE_DETAIL = "Gene catalog is unavailable."
QUERY_FAILED_DETAIL = "Gene catalog query failed."

IDENTIFIER_TYPES = ("gene_id", "reported_id", "gene_symbol")
PUBLIC_TO_INTERNAL_SEQUENCE_TYPES = {
    "cds": "cds",
    "protein": "protein",
    "genomic": "genomic",
    "promoter": "promoter_atg_upstream_2000",
}
INTERNAL_TO_PUBLIC_SEQUENCE_TYPES = {
    internal: public for public, internal in PUBLIC_TO_INTERNAL_SEQUENCE_TYPES.items()
}
SEQUENCE_TYPE_ORDER = {
    sequence_type: index
    for index, sequence_type in enumerate(PUBLIC_TO_INTERNAL_SEQUENCE_TYPES)
}
REQUIRED_TABLES = frozenset(
    {
        "catalog_metadata",
        "catalog_sources",
        "genes",
        "gene_identifiers",
        "transcripts",
        "gene_descriptions",
        "papers",
        "paper_local_ids",
        "gene_paper_refs",
        "gene_annotations",
        "protein_similarity_hits",
        "transcript_sequences",
    }
)

LOGGER = logging.getLogger("potato_interface.gene_catalog")
router = APIRouter()
STATIC_ROOT = Path(__file__).resolve().parent / "static" / "genes"


def get_database_path() -> Path:
    return Path(os.getenv("GENE_CATALOG_DB_PATH") or DEFAULT_DB_PATH).resolve()


def _catalog_unavailable(exc: Exception | None = None) -> HTTPException:
    if exc is None:
        LOGGER.error("Gene catalog database is unavailable")
    else:
        LOGGER.exception("Gene catalog database is unavailable", exc_info=exc)
    return HTTPException(status_code=503, detail=CATALOG_UNAVAILABLE_DETAIL)


def _query_failed(exc: Exception) -> HTTPException:
    LOGGER.exception("Gene catalog query failed", exc_info=exc)
    return HTTPException(status_code=500, detail=QUERY_FAILED_DETAIL)


def connect_db() -> sqlite3.Connection:
    db_path = get_database_path()
    if not db_path.is_file():
        raise _catalog_unavailable()

    conn: sqlite3.Connection | None = None
    try:
        uri = f"{db_path.as_uri()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")

        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        tables = {str(row["name"]) for row in table_rows}
        if not REQUIRED_TABLES.issubset(tables):
            raise ValueError("gene catalog schema is incomplete")

        metadata = conn.execute(
            "SELECT schema_version, counts_json FROM catalog_metadata WHERE singleton = 1"
        ).fetchone()
        if metadata is None or int(metadata["schema_version"]) != EXPECTED_SCHEMA_VERSION:
            raise ValueError("unsupported gene catalog schema version")
        counts = json.loads(str(metadata["counts_json"] or "{}"))
        if not isinstance(counts, dict):
            raise ValueError("invalid gene catalog counts metadata")
        return conn
    except Exception as exc:
        if conn is not None:
            conn.close()
        raise _catalog_unavailable(exc) from exc


def _metadata_row(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT
          schema_version,
          catalog_version,
          dataset_id,
          assembly,
          built_at,
          counts_json,
          prediction_release,
          expression_statistic
        FROM catalog_metadata
        WHERE singleton = 1
        """
    ).fetchone()
    if row is None:
        raise ValueError("gene catalog metadata is missing")
    return row


def _camelize_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _metadata_payload(row: sqlite3.Row) -> dict[str, Any]:
    counts = json.loads(str(row["counts_json"] or "{}"))
    if not isinstance(counts, dict):
        raise ValueError("invalid gene catalog counts metadata")
    return {
        "schemaVersion": int(row["schema_version"]),
        "catalogVersion": str(row["catalog_version"] or ""),
        "datasetId": str(row["dataset_id"] or ""),
        "assembly": str(row["assembly"] or ""),
        "builtAt": str(row["built_at"] or ""),
        "counts": {_camelize_key(str(key)): value for key, value in counts.items()},
        "predictionRelease": str(row["prediction_release"] or ""),
        "expressionStatistic": str(row["expression_statistic"] or ""),
        "search": {
            "defaultLimit": DEFAULT_SEARCH_LIMIT,
            "maxLimit": MAX_SEARCH_LIMIT,
        },
        "sequenceTypes": list(PUBLIC_TO_INTERNAL_SEQUENCE_TYPES),
    }


def _normalize_identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.strip().casefold().split())


def _validate_search(query: str, limit: int, offset: int) -> tuple[str, str]:
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required.")
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Query is too long (maximum {MAX_SEARCH_QUERY_LENGTH} characters).",
        )
    if limit < 1 or limit > MAX_SEARCH_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"Limit must be between 1 and {MAX_SEARCH_LIMIT}.",
        )
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset must be zero or greater.")
    if offset > MAX_SEARCH_OFFSET:
        raise HTTPException(
            status_code=400,
            detail=f"Offset must not exceed {MAX_SEARCH_OFFSET}.",
        )
    return query, _normalize_identifier(query)


def _validate_gene_id(gene_id: str) -> str:
    normalized = _normalize_identifier(gene_id)
    if not normalized or len(gene_id.strip()) > MAX_GENE_ID_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid gene id.")
    return normalized


def _location_payload(
    *, seqid: Any, start: Any, end: Any, strand: Any
) -> dict[str, Any] | None:
    if seqid in (None, "") and start is None and end is None and strand in (None, ""):
        return None
    return {
        "seqid": str(seqid or ""),
        "start": int(start) if start is not None else None,
        "end": int(end) if end is not None else None,
        "strand": str(strand or ""),
    }


def _qualifier_payload(row: sqlite3.Row) -> dict[str, Any] | None:
    qualifier_type = str(row["qualifier_type"] or "").strip()
    if not qualifier_type:
        return None
    return {
        "type": qualifier_type,
        "value": row["qualifier_value"],
    }


def _load_identifier_payloads(
    conn: sqlite3.Connection, gene_pks: list[int]
) -> dict[int, dict[str, Any]]:
    payloads = {
        gene_pk: {"symbols": [], "reportedIds": []} for gene_pk in gene_pks
    }
    if not gene_pks:
        return payloads

    placeholders = ",".join("?" for _ in gene_pks)
    rows = conn.execute(
        f"""
        SELECT
          gene_pk,
          identifier_type,
          identifier,
          qualifier_type,
          qualifier_value
        FROM gene_identifiers
        WHERE gene_pk IN ({placeholders})
          AND identifier_type IN ('gene_symbol', 'reported_id')
        ORDER BY gene_pk, display_order, identifier_norm
        """,
        gene_pks,
    ).fetchall()
    seen_symbols: dict[int, set[str]] = {gene_pk: set() for gene_pk in gene_pks}
    seen_reported: dict[int, set[tuple[str, str, str]]] = {
        gene_pk: set() for gene_pk in gene_pks
    }
    for row in rows:
        gene_pk = int(row["gene_pk"])
        identifier = str(row["identifier"] or "")
        if row["identifier_type"] == "gene_symbol":
            if identifier not in seen_symbols[gene_pk]:
                payloads[gene_pk]["symbols"].append(identifier)
                seen_symbols[gene_pk].add(identifier)
            continue

        qualifier = _qualifier_payload(row)
        qualifier_key = (
            identifier,
            str((qualifier or {}).get("type") or ""),
            str((qualifier or {}).get("value") or ""),
        )
        if qualifier_key in seen_reported[gene_pk]:
            continue
        payloads[gene_pk]["reportedIds"].append(
            {"identifier": identifier, "qualifier": qualifier}
        )
        seen_reported[gene_pk].add(qualifier_key)
    return payloads


def _description_excerpt(value: Any) -> tuple[str, bool]:
    description = " ".join(str(value or "").split())
    if len(description) <= SEARCH_DESCRIPTION_EXCERPT_LENGTH:
        return description, False
    excerpt = description[: SEARCH_DESCRIPTION_EXCERPT_LENGTH - 3].rstrip()
    return f"{excerpt}...", True


def _load_search_descriptions(
    conn: sqlite3.Connection, gene_pks: list[int]
) -> dict[int, tuple[str, bool]]:
    descriptions = {gene_pk: ("", False) for gene_pk in gene_pks}
    if not gene_pks:
        return descriptions

    placeholders = ",".join("?" for _ in gene_pks)
    rows = conn.execute(
        f"""
        SELECT gene_pk, predicted_function
        FROM gene_descriptions
        WHERE gene_pk IN ({placeholders})
        """,
        gene_pks,
    ).fetchall()
    for row in rows:
        descriptions[int(row["gene_pk"])] = _description_excerpt(
            row["predicted_function"]
        )
    return descriptions


def _find_gene(conn: sqlite3.Connection, gene_id: str) -> sqlite3.Row:
    normalized = _validate_gene_id(gene_id)
    row = conn.execute(
        """
        SELECT gene_pk, gene_id, seqid, start, end, strand
        FROM genes
        WHERE gene_id_norm = ?
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Gene not found.")
    return row


def load_catalog() -> dict[str, Any]:
    try:
        with closing(connect_db()) as conn:
            return _metadata_payload(_metadata_row(conn))
    except HTTPException:
        raise
    except Exception as exc:
        raise _query_failed(exc) from exc


def _search_tier(
    conn: sqlite3.Connection,
    *,
    condition_sql: str,
    condition_params: tuple[Any, ...],
    match_rank: int,
    limit: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        WITH matches AS (
          SELECT
            g.gene_pk,
            g.gene_id,
            g.gene_id_norm,
            g.seqid,
            g.start,
            g.end,
            g.strand,
            gi.identifier_type,
            gi.identifier,
            gi.identifier_norm,
            gi.qualifier_type,
            gi.qualifier_value,
            gi.display_order,
            ? AS match_rank,
            CASE gi.identifier_type
              WHEN 'gene_id' THEN 0
              WHEN 'reported_id' THEN 1
              WHEN 'gene_symbol' THEN 2
              ELSE 3
            END AS identifier_rank
          FROM gene_identifiers gi
          JOIN genes g ON g.gene_pk = gi.gene_pk
          WHERE gi.identifier_type IN ('gene_id', 'reported_id', 'gene_symbol')
            AND ({condition_sql})
        ), ranked AS (
          SELECT
            *,
            ROW_NUMBER() OVER (
              PARTITION BY gene_pk
              ORDER BY identifier_rank, display_order, identifier_norm
            ) AS gene_match_rank
          FROM matches
        )
        SELECT *
        FROM ranked
        WHERE gene_match_rank = 1
        ORDER BY identifier_rank, gene_id_norm, gene_pk
        LIMIT ?
        """,
        (match_rank, *condition_params, limit),
    ).fetchall()


def _search_rows(
    conn: sqlite3.Connection, normalized: str, limit: int, offset: int
) -> tuple[list[sqlite3.Row], bool]:
    target_count = offset + limit + 1
    prefix_upper_bound = f"{normalized}\U0010ffff"
    tiers = (
        ("gi.identifier_norm = ?", (normalized,)),
        (
            "gi.identifier_norm >= ? AND gi.identifier_norm < ? "
            "AND gi.identifier_norm <> ?",
            (normalized, prefix_upper_bound, normalized),
        ),
    )

    selected: list[sqlite3.Row] = []
    seen_gene_pks: set[int] = set()
    for match_rank, (condition_sql, condition_params) in enumerate(tiers):
        rows = _search_tier(
            conn,
            condition_sql=condition_sql,
            condition_params=condition_params,
            match_rank=match_rank,
            limit=target_count + len(seen_gene_pks),
        )
        for row in rows:
            gene_pk = int(row["gene_pk"])
            if gene_pk in seen_gene_pks:
                continue
            selected.append(row)
            seen_gene_pks.add(gene_pk)
            if len(selected) >= target_count:
                break
        if len(selected) >= target_count:
            break

    page = selected[offset : offset + limit]
    return page, len(selected) > offset + limit


def search_genes(q: str, limit: int, offset: int) -> dict[str, Any]:
    query, normalized = _validate_search(q, limit, offset)
    try:
        with closing(connect_db()) as conn:
            metadata = _metadata_row(conn)
            rows, has_more = _search_rows(conn, normalized, limit, offset)
            gene_pks = [int(row["gene_pk"]) for row in rows]
            identifiers = _load_identifier_payloads(conn, gene_pks)
            descriptions = _load_search_descriptions(conn, gene_pks)
            return {
                "catalogVersion": str(metadata["catalog_version"] or ""),
                "assembly": str(metadata["assembly"] or ""),
                "query": query,
                "limit": limit,
                "offset": offset,
                "hasMore": has_more,
                "genes": [
                    {
                        "geneId": str(row["gene_id"]),
                        "symbols": identifiers[int(row["gene_pk"])]["symbols"],
                        "reportedIds": identifiers[int(row["gene_pk"])][
                            "reportedIds"
                        ],
                        "descriptionExcerpt": descriptions[int(row["gene_pk"])][0],
                        "descriptionIsTruncated": descriptions[int(row["gene_pk"])][1],
                        "location": _location_payload(
                            seqid=row["seqid"],
                            start=row["start"],
                            end=row["end"],
                            strand=row["strand"],
                        ),
                        "matchedBy": str(row["identifier_type"]),
                        "matchedIdentifier": str(row["identifier"]),
                        "qualifier": _qualifier_payload(row),
                    }
                    for row in rows
                ],
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise _query_failed(exc) from exc


def _load_transcripts(
    conn: sqlite3.Connection, gene_pk: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    transcript_rows = conn.execute(
        """
        SELECT
          transcript_pk,
          transcript_id,
          is_representative,
          display_order,
          seqid,
          start,
          end,
          strand
        FROM transcripts
        WHERE gene_pk = ?
        ORDER BY display_order, transcript_id_norm
        """,
        (gene_pk,),
    ).fetchall()
    sequence_rows = conn.execute(
        """
        SELECT
          t.transcript_pk,
          t.transcript_id,
          ts.sequence_type,
          ts.sequence_length,
          ts.sha256
        FROM transcript_sequences ts
        JOIN transcripts t ON t.transcript_pk = ts.transcript_pk
        WHERE t.gene_pk = ?
        ORDER BY t.display_order, t.transcript_id_norm, ts.sequence_type
        """,
        (gene_pk,),
    ).fetchall()

    availability: list[dict[str, Any]] = []
    types_by_transcript: dict[int, list[str]] = {
        int(row["transcript_pk"]): [] for row in transcript_rows
    }
    for row in sequence_rows:
        public_type = INTERNAL_TO_PUBLIC_SEQUENCE_TYPES.get(str(row["sequence_type"]))
        if public_type is None:
            continue
        transcript_pk = int(row["transcript_pk"])
        types_by_transcript.setdefault(transcript_pk, []).append(public_type)
        availability.append(
            {
                "transcriptId": str(row["transcript_id"]),
                "sequenceType": public_type,
                "length": int(row["sequence_length"]),
                "sha256": str(row["sha256"]),
            }
        )

    for sequence_types in types_by_transcript.values():
        sequence_types.sort(key=lambda value: SEQUENCE_TYPE_ORDER[value])
    availability.sort(
        key=lambda item: (
            next(
                (
                    int(row["display_order"])
                    for row in transcript_rows
                    if row["transcript_id"] == item["transcriptId"]
                ),
                0,
            ),
            SEQUENCE_TYPE_ORDER[item["sequenceType"]],
        )
    )
    transcripts = [
        {
            "transcriptId": str(row["transcript_id"]),
            "isRepresentative": bool(row["is_representative"]),
            "location": _location_payload(
                seqid=row["seqid"],
                start=row["start"],
                end=row["end"],
                strand=row["strand"],
            ),
            "sequenceTypes": types_by_transcript.get(int(row["transcript_pk"]), []),
        }
        for row in transcript_rows
    ]
    return transcripts, availability


def _description_row(conn: sqlite3.Connection, gene_pk: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT
          gd.transcript_pk,
          t.transcript_id,
          gd.predicted_function,
          gd.reliability_grade,
          gd.grade_reason,
          gd.evidence_json,
          gd.source_run,
          gd.source_key
        FROM gene_descriptions gd
        LEFT JOIN transcripts t ON t.transcript_pk = gd.transcript_pk
        WHERE gd.gene_pk = ?
        ORDER BY
          CASE
            WHEN gd.transcript_pk IS NULL THEN 0
            WHEN t.is_representative = 1 THEN 1
            ELSE 2
          END,
          t.display_order,
          t.transcript_id_norm
        LIMIT 1
        """,
        (gene_pk,),
    ).fetchone()


def _description_summary(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "transcriptId": str(row["transcript_id"] or ""),
        "predictedFunction": str(row["predicted_function"] or ""),
        "reliabilityGrade": str(row["reliability_grade"] or ""),
    }


def _load_papers(conn: sqlite3.Connection, gene_pk: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          gpr.display_order,
          pli.local_paper_id,
          p.paper_pk,
          p.doi,
          p.doi_norm,
          p.title
        FROM gene_paper_refs gpr
        JOIN paper_local_ids pli ON pli.local_paper_id = gpr.local_paper_id
        JOIN papers p ON p.paper_pk = pli.paper_pk
        WHERE gpr.gene_pk = ?
        ORDER BY gpr.display_order, pli.local_paper_id
        """,
        (gene_pk,),
    ).fetchall()
    papers: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["doi_norm"] or "").strip() or f"paper:{row['paper_pk']}"
        paper = papers.setdefault(
            key,
            {
                "doi": str(row["doi"] or ""),
                "title": str(row["title"] or ""),
                "localPaperIds": [],
            },
        )
        local_id = str(row["local_paper_id"] or "")
        if local_id and local_id not in paper["localPaperIds"]:
            paper["localPaperIds"].append(local_id)
    return list(papers.values())


def _load_annotations(conn: sqlite3.Connection, gene_pk: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT go_terms_json, kegg_terms_json, interpro_json
        FROM gene_annotations
        WHERE gene_pk = ?
        LIMIT 1
        """,
        (gene_pk,),
    ).fetchone()
    if row is None:
        return {"goTerms": [], "keggTerms": [], "interpro": []}
    raw_payload = {
        "goTerms": json.loads(str(row["go_terms_json"] or "[]")),
        "keggTerms": json.loads(str(row["kegg_terms_json"] or "[]")),
        "interpro": json.loads(str(row["interpro_json"] or "[]")),
    }
    if not all(isinstance(value, list) for value in raw_payload.values()):
        raise ValueError("invalid gene annotation JSON")

    payload: dict[str, list[dict[str, Any]]] = {}
    for key, terms in raw_payload.items():
        normalized_terms: list[dict[str, Any]] = []
        for term in terms:
            if isinstance(term, str) and term:
                normalized_terms.append({"id": term})
            elif isinstance(term, dict) and str(term.get("id") or ""):
                normalized_terms.append(dict(term))
            else:
                raise ValueError("invalid gene annotation term")
        payload[key] = normalized_terms
    return payload


def _load_similarity_hits(
    conn: sqlite3.Connection, gene_pk: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          database_name,
          subject_id,
          identity_pct,
          e_value,
          description,
          display_order
        FROM protein_similarity_hits
        WHERE gene_pk = ?
        ORDER BY display_order, subject_id
        """,
        (gene_pk,),
    ).fetchall()
    return [
        {
            "databaseName": str(row["database_name"] or ""),
            "subjectId": str(row["subject_id"] or ""),
            "identityPct": float(row["identity_pct"]),
            "eValue": float(row["e_value"]),
            "description": str(row["description"] or ""),
            "displayOrder": int(row["display_order"]),
        }
        for row in rows
    ]


def _description_evidence(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    evidence = json.loads(str(row["evidence_json"] or "{}"))
    if not isinstance(evidence, dict):
        raise ValueError("invalid gene description evidence JSON")
    return evidence


def _expression_from_description(row: sqlite3.Row | None) -> dict[str, Any]:
    evidence = _description_evidence(row)
    expression_rows = evidence.get("expression_by_tissue") or []
    if not isinstance(expression_rows, list) or not all(
        isinstance(item, dict) for item in expression_rows
    ):
        raise ValueError("invalid gene description expression evidence")
    return {
        "statistic": str(evidence.get("expression_statistic") or ""),
        "tissues": [
            {
                "tissue": str(item.get("tissue") or ""),
                "meanTpm": float(item.get("mean_tpm") or 0.0),
                "sdTpm": float(item.get("sd_tpm") or 0.0),
                "nSources": int(item.get("n_sources") or 0),
                "nRuns": int(item.get("n_runs") or 0),
            }
            for item in expression_rows
        ],
    }


def load_gene_detail(gene_id: str) -> dict[str, Any]:
    try:
        with closing(connect_db()) as conn:
            metadata = _metadata_row(conn)
            gene = _find_gene(conn, gene_id)
            gene_pk = int(gene["gene_pk"])
            identifiers = _load_identifier_payloads(conn, [gene_pk])[gene_pk]
            transcripts, availability = _load_transcripts(conn, gene_pk)
            description = _description_row(conn, gene_pk)
            return {
                "catalogVersion": str(metadata["catalog_version"] or ""),
                "assembly": str(metadata["assembly"] or ""),
                "gene": {
                    "geneId": str(gene["gene_id"]),
                    "symbols": identifiers["symbols"],
                    "reportedIds": identifiers["reportedIds"],
                    "location": _location_payload(
                        seqid=gene["seqid"],
                        start=gene["start"],
                        end=gene["end"],
                        strand=gene["strand"],
                    ),
                },
                "transcripts": transcripts,
                "description": _description_summary(description),
                "papers": _load_papers(conn, gene_pk),
                "annotations": _load_annotations(conn, gene_pk),
                "proteinSimilarityHits": _load_similarity_hits(conn, gene_pk),
                "expression": _expression_from_description(description),
                "sequenceAvailability": availability,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise _query_failed(exc) from exc


def load_description_evidence(gene_id: str) -> dict[str, Any]:
    try:
        with closing(connect_db()) as conn:
            metadata = _metadata_row(conn)
            gene = _find_gene(conn, gene_id)
            description = _description_row(conn, int(gene["gene_pk"]))
            if description is None:
                raise HTTPException(
                    status_code=404,
                    detail="Gene description evidence not found.",
                )
            evidence = _description_evidence(description)
            return {
                "catalogVersion": str(metadata["catalog_version"] or ""),
                "assembly": str(metadata["assembly"] or ""),
                "geneId": str(gene["gene_id"]),
                "transcriptId": str(description["transcript_id"] or ""),
                "predictedFunction": str(description["predicted_function"] or ""),
                "reliabilityGrade": str(description["reliability_grade"] or ""),
                "gradeReason": str(description["grade_reason"] or ""),
                "sourceRun": str(description["source_run"] or ""),
                "sourceKey": str(description["source_key"] or ""),
                "evidence": evidence,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise _query_failed(exc) from exc


def _find_transcript(
    conn: sqlite3.Connection, gene_pk: int, transcript_id: str
) -> sqlite3.Row | None:
    normalized = _normalize_identifier(transcript_id)
    if transcript_id.strip():
        if len(transcript_id.strip()) > MAX_GENE_ID_LENGTH:
            raise HTTPException(status_code=400, detail="Invalid transcript id.")
        row = conn.execute(
            """
            SELECT transcript_pk, transcript_id
            FROM transcripts
            WHERE gene_pk = ? AND transcript_id_norm = ?
            LIMIT 1
            """,
            (gene_pk, normalized),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Transcript not found.")
        return row
    return conn.execute(
        """
        SELECT transcript_pk, transcript_id
        FROM transcripts
        WHERE gene_pk = ?
        ORDER BY is_representative DESC, display_order, transcript_id_norm
        LIMIT 1
        """,
        (gene_pk,),
    ).fetchone()


def load_sequence(
    gene_id: str, sequence_type: str, transcript_id: str = ""
) -> dict[str, Any]:
    public_type = sequence_type.strip().casefold()
    internal_type = PUBLIC_TO_INTERNAL_SEQUENCE_TYPES.get(public_type)
    if internal_type is None:
        raise HTTPException(status_code=400, detail="Unsupported sequence type.")
    try:
        with closing(connect_db()) as conn:
            metadata = _metadata_row(conn)
            gene = _find_gene(conn, gene_id)
            transcript = _find_transcript(conn, int(gene["gene_pk"]), transcript_id)
            if transcript is None:
                raise HTTPException(status_code=404, detail="Sequence not found.")
            row = conn.execute(
                """
                SELECT
                  sequence_zlib,
                  sequence_length,
                  sha256,
                  seqid,
                  region_start,
                  region_end,
                  strand,
                  anchor_type,
                  anchor_pos,
                  requested_length,
                  was_truncated
                FROM transcript_sequences
                WHERE transcript_pk = ? AND sequence_type = ?
                LIMIT 1
                """,
                (int(transcript["transcript_pk"]), internal_type),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Sequence not found.")

            compressed = row["sequence_zlib"]
            if isinstance(compressed, memoryview):
                compressed = compressed.tobytes()
            sequence_bytes = zlib.decompress(bytes(compressed))
            sequence = sequence_bytes.decode("ascii")
            expected_length = int(row["sequence_length"])
            expected_sha256 = str(row["sha256"] or "").casefold()
            if len(sequence) != expected_length:
                raise ValueError("gene catalog sequence length mismatch")
            if hashlib.sha256(sequence_bytes).hexdigest() != expected_sha256:
                raise ValueError("gene catalog sequence checksum mismatch")

            location = _location_payload(
                seqid=row["seqid"],
                start=row["region_start"],
                end=row["region_end"],
                strand=row["strand"],
            )
            anchor_type = str(row["anchor_type"] or "").strip()
            anchor = None
            if anchor_type or row["anchor_pos"] is not None:
                anchor = {
                    "type": anchor_type,
                    "position": (
                        int(row["anchor_pos"])
                        if row["anchor_pos"] is not None
                        else None
                    ),
                    "requestedLength": (
                        int(row["requested_length"])
                        if row["requested_length"] is not None
                        else None
                    ),
                    "wasTruncated": bool(row["was_truncated"]),
                }
            return {
                "catalogVersion": str(metadata["catalog_version"] or ""),
                "assembly": str(metadata["assembly"] or ""),
                "geneId": str(gene["gene_id"]),
                "transcriptId": str(transcript["transcript_id"]),
                "sequenceType": public_type,
                "sequence": sequence,
                "length": expected_length,
                "sha256": expected_sha256,
                "location": location,
                "anchor": anchor,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise _query_failed(exc) from exc


@router.get("/genes", include_in_schema=False)
@router.get("/genes/{gene_id}", include_in_schema=False)
async def serve_genes_index(gene_id: str = "") -> FileResponse:
    del gene_id
    index_path = STATIC_ROOT / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Genes frontend not found")
    return FileResponse(index_path)


@router.get("/api/v1/gene-catalog")
async def api_gene_catalog() -> dict[str, Any]:
    return await asyncio.to_thread(load_catalog)


@router.get("/api/v1/genes/search")
async def api_search_genes(
    q: str = "", limit: int = DEFAULT_SEARCH_LIMIT, offset: int = 0
) -> dict[str, Any]:
    return await asyncio.to_thread(search_genes, q, limit, offset)


@router.get("/api/v1/genes/{gene_id}/description/evidence")
async def api_gene_description_evidence(gene_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(load_description_evidence, gene_id)


@router.get("/api/v1/genes/{gene_id}/sequences/{sequence_type}")
async def api_gene_sequence(
    gene_id: str, sequence_type: str, transcript_id: str = ""
) -> dict[str, Any]:
    return await asyncio.to_thread(load_sequence, gene_id, sequence_type, transcript_id)


@router.get("/api/v1/genes/{gene_id}")
async def api_gene_detail(gene_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(load_gene_detail, gene_id)

"""Public, immutable genome annotations shared by the portal and agent clients."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

router = APIRouter()
LOGGER = logging.getLogger(__name__)
STATIC_ROOT = Path(__file__).resolve().parent / "static" / "functional_annotation"
DEFAULT_DB_PATH = Path("/srv/genome_annotations/current/genome_annotations.sqlite")
SCHEMA_VERSION = 3
MAX_IDS = 5_000
MAX_LIMIT = 500
TABLES = ("genes", "transcripts", "domains", "tf_decisions", "tf_evidence")
REQUIRED_TABLES = {
    "metadata", "assemblies", "genes", "transcripts", "proteins", "matches",
    "matches_fts", "protein_terms", "protein_families", "tf_evidence",
    "tf_families", "tf_counts", "downloads",
}


def _identifier(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Identifiers must contain 1–512 printable characters.")
    return value


class AnnotationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assemblyIds: list[str] = Field(default_factory=list, max_length=100)
    view: Literal["genes", "transcripts"] = "genes"
    ids: list[str] = Field(default_factory=list, max_length=MAX_IDS)
    q: str = Field(default="", max_length=256)
    analyses: list[Literal["CDD", "PANTHER", "Pfam", "SMART"]] = Field(default_factory=list, max_length=4)
    signatures: list[str] = Field(default_factory=list, max_length=100)
    interproIds: list[str] = Field(default_factory=list, max_length=100)
    goIds: list[str] = Field(default_factory=list, max_length=100)
    domainMode: Literal["all", "any"] = "all"
    tfFamilies: list[str] = Field(default_factory=list, max_length=100)
    grades: list[Literal["A", "B", "C", "U"]] = Field(default_factory=list, max_length=4)
    tfStatus: Literal["all", "selected", "not_selected", "ambiguous", "unassessable"] = "all"
    annotationStatus: Literal["all", "hit", "no_match", "no_cds"] = "all"
    conflict: Literal["all", "presence", "family", "any"] = "all"
    limit: int = Field(default=50, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0, le=10_000_000)
    datasetVersion: str | None = Field(default=None, max_length=200)

    @field_validator("assemblyIds", "ids", "signatures", "interproIds", "goIds", "tfFamilies")
    @classmethod
    def identifiers(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_identifier(value) for value in values))

    @field_validator("analyses", "grades")
    @classmethod
    def distinct_choices(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @field_validator("q")
    @classmethod
    def keyword(cls, value: str) -> str:
        value = value.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid keyword.")
        return value


class SelectedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assemblyId: str
    geneId: str | None = None
    transcriptId: str | None = None

    @model_validator(mode="after")
    def check_identity(self):
        self.assemblyId = _identifier(self.assemblyId)
        if bool(self.geneId) == bool(self.transcriptId):
            raise ValueError("Specify exactly one geneId or transcriptId.")
        if self.geneId:
            self.geneId = _identifier(self.geneId)
        if self.transcriptId:
            self.transcriptId = _identifier(self.transcriptId)
        return self


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: AnnotationQuery = Field(default_factory=AnnotationQuery)
    tables: list[Literal["genes", "transcripts", "domains", "tf_decisions", "tf_evidence"]] = Field(
        default_factory=lambda: ["genes"], min_length=1, max_length=5,
    )
    format: Literal["tsv", "csv"] = "tsv"
    selection: list[SelectedRecord] = Field(default_factory=list, max_length=MAX_IDS)
    datasetVersion: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def selection_matches_view(self):
        if any(bool(item.geneId) != (self.query.view == "genes") for item in self.selection):
            raise ValueError("Selection identities must match the query view.")
        return self


def database_path() -> Path:
    return Path(os.getenv("GENOME_ANNOTATIONS_DB_PATH") or DEFAULT_DB_PATH).resolve()


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    path = path or database_path()
    conn = None
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        if not REQUIRED_TABLES.issubset(tables):
            raise ValueError("Incomplete schema")
        row = conn.execute("SELECT schema_version FROM metadata WHERE singleton=1").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise ValueError("Unsupported schema")
        return conn
    except (sqlite3.Error, ValueError, OSError):
        if conn is not None:
            conn.close()
        LOGGER.error("Genome annotation database is unavailable")
        raise HTTPException(503, "Genome annotation database is unavailable.") from None


def _json(value: str | None) -> dict:
    return json.loads(value or "{}")


def _values(value: str | None) -> list[str]:
    return [x for x in (value or "").split(";") if x and x != "-"]


def _truth(value: Any) -> bool:
    return value is True or str(value).lower() in {"true", "1"}


def _marks(values) -> str:
    return ",".join("?" for _ in values)


def _version(conn: sqlite3.Connection, expected: str | None = None) -> str:
    version = conn.execute("SELECT dataset_version FROM metadata WHERE singleton=1").fetchone()[0]
    if expected is not None and version != expected:
        raise HTTPException(409, "The annotation dataset has changed. Refresh the query before exporting.")
    return version


def _assemblies(conn: sqlite3.Connection) -> list[dict]:
    return [{"assemblyId": r["assembly_id"], "browserAssemblyId": r["assembly_id"],
             "label": r["label"], "stats": _json(r["stats_json"])}
            for r in conn.execute("SELECT * FROM assemblies ORDER BY display_order")]


def metadata() -> dict:
    with closing(connect_db()) as conn:
        row = conn.execute("SELECT * FROM metadata WHERE singleton=1").fetchone()
        result = _json(row["metadata_json"])
        result.update(schemaVersion=row["schema_version"], datasetVersion=row["dataset_version"],
                      builtAt=row["built_at"], assemblies=_assemblies(conn),
                      limits={"maxIds": MAX_IDS, "maxLimit": MAX_LIMIT, "defaultLimit": 50})
        return result


def _validate_catalog(conn: sqlite3.Connection, query: AnnotationQuery) -> None:
    _version(conn, query.datasetVersion)
    known = {r[0] for r in conn.execute("SELECT assembly_id FROM assemblies")}
    if set(query.assemblyIds) - known:
        raise HTTPException(400, "Unknown assembly ID; use the canonical IDs from metadata.")
    if query.tfFamilies:
        known_families = {r[0] for r in conn.execute("SELECT family FROM tf_families")}
        if set(query.tfFamilies) - known_families:
            raise HTTPException(400, "Unknown TF family.")


def _match_sql(query: AnnotationQuery, selection: list[SelectedRecord] | None = None) -> tuple[str, list]:
    """All protein-level predicates apply to this one transcript, before gene grouping."""
    conditions, args = [], []
    if query.assemblyIds:
        conditions.append(f"t.assembly_id IN ({_marks(query.assemblyIds)})")
        args.extend(query.assemblyIds)
    if query.ids:
        marks = _marks(query.ids)
        conditions.append(f"(t.gene_id IN ({marks}) OR t.transcript_id IN ({marks}) OR t.protein_id IN ({marks}))")
        args.extend(query.ids * 3)
    if query.q:
        # Description keywords use FTS; exact identifiers have the separate ids field.
        words = re.findall(r"[\w]+", query.q, flags=re.UNICODE)
        if not words:
            raise HTTPException(400, "Enter an identifier or a word to search.")
        fts = " AND ".join('"' + word + '"' for word in words)
        predicate = "matches_fts MATCH ?"
        args.append(fts)
        if query.analyses:
            predicate += f" AND m.analysis IN ({_marks(query.analyses)})"
            args.extend(query.analyses)
        conditions.append("t.protein_id IN (SELECT m.protein_id FROM matches_fts JOIN matches m "
                          "ON m.hit_id=matches_fts.rowid WHERE " + predicate + ")")
    if query.analyses and not (query.q or query.signatures or query.interproIds or query.goIds):
        conditions.append(f"t.protein_id IN (SELECT m.protein_id FROM matches m WHERE m.analysis IN ({_marks(query.analyses)}))")
        args.extend(query.analyses)
    domain_conditions = []
    for kind, terms in (("signature", query.signatures), ("interpro", query.interproIds)):
        for term in terms:
            column = "signature_accession" if kind == "signature" else "interpro_accession"
            predicate = f"m.{column}=?"
            args.append(term)
            if query.analyses:
                predicate += f" AND m.analysis IN ({_marks(query.analyses)})"
                args.extend(query.analyses)
            domain_conditions.append("t.protein_id IN (SELECT m.protein_id FROM matches m WHERE " + predicate + ")")
    if domain_conditions:
        conditions.append("(" + (" AND " if query.domainMode == "all" else " OR ").join(domain_conditions) + ")")
    if query.goIds:
        conditions.append(f"t.protein_id IN (SELECT pt.protein_id FROM protein_terms pt WHERE pt.kind='go' AND pt.term IN ({_marks(query.goIds)}))")
        args.extend(query.goIds)
        if query.analyses:
            alternatives = []
            args.extend(query.analyses)
            for term in query.goIds:
                alternatives.append("(instr('|' || m.go_terms, '|' || ? || '(')>0 OR instr('|' || m.go_terms || '|', '|' || ? || '|')>0)")
                args.extend([term, term])
            conditions.append(f"EXISTS (SELECT 1 FROM matches m WHERE m.protein_id=t.protein_id AND m.analysis IN ({_marks(query.analyses)}) AND (" + " OR ".join(alternatives) + "))")
    if query.tfFamilies or query.grades:
        predicates = []
        if query.tfFamilies:
            predicates.append(f"pf.family IN ({_marks(query.tfFamilies)})")
            args.extend(query.tfFamilies)
        if query.grades:
            predicates.append(f"pf.grade IN ({_marks(query.grades)})")
            args.extend(query.grades)
        conditions.append("t.protein_id IN (SELECT pf.protein_id FROM protein_families pf WHERE " + " AND ".join(predicates) + ")")
    if query.tfStatus == "selected":
        conditions.append("p.is_tf=1")
    elif query.tfStatus == "not_selected":
        conditions.append("p.decision_status='not_selected'")
    elif query.tfStatus == "ambiguous":
        conditions.append("p.decision_status='ambiguous_multifamily'")
    elif query.tfStatus == "unassessable":
        conditions.append("t.status='no_cds'")
    if query.annotationStatus == "hit":
        conditions.append("p.has_hit=1")
    elif query.annotationStatus == "no_match":
        conditions.append("t.status='valid' AND p.has_hit=0")
    elif query.annotationStatus == "no_cds":
        conditions.append("t.status='no_cds'")
    if query.conflict == "presence":
        conditions.append("g.isoform_presence_conflict=1")
    elif query.conflict == "family":
        conditions.append("g.family_conflict=1")
    elif query.conflict == "any":
        conditions.append("(g.isoform_presence_conflict=1 OR g.family_conflict=1)")
    if selection:
        # Group rows to avoid SQLite's expression-depth limit with thousands of selections.
        groups: dict[tuple[str, str], list[str]] = {}
        for item in selection:
            column = "gene_id" if item.geneId else "transcript_id"
            groups.setdefault((item.assemblyId, column), []).append(item.geneId or item.transcriptId)
        alternatives = []
        for (assembly, column), ids in groups.items():
            alternatives.append(f"(t.assembly_id=? AND t.{column} IN ({_marks(ids)}))")
            args.extend([assembly, *ids])
        conditions.append("(" + " OR ".join(alternatives) + ")")
    where = " AND ".join(conditions) or "1"
    joins = ""
    if query.conflict != "all":
        joins += " JOIN genes g ON g.assembly_id=t.assembly_id AND g.gene_id=t.gene_id"
    if query.tfStatus in {"selected", "not_selected", "ambiguous"} or query.annotationStatus in {"hit", "no_match"}:
        joins += " LEFT JOIN proteins p ON p.protein_id=t.protein_id"
    return ("SELECT t.assembly_id,t.transcript_id,t.gene_id,t.protein_id,t.status,t.reason FROM transcripts t "
            + joins + " WHERE " + where), args


def _id_report(conn: sqlite3.Connection, query: AnnotationQuery, sql: str, args: list) -> dict:
    report = {"unmatchedIds": [], "ambiguousIds": [], "filteredIds": []}
    if not query.ids:
        return report
    raw = AnnotationQuery(assemblyIds=query.assemblyIds, ids=query.ids)
    raw_sql, raw_args = _match_sql(raw)
    associations = {identifier: set() for identifier in query.ids}
    wanted = set(query.ids)
    for row in conn.execute(raw_sql, raw_args):
        for identifier in {row["gene_id"], row["transcript_id"], row["protein_id"]} & wanted:
            associations[identifier].add((row["assembly_id"], row["gene_id"]))
    matching = set()
    for row in conn.execute(sql, args):
        matching.update({row["gene_id"], row["transcript_id"], row["protein_id"]} & wanted)
    for identifier in query.ids:
        matches = associations[identifier]
        if not matches:
            report["unmatchedIds"].append(identifier)
        elif identifier not in matching:
            report["filteredIds"].append(identifier)
        if len(matches) > 1:
            report["ambiguousIds"].append(identifier)
    return report


def _transcript(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    if "data_json" in row.keys():
        data = _json(row["data_json"])
    else:
        data = _json(conn.execute("SELECT data_json FROM transcripts WHERE assembly_id=? AND transcript_id=?",
                                 (row["assembly_id"], row["transcript_id"])).fetchone()[0])
    protein = conn.execute("SELECT * FROM proteins WHERE protein_id=?", (row["protein_id"],)).fetchone()
    decision = _json(protein["data_json"]) if protein else {}
    signatures = [r[0] for r in conn.execute("SELECT DISTINCT signature_accession FROM matches WHERE protein_id=? ORDER BY signature_accession", (row["protein_id"],))]
    return {
        "assemblyId": row["assembly_id"], "geneId": row["gene_id"], "transcriptId": row["transcript_id"],
        "proteinId": row["protein_id"], "proteinLength": protein["protein_length"] if protein else None,
        "annotationStatus": "no_cds" if row["status"] == "no_cds" else ("hit" if protein and protein["has_hit"] else "no_match"),
        "reason": row["reason"], "decisionStatus": protein["decision_status"] if protein else "unassessable_no_valid_protein",
        "isTf": bool(protein and protein["is_tf"]), "selectionBasis": decision.get("selection_basis", ""),
        "tfFamilies": _values(decision.get("tf_families")), "confidenceGrades": sorted(set(_values(decision.get("confidence_grades")))),
        "signatures": signatures[:12], "signatureCount": len(signatures), "source": data,
    }


def _gene(conn: sqlite3.Connection, row: sqlite3.Row, matched_ids: list[str] | None = None) -> dict:
    data = _json(row["data_json"])
    transcripts = conn.execute("""SELECT t.transcript_id,t.protein_id,t.status,p.has_hit FROM transcripts t
        LEFT JOIN proteins p ON p.protein_id=t.protein_id WHERE t.assembly_id=? AND t.gene_id=? ORDER BY t.transcript_id""",
        (row["assembly_id"], row["gene_id"])).fetchall()
    if matched_ids is None:
        matched_ids = [r["transcript_id"] for r in transcripts]
    selected_ids = set(matched_ids)
    proteins = {r["protein_id"] for r in transcripts if r["transcript_id"] in selected_ids and r["protein_id"]}
    statuses = {"no_cds" if r["status"] == "no_cds" else ("hit" if r["has_hit"] else "no_match") for r in transcripts}
    signatures = []
    if proteins:
        signatures = [r[0] for r in conn.execute(f"SELECT DISTINCT signature_accession FROM matches WHERE protein_id IN ({_marks(proteins)}) ORDER BY signature_accession", list(proteins))]
    return {"assemblyId": row["assembly_id"], "geneId": row["gene_id"],
            "transcriptCount": len(transcripts), "matchedTranscriptCount": len(matched_ids), "matchedTranscriptIds": matched_ids,
            "tfFamilies": _values(data.get("tf_family_union")), "confidenceGrades": sorted(set(_values(data.get("confidence_grades")))),
            "geneDecision": row["gene_decision"], "isoformPresenceConflict": bool(row["isoform_presence_conflict"]),
            "familyConflict": bool(row["family_conflict"]), "annotationStatus": next(iter(statuses)) if len(statuses) == 1 else "mixed",
            "signatures": signatures[:12], "signatureCount": len(signatures), "source": data}


def query_annotations(query: AnnotationQuery) -> dict:
    with closing(connect_db()) as conn:
        _validate_catalog(conn, query)
        sql, args = _match_sql(query)
        cte = "WITH matched AS (" + sql + ") "
        gene_only = query.view == "genes" and not any((query.ids, query.q, query.analyses, query.signatures,
            query.interproIds, query.goIds, query.tfFamilies, query.grades,
            query.tfStatus != "all", query.annotationStatus != "all"))
        if gene_only:
            # Browsing does not need to group every isoform before returning the first page.
            conflict = {"all": "1", "presence": "isoform_presence_conflict=1", "family": "family_conflict=1",
                        "any": "(isoform_presence_conflict=1 OR family_conflict=1)"}[query.conflict]
            total, remaining_offset, rows = 0, query.offset, []
            for assembly in _assemblies(conn):
                identifier = assembly["assemblyId"]
                if query.assemblyIds and identifier not in query.assemblyIds:
                    continue
                where = "assembly_id=? AND " + conflict
                count = conn.execute("SELECT count(*) FROM genes WHERE " + where, (identifier,)).fetchone()[0]
                total += count
                if remaining_offset >= count:
                    remaining_offset -= count
                elif len(rows) < query.limit:
                    rows.extend(conn.execute("SELECT * FROM genes WHERE " + where + " ORDER BY gene_id LIMIT ? OFFSET ?",
                                             (identifier, query.limit - len(rows), remaining_offset)))
                    remaining_offset = 0
            items = [_gene(conn, row) for row in rows]
        elif query.view == "genes":
            total = conn.execute(cte + "SELECT count(*) FROM (SELECT 1 FROM matched GROUP BY assembly_id,gene_id)", args).fetchone()[0]
            rows = conn.execute(cte + """SELECT m.assembly_id,m.gene_id,json_group_array(m.transcript_id) AS matched_ids FROM matched m
                JOIN assemblies a ON a.assembly_id=m.assembly_id GROUP BY m.assembly_id,m.gene_id
                ORDER BY a.display_order,m.gene_id LIMIT ? OFFSET ?""", [*args, query.limit, query.offset]).fetchall()
            items = [_gene(conn, conn.execute("SELECT * FROM genes WHERE assembly_id=? AND gene_id=?",
                          (r["assembly_id"], r["gene_id"])).fetchone(), sorted(json.loads(r["matched_ids"]))) for r in rows]
        else:
            total = conn.execute(cte + "SELECT count(*) FROM matched", args).fetchone()[0]
            rows = conn.execute(cte + """SELECT m.* FROM matched m JOIN assemblies a ON a.assembly_id=m.assembly_id
                ORDER BY a.display_order,m.gene_id,m.transcript_id LIMIT ? OFFSET ?""", [*args, query.limit, query.offset]).fetchall()
            items = [_transcript(conn, r) for r in rows]
        return {"datasetVersion": _version(conn), "query": query.model_dump(), "items": items, "total": total,
                "returned": len(items), "limit": query.limit, "offset": query.offset,
                "hasMore": query.offset + len(items) < total, "idReport": _id_report(conn, query, sql, args)}


def gene_detail(assembly: str, identifier: str) -> dict:
    with closing(connect_db()) as conn:
        row = conn.execute("SELECT * FROM genes WHERE assembly_id=? AND gene_id=?", (assembly, identifier)).fetchone()
        if row is None:
            raise HTTPException(404, "Gene not found in this assembly.")
        transcripts = [_transcript(conn, r) for r in conn.execute("SELECT * FROM transcripts WHERE assembly_id=? AND gene_id=? ORDER BY transcript_id", (assembly, identifier))]
        return {"datasetVersion": _version(conn), "gene": _gene(conn, row), "transcripts": transcripts}


def _hit(row: sqlite3.Row) -> dict:
    mapping = {"hit_id": "hitId", "signature_accession": "signatureAccession", "signature_description": "signatureDescription",
               "interpro_accession": "interproAccession", "interpro_description": "interproDescription", "go_terms": "goTerms"}
    return {mapping.get(k, k): row[k] for k in row.keys() if k != "protein_id"}


def transcript_detail(assembly: str, identifier: str) -> dict:
    with closing(connect_db()) as conn:
        row = conn.execute("SELECT * FROM transcripts WHERE assembly_id=? AND transcript_id=?", (assembly, identifier)).fetchone()
        if row is None:
            raise HTTPException(404, "Transcript not found in this assembly.")
        protein = conn.execute("SELECT data_json FROM proteins WHERE protein_id=?", (row["protein_id"],)).fetchone()
        return {"datasetVersion": _version(conn), "transcript": _transcript(conn, row),
                "matches": [_hit(r) for r in conn.execute(
                    "SELECT h.* FROM matches h "
                    "WHERE h.protein_id=? ORDER BY h.analysis,h.start,h.end,h.hit_id", (row["protein_id"],))],
                "tfEvidence": [_json(r[0]) for r in conn.execute("SELECT data_json FROM tf_evidence WHERE protein_id=? ORDER BY evidence_id", (row["protein_id"],))],
                "proteinDecision": _json(protein[0]) if protein else {}}


def families() -> dict:
    with closing(connect_db()) as conn:
        items = []
        for row in conn.execute("SELECT * FROM tf_families ORDER BY family"):
            counts = [{"assemblyId": r["assembly_id"], "genes": r["genes"], "transcripts": r["transcripts"], "proteins": r["proteins"]}
                      for r in conn.execute("SELECT * FROM tf_counts WHERE family=? ORDER BY assembly_id", (row["family"],))]
            items.append({"family": row["family"], "confidenceGrade": row["confidence_grade"],
                          "coverageStatus": row["coverage_status"], "counts": counts, "rule": _json(row["data_json"])})
        return {"datasetVersion": _version(conn), "assemblies": _assemblies(conn), "items": items}


def downloads() -> dict:
    with closing(connect_db()) as conn:
        items = [{"fileId": r["file_id"], "filename": r["filename"], "category": r["category"],
                  "assemblyId": r["assembly_id"], "sizeBytes": r["size_bytes"], "sha256": r["sha256"],
                  "url": "/api/genome-annotations/downloads/" + r["file_id"]}
                 for r in conn.execute("SELECT * FROM downloads ORDER BY category,assembly_id,filename")]
        return {"datasetVersion": _version(conn), "items": items}


def download_file(file_id: str) -> FileResponse:
    db_path = database_path()
    root = db_path.parent
    with closing(connect_db(db_path)) as conn:
        row = conn.execute("SELECT * FROM downloads WHERE file_id=?", (file_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Download not found.")
        path = (root / row["relative_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != row["size_bytes"]:
            raise HTTPException(503, "Annotation download is unavailable.")
        return FileResponse(path, filename=row["filename"], headers={"ETag": '"' + row["sha256"] + '"'})


def _matches_domain_filter(row: sqlite3.Row, query: AnnotationQuery) -> bool:
    has_terms = bool(query.signatures or query.interproIds or query.goIds)
    term_match = (row["signature_accession"] in query.signatures or row["interpro_accession"] in query.interproIds
                  or bool(set(re.findall(r"GO:\d+", row["go_terms"] or "")) & set(query.goIds)))
    return (not query.analyses or row["analysis"] in query.analyses) and (not has_terms or term_match)


def _export_rows(conn: sqlite3.Connection, query: AnnotationQuery, selection: list[SelectedRecord], table: str):
    sql, args = _match_sql(query, selection)
    cte = "WITH matched AS (" + sql + ") "
    if table == "genes":
        statement = """SELECT g.data_json,count(*) AS matched_count,json_group_array(m.transcript_id) AS matched_ids
            FROM matched m JOIN genes g ON g.assembly_id=m.assembly_id AND g.gene_id=m.gene_id
            GROUP BY m.assembly_id,m.gene_id ORDER BY m.assembly_id,m.gene_id"""
        for row in conn.execute(cte + statement, args):
            yield {**_json(row["data_json"]), "matched_transcript_count": row["matched_count"],
                   "matched_transcript_ids": ";".join(sorted(json.loads(row["matched_ids"])))}
    elif table in {"transcripts", "tf_decisions"}:
        statement = """SELECT m.*,p.data_json AS protein_json,p.has_hit,g.data_json AS gene_json FROM matched m
            LEFT JOIN proteins p ON p.protein_id=m.protein_id
            JOIN genes g ON g.assembly_id=m.assembly_id AND g.gene_id=m.gene_id
            ORDER BY m.assembly_id,m.gene_id,m.transcript_id"""
        for row in conn.execute(cte + statement, args):
            decision = _json(row["protein_json"])
            result = {"assembly_id": row["assembly_id"], "gene_id": row["gene_id"], "transcript_id": row["transcript_id"],
                   "global_unique_protein_id": row["protein_id"] or "",
                   "annotation_status": "no_cds" if row["status"] == "no_cds" else ("hit" if row["has_hit"] else "no_match"),
                   "reason": row["reason"],
                   "decision_status": decision.get("decision_status", "unassessable_no_valid_protein"),
                   "is_tf_inclusive_result": decision.get("is_tf_inclusive_result", ""),
                   "selection_basis": decision.get("selection_basis", ""),
                   "tf_families": decision.get("tf_families", ""), "confidence_grades": decision.get("confidence_grades", ""),
                   "unresolved_candidates": decision.get("unresolved_candidates", ""),
                   "relevant_pfam_counts": decision.get("relevant_pfam_counts", ""),
                   "protein_length": decision.get("protein_length", ""),
                   "sequence_md5": decision.get("sequence_md5", ""),
                   "sequence_sha256": decision.get("sequence_sha256", ""),
                   "annotation_origin": decision.get("annotation_origin", ""),
                   "decision_reason": decision.get("decision_reason", "")}
            if table == "tf_decisions":
                gene = _json(row["gene_json"])
                result.update({key: gene.get(key, "") for key in (
                    "gene_decision", "tf_family_union", "tf_family_intersection",
                    "isoform_presence_conflict", "family_conflict",
                )})
            yield result
    elif table == "domains":
        statement = """SELECT m.assembly_id,m.gene_id,m.transcript_id,h.*,p.protein_length,
            json_extract(p.data_json,'$.sequence_md5') AS sequence_md5,
            json_extract(p.data_json,'$.annotation_origin') AS annotation_origin FROM matched m
            JOIN matches h ON h.protein_id=m.protein_id JOIN proteins p ON p.protein_id=m.protein_id
            ORDER BY m.assembly_id,m.gene_id,m.transcript_id,h.hit_id"""
        for row in conn.execute(cte + statement, args):
            yield {**dict(row), "matches_domain_filter": _matches_domain_filter(row, query)}
    else:
        statement = """SELECT m.assembly_id,m.gene_id,m.transcript_id,e.data_json FROM matched m
            JOIN tf_evidence e ON e.protein_id=m.protein_id ORDER BY m.assembly_id,m.gene_id,m.transcript_id,e.evidence_id"""
        for row in conn.execute(cte + statement, args):
            yield {"assembly_id": row["assembly_id"], "gene_id": row["gene_id"], "transcript_id": row["transcript_id"], **_json(row["data_json"])}


EXPORT_FIELDS = {
    "genes": ["assembly_id", "gene_id", "global_gene_key", "gene_decision", "total_transcript_count", "valid_transcript_count",
              "exception_transcript_count", "distinct_protein_count", "selected_tf_transcript_count", "ambiguous_tf_transcript_count",
              "unresolved_transcript_count", "not_selected_transcript_count", "has_selected_tf_isoform", "all_valid_isoforms_selected",
              "tf_family_union", "tf_family_intersection", "confidence_grades", "ambiguous_family_union",
              "isoform_presence_conflict", "family_conflict", "matched_transcript_count", "matched_transcript_ids"],
    "transcripts": ["assembly_id", "gene_id", "transcript_id", "global_unique_protein_id", "annotation_status", "reason",
                    "decision_status", "is_tf_inclusive_result", "selection_basis", "tf_families", "confidence_grades",
                    "unresolved_candidates", "relevant_pfam_counts", "protein_length", "sequence_md5", "sequence_sha256",
                    "annotation_origin", "decision_reason"],
    "domains": ["assembly_id", "gene_id", "transcript_id", "hit_id", "protein_id", "protein_length", "sequence_md5", "annotation_origin", "analysis", "signature_accession",
                "signature_description", "start", "end", "score", "status", "date", "interpro_accession",
                "interpro_description", "go_terms", "matches_domain_filter"],
    "tf_evidence": ["assembly_id", "gene_id", "transcript_id", "global_unique_protein_id", "signature_accession",
                    "signature_description", "start", "end", "score", "status", "interpro_accession", "interpro_description", "rule_roles"],
}
EXPORT_FIELDS["tf_decisions"] = EXPORT_FIELDS["transcripts"] + [
    "gene_decision", "tf_family_union", "tf_family_intersection", "isoform_presence_conflict", "family_conflict",
]


class _ZipBuffer:
    """Unseekable ZIP output drained between rows, with bounded resident memory."""
    def __init__(self):
        self.data = bytearray()
        self.position = 0

    def write(self, data):
        self.data.extend(data)
        self.position += len(data)
        return len(data)

    def tell(self):
        return self.position

    def flush(self):
        pass

    def drain(self):
        data = bytes(self.data)
        self.data.clear()
        return data


def _export_zip(path: Path, request: ExportRequest):
    with closing(connect_db(path)) as conn:
        _version(conn, request.datasetVersion)
        sql, args = _match_sql(request.query, request.selection)
        source = _json(conn.execute("SELECT metadata_json FROM metadata WHERE singleton=1").fetchone()[0])
        manifest = {"datasetVersion": request.datasetVersion, "query": request.query.model_dump(exclude={"limit", "offset"}),
                    "selection": [r.model_dump(exclude_none=True) for r in request.selection], "format": request.format,
                    "tableCounts": {}, "method": source.get("method"), "rulesetVersion": source.get("rulesetVersion"),
                    "limitations": source.get("limitations", []), "coordinateSystem": "Protein amino acids, 1-based inclusive",
                    "domainExportScope": "All hits of matched transcripts; matches_domain_filter marks the requested evidence.",
                    "idReport": _id_report(conn, request.query, sql, args)}
        target = _ZipBuffer()
        delimiter = "\t" if request.format == "tsv" else ","
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for table in dict.fromkeys(request.tables):
                count = 0
                with archive.open(f"{table}.{request.format}", "w", force_zip64=True) as file:
                    buffer = io.StringIO(newline="")
                    writer = csv.DictWriter(buffer, EXPORT_FIELDS[table], delimiter=delimiter, lineterminator="\n", extrasaction="ignore")
                    writer.writeheader()
                    file.write(buffer.getvalue().encode("utf-8"))
                    for row in _export_rows(conn, request.query, request.selection, table):
                        buffer.seek(0)
                        buffer.truncate(0)
                        writer.writerow(row)
                        file.write(buffer.getvalue().encode("utf-8"))
                        count += 1
                        if len(target.data) >= 65_536:
                            yield target.drain()
                manifest["tableCounts"][table] = count
                yield target.drain()
            archive.writestr("metadata.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            report_buffer = io.StringIO(newline="")
            writer = csv.writer(report_buffer, delimiter="\t", lineterminator="\n")
            writer.writerow(["requested_id", "status"])
            for key, status in (("unmatchedIds", "not_found"), ("ambiguousIds", "multiple_genes"), ("filteredIds", "filtered_out")):
                writer.writerows((identifier, status) for identifier in manifest["idReport"][key])
            archive.writestr("id_report.tsv", report_buffer.getvalue())
        yield target.drain()


def prepare_export(request: ExportRequest) -> StreamingResponse:
    path = database_path()  # Resolve the release once; retain it throughout the stream.
    with closing(connect_db(path)) as conn:
        _validate_catalog(conn, request.query)
        _version(conn, request.datasetVersion)
        known = {r[0] for r in conn.execute("SELECT assembly_id FROM assemblies")}
        if any(r.assemblyId not in known for r in request.selection):
            raise HTTPException(400, "Unknown assembly in selection.")
        sql, args = _match_sql(request.query, request.selection)
        conn.execute("SELECT 1 FROM (" + sql + ") LIMIT 1", args).fetchone()
    return StreamingResponse(_export_zip(path, request), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="genome-annotations.zip"',
        "X-Annotation-Dataset-Version": request.datasetVersion, "Cache-Control": "no-store",
    })


async def _run(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except (sqlite3.Error, ValueError, OSError, KeyError):
        LOGGER.error("Genome annotation query failed")
        raise HTTPException(500, "Genome annotation query failed.") from None


@router.api_route("/functional-annotation", methods=["GET", "HEAD"], include_in_schema=False)
def annotation_page():
    return FileResponse(STATIC_ROOT / "index.html")


@router.get("/api/genome-annotations/metadata")
async def api_metadata():
    return await _run(metadata)


@router.post("/api/genome-annotations/query")
async def api_query(query: AnnotationQuery):
    return await _run(query_annotations, query)


@router.get("/api/genome-annotations/genes/{identifier}")
async def api_gene(identifier: str, assembly: str):
    return await _run(gene_detail, assembly, identifier)


@router.get("/api/genome-annotations/transcripts/{identifier}")
async def api_transcript(identifier: str, assembly: str):
    return await _run(transcript_detail, assembly, identifier)


@router.get("/api/genome-annotations/tf-families")
async def api_families():
    return await _run(families)


@router.get("/api/genome-annotations/downloads")
async def api_downloads():
    return await _run(downloads)


@router.get("/api/genome-annotations/downloads/{file_id}")
async def api_download(file_id: str):
    return await _run(download_file, file_id)


@router.post("/api/genome-annotations/export")
async def api_export(request: ExportRequest):
    return await _run(prepare_export, request)


@router.post("/api/genome-annotations/export-download", include_in_schema=False)
async def browser_export(payload: str = Form(...)):
    """Native browser downloads stream to disk instead of retaining a ZIP Blob in RAM."""
    try:
        request = ExportRequest.model_validate_json(payload)
    except ValidationError:
        raise HTTPException(422, "Invalid annotation export request.") from None
    return await _run(prepare_export, request)

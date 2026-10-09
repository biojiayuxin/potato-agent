"""Build an immutable, public annotation database from a verified annotation release.

Only explicitly selected tables are published. The source lock contains private paths;
it is used for verification and is never copied into public metadata or downloads.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from interface.genome_annotation_fields import (
    ASSEMBLY_STATS_FIELDS, DOMAIN_DOWNLOAD_FIELDS, EVIDENCE_FIELDS,
    EXCEPTION_DOWNLOAD_FIELDS, EXCEPTION_FIELDS, FAMILY_FIELDS, GENE_FIELDS,
    NO_MATCH_DOWNLOAD_FIELDS, PROTEIN_FIELDS, PUBLIC_LIMITATIONS, PUBLIC_SOURCES,
    TRANSCRIPT_FIELDS,
)

SCHEMA_VERSION = 4
TF_ROOT = Path("tf_extraction_planttfdb_rules")
APPLICATIONS = {"CDD", "PANTHER", "Pfam", "SMART"}
EXTRACTION_STATUSES = {"valid", "no_cds", "duplicate_gff_transcript_id", "empty_protein", "missing_protein"}

SCHEMA_SQL = """
CREATE TABLE metadata(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 schema_version INTEGER NOT NULL,dataset_version TEXT NOT NULL,built_at TEXT NOT NULL,
 metadata_json TEXT NOT NULL);
CREATE TABLE assemblies(assembly_id TEXT PRIMARY KEY,label TEXT NOT NULL,
 display_order INTEGER NOT NULL,stats_json TEXT NOT NULL);
CREATE TABLE genes(assembly_id TEXT NOT NULL,gene_id TEXT NOT NULL,gene_decision TEXT NOT NULL,
 isoform_presence_conflict INTEGER NOT NULL,family_conflict INTEGER NOT NULL,data_json TEXT NOT NULL,
 PRIMARY KEY(assembly_id,gene_id),FOREIGN KEY(assembly_id) REFERENCES assemblies);
CREATE TABLE proteins(protein_id TEXT PRIMARY KEY,protein_length INTEGER NOT NULL,
 has_hit INTEGER NOT NULL,decision_status TEXT NOT NULL,is_tf INTEGER NOT NULL,data_json TEXT NOT NULL);
CREATE TABLE transcripts(assembly_id TEXT NOT NULL,transcript_id TEXT NOT NULL,gene_id TEXT NOT NULL,
 protein_id TEXT,status TEXT NOT NULL CHECK(status IN
 ('valid','no_cds','duplicate_gff_transcript_id','empty_protein','missing_protein')),reason TEXT NOT NULL,
 data_json TEXT NOT NULL,PRIMARY KEY(assembly_id,transcript_id),
 CHECK((status='valid')=(protein_id IS NOT NULL)),
 FOREIGN KEY(assembly_id,gene_id) REFERENCES genes(assembly_id,gene_id),
 FOREIGN KEY(protein_id) REFERENCES proteins(protein_id));
CREATE TABLE protein_families(protein_id TEXT NOT NULL,family TEXT NOT NULL,grade TEXT NOT NULL,
 PRIMARY KEY(protein_id,family),FOREIGN KEY(protein_id) REFERENCES proteins);
CREATE TABLE matches(hit_id INTEGER PRIMARY KEY,protein_id TEXT NOT NULL,analysis TEXT NOT NULL,
 signature_accession TEXT NOT NULL,signature_description TEXT NOT NULL,start INTEGER NOT NULL,
 end INTEGER NOT NULL,score TEXT NOT NULL,status TEXT NOT NULL,date TEXT NOT NULL,
 interpro_accession TEXT NOT NULL,interpro_description TEXT NOT NULL,go_terms TEXT NOT NULL,
 FOREIGN KEY(protein_id) REFERENCES proteins);
CREATE VIRTUAL TABLE matches_fts USING fts5(signature_description,interpro_description,
 content='matches',content_rowid='hit_id');
CREATE TABLE protein_terms(protein_id TEXT NOT NULL,kind TEXT NOT NULL,term TEXT NOT NULL,
 PRIMARY KEY(protein_id,kind,term),FOREIGN KEY(protein_id) REFERENCES proteins) WITHOUT ROWID;
CREATE TABLE tf_evidence(evidence_id INTEGER PRIMARY KEY,protein_id TEXT NOT NULL,
 data_json TEXT NOT NULL,FOREIGN KEY(protein_id) REFERENCES proteins);
CREATE TABLE tf_families(family TEXT PRIMARY KEY,confidence_grade TEXT NOT NULL,
 coverage_status TEXT NOT NULL,data_json TEXT NOT NULL);
CREATE TABLE tf_counts(assembly_id TEXT NOT NULL,family TEXT NOT NULL,genes INTEGER NOT NULL,
 transcripts INTEGER NOT NULL,proteins INTEGER NOT NULL,PRIMARY KEY(assembly_id,family));
CREATE TABLE downloads(file_id TEXT PRIMARY KEY,filename TEXT NOT NULL,relative_path TEXT NOT NULL,
 category TEXT NOT NULL,assembly_id TEXT,size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL);
"""

INDEX_SQL = """
CREATE INDEX transcripts_gene ON transcripts(assembly_id,gene_id);
CREATE INDEX transcripts_protein ON transcripts(protein_id,assembly_id,gene_id);
CREATE INDEX transcripts_id ON transcripts(transcript_id,assembly_id);
CREATE INDEX genes_id ON genes(gene_id,assembly_id);
CREATE INDEX matches_protein ON matches(protein_id);
CREATE INDEX matches_analysis ON matches(analysis,protein_id);
CREATE INDEX matches_signature ON matches(signature_accession,protein_id);
CREATE INDEX matches_interpro ON matches(interpro_accession,protein_id);
CREATE INDEX terms_lookup ON protein_terms(kind,term,protein_id);
CREATE INDEX families_lookup ON protein_families(family,grade,protein_id);
CREATE INDEX evidence_protein ON tf_evidence(protein_id);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _progress(message: str) -> None:
    print(f"[{datetime.now(UTC).isoformat(timespec='seconds')}] {message}", file=sys.stderr, flush=True)


def _public(row: dict, fields: Iterable[str]) -> dict:
    return {field: row[field] for field in fields if field in row}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _text(path: Path):
    with (gzip.open(path, "rt", encoding="utf-8", newline="") if path.suffix == ".gz"
          else path.open(encoding="utf-8", newline="")) as handle:
        yield handle


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with _text(path) as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV: {path.name}")
            yield row


def _interpro_rows(path: Path) -> Iterator[list[str]]:
    """Ignore the unbounded final pathway cell before invoking the CSV parser.

    InterProScan's frozen TSV format has one physical line per hit. The fourteen
    retained cells are small, even when the excluded pathway cell is enormous.
    """
    with _text(path) as handle:
        for raw in handle:
            boundary = -1
            for _ in range(14):
                boundary = raw.find("\t", boundary + 1)
                if boundary < 0:
                    raise ValueError("InterProScan row must contain 15 columns")
            line = next(csv.reader([raw[:boundary] + "\t-"], delimiter="\t"))
            if len(line) != 15:
                raise ValueError("InterProScan row must contain 15 columns")
            yield line


def _bool(value: str) -> int:
    if value not in {"true", "false"}:
        raise ValueError(f"Invalid boolean: {value!r}")
    return int(value == "true")


def _tf_selected(value: str) -> int:
    # This source column is a decision label despite its boolean-looking name.
    if value not in {"true", "false", "ambiguous", "unresolved"}:
        raise ValueError(f"Invalid TF inclusive decision: {value!r}")
    return int(value == "true")


def _list(value: str) -> list[str]:
    return [] if value in {"", "-"} else value.split(";")


def _batches(rows: Iterable[tuple], size: int = 5000) -> Iterator[list[tuple]]:
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _insert(conn: sqlite3.Connection, sql: str, rows: Iterable[tuple]) -> None:
    count, last_report = 0, time.monotonic()
    for batch in _batches(rows):
        conn.executemany(sql, batch)
        count += len(batch)
        if time.monotonic() - last_report >= 30:
            _progress(f"Inserted {count:,} rows into {sql.split()[2].split('(')[0]}")
            last_report = time.monotonic()


def _manifest(root: Path, relative: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (root / relative).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid release checksum")
        item = Path(name)
        if item.is_absolute() or ".." in item.parts:
            raise ValueError("Unsafe release manifest entry")
        if name in result:
            raise ValueError("Duplicate release manifest entry")
        result[name] = digest
    return result


class VerifiedSources:
    def __init__(self, root: Path, source_project_root: Path | None = None,
                 source_format: str = "auto"):
        self.root = root
        self.source_project_root = source_project_root
        self.manifests = {
            "annotations": _manifest(root, Path("release/SHA256SUMS")),
            "tf": _manifest(root / TF_ROOT, Path("release/SHA256SUMS")),
        }
        self.checksums: dict[str, str] = {}
        if source_format == "auto":
            source_format = "five" if "metadata/source_inputs.lock.json" in self.manifests["annotations"] else "full"
        if source_format not in {"five", "full"}:
            raise ValueError("Unsupported annotation source format")
        self.full = source_format == "full"

    def input(self, kind: str) -> Path:
        names = {
            "matches": ("results/unique/interproscan_global_unique.tsv.gz", "results/unique/interproscan_unique.tsv.gz"),
            "no_match": ("results/unique/no_match_global_unique.tsv.gz", "results/unique/no_match_unique.tsv.gz"),
            "mapping": ("metadata/transcript_to_selected_unique.tsv.gz", "unique/transcript_to_unique.tsv.gz"),
        }
        return self.file(names[kind][int(self.full)])

    def file(self, relative: str | Path, *, tf: bool = False) -> Path:
        relative = Path(relative)
        manifest = self.manifests["tf" if tf else "annotations"]
        expected = manifest.get(relative.as_posix())
        if not expected:
            raise ValueError(f"Missing release checksum: {relative.name}")
        key = (TF_ROOT / relative if tf else relative).as_posix()
        path = self.root / key
        if key not in self.checksums:
            actual = _sha256(path)
            if actual != expected:
                raise ValueError(f"Checksum mismatch: {relative.name}")
            self.checksums[key] = actual
        return path

    def transcript_metadata(self, item: dict) -> Path:
        if self.full:
            return self.file(f"metadata/transcripts/{item['assembly_id']}.transcripts.tsv.gz")
        definition = item["transcript_metadata"]
        path = Path(definition["path"])
        if self.source_project_root is not None:
            path = self.source_project_root / "metadata" / "transcripts" / f"{item['assembly_id']}.transcripts.tsv.gz"
        elif not path.is_absolute():
            path = self.root / path
        actual = _sha256(path)
        if actual != definition["sha256"]:
            raise ValueError(f"Checksum mismatch: {path.name}")
        self.checksums[f"transcript_metadata/{item['assembly_id']}.tsv.gz"] = actual
        return path


def _require_pass(report: dict, field: str, label: str, *, zero_checks: set[str] | None = None) -> None:
    checks = report.get(field)
    if report.get("status") != "PASS" or not isinstance(checks, dict) or not checks:
        raise ValueError(f"Source {label} release did not pass final validation")
    for name, value in checks.items():
        if name in (zero_checks or set()):
            passed = type(value) is int and value == 0
        else:
            passed = value is True or value == "PASS"
        if not passed:
            raise ValueError(f"Source {label} release did not pass final validation: {name}")


def _load_definitions(conn: sqlite3.Connection, source: VerifiedSources) -> tuple[dict, dict, dict]:
    summary = json.loads(source.file("qc/run_summary.json", tf=True).read_text())
    closure = summary.get("closure_checks")
    if not isinstance(closure, dict) or not closure or any(value is not True for value in closure.values()):
        raise ValueError("Source TF release did not pass closure checks")
    if source.full:
        validation = json.loads(source.file("qc/T14_final_validation.json").read_text())
        _require_pass(validation, "validation", "annotation", zero_checks={"duplicate_annotation_rows", "unknown_or_missing_ids"})
        tf_validation = json.loads(source.file("qc/final_validation.json", tf=True).read_text())
        _require_pass(tf_validation, "checks", "TF")
        regression = json.loads(source.file("qc/five_genome_regression.json", tf=True).read_text())
        if (regression.get("status") not in {"PASS", "PASS_WITH_ACCEPTED_SOURCE_DIFFERENCES"}
                or regression.get("status") != tf_validation.get("five_genome_regression")
                or regression.get("biological_and_sequence_fields_strict") is not True
                or regression.get("unused_source_difference_approvals") != []):
            raise ValueError("Source five-genome regression did not pass acceptance")
        if summary.get("status") not in {"COMPUTE_COMPLETE", "PASS"}:
            raise ValueError("Source TF computation is incomplete")
        for key in summary["counts"].keys() & tf_validation["counts"].keys():
            if summary["counts"][key] != tf_validation["counts"][key]:
                raise ValueError(f"TF summary and final validation disagree: {key}")
        raw_counts = {**summary["counts"], **tf_validation["counts"]}
        aliases = {"genes_total": "total_genes", "transcripts_total": "total_transcripts",
                   "transcripts_valid": "valid_transcripts", "transcripts_exceptions": "extraction_exceptions",
                   "unique_proteins_selected_tf": "protein_selected_tf", "genes_selected_tf": "selected_tf_genes",
                   "transcripts_selected_tf": "selected_tf_transcripts", "relevant_pfam_evidence_rows": "evidence_rows"}
        summary["counts"] = {**raw_counts, **{target: raw_counts[key] for target, key in aliases.items()}}
        summary["counts"]["annotation_hits"] = validation["counts"]["tsv_line_count"]
        annotation_aliases = {"total_transcripts": "metadata_rows", "extraction_exceptions": "exception_unique_transcript_ids",
                              "global_unique_proteins": "input_protein_count", "global_hit_proteins": "matched_protein_count",
                              "global_no_hit_proteins": "no_hit_protein_count", "hit_transcripts": "matched_transcripts",
                              "no_hit_transcripts": "no_match_transcripts"}
        validation["counts"].update({target: validation["counts"][key] for target, key in annotation_aliases.items()})
        assemblies = [{"assembly_id": r["id"]} for r in _rows(source.file("metadata/source_assemblies.tsv"))]
        lock = {"selected_assemblies": assemblies}
        signature = json.loads(source.file("metadata/production_signature.json").read_text())
        execution = signature["payload"]["execution_signature_payload"]
        if set(execution["applications"]) != APPLICATIONS:
            raise ValueError("Source applications disagree with the annotation catalog")
        jar_path = execution["tracked_files"]["interproscan_jar"]["path"]
        version = re.search(r"interproscan-([0-9]+\.[0-9]+-[0-9]+\.[0-9]+)", jar_path)
        if not version:
            raise ValueError("Verified production signature does not identify InterProScan version")
        summary["software"] = {"interproscan": version.group(1)}
        summary["limitations"] = PUBLIC_LIMITATIONS
        summary["sources"] = PUBLIC_SOURCES
        source.tf_validation = tf_validation
    else:
        lock = json.loads(source.file("metadata/source_inputs.lock.json").read_text())
        validation = json.loads(source.file("qc/G08_final_validation.json").read_text())
        _require_pass(validation, "checks", "annotation")
        if summary.get("status") != "PASS":
            raise ValueError("Source TF release did not pass closure checks")
        assemblies = list(_rows(source.file("metadata/selected_assemblies.tsv")))
        if {r["assembly_id"] for r in assemblies} != {r["assembly_id"] for r in lock["selected_assemblies"]}:
            raise ValueError("Selected assembly manifest disagrees with source lock")
    counts = {row["assembly_id"]: row for row in _rows(source.file("qc/final_counts_by_assembly.tsv"))}
    tf_counts = {row["assembly_id"]: row for row in _rows(source.file("qc/assembly_summary.tsv", tf=True))}
    expected_assemblies = {r["assembly_id"] for r in assemblies}
    if set(counts) != expected_assemblies or set(tf_counts) != expected_assemblies:
        raise ValueError("Assembly count tables disagree with the source assembly catalog")
    for order, row in enumerate(assemblies):
        assembly = row["assembly_id"]
        stats = dict(counts[assembly], **tf_counts[assembly])
        if source.full:
            original = counts[assembly]
            for normalized, original_key in (("total_transcripts", "metadata_rows"), ("valid_transcripts", "valid_transcripts"),
                                               ("extraction_exceptions", "exception_unique_transcript_ids")):
                if int(stats[normalized]) != int(original[original_key]):
                    raise ValueError(f"Annotation and TF assembly counts disagree: {assembly}")
            stats.update(hit_transcripts=original["matched_transcripts"], no_hit_transcripts=original["no_match_transcripts"], closure_status="PASS")
        if stats["closure_status"] != "PASS":
            raise ValueError(f"Source closure failed: {assembly}")
        stats = _public(stats, ASSEMBLY_STATS_FIELDS)
        for key, value in stats.items():
            if isinstance(value, str) and value.isdigit():
                stats[key] = int(value)
        conn.execute("INSERT INTO assemblies VALUES(?,?,?,?)", (
            assembly, assembly.rsplit("/", 1)[-1], order, _json(stats)))
    for name in ("planttfdb_public_rules_v5.tsv", "supplemental_candidate_policy.tsv"):
        _insert(conn, "INSERT INTO tf_families VALUES(?,?,?,?)", (
            (row["family"], row["confidence_grade"], row["coverage_status"], _json(_public(row, FAMILY_FIELDS)))
            for row in _rows(source.file(f"rules/{name}", tf=True))))
    _insert(conn, "INSERT INTO tf_counts VALUES(?,?,?,?,?)", (
        (row["scope"], row["family"], int(row["selected_genes"]), int(row["selected_transcripts"]),
         int(row["selected_unique_proteins"]))
        for row in _rows(source.file("qc/family_counts.tsv", tf=True))))
    return lock, summary, validation


def _load_entities(conn: sqlite3.Connection, source: VerifiedSources, lock: dict) -> None:
    _insert(conn, "INSERT INTO genes VALUES(?,?,?,?,?,?)", (
        (r["assembly_id"], r["gene_id"], r["gene_decision"], _bool(r["isoform_presence_conflict"]),
         _bool(r["family_conflict"]), _json(_public(r, GENE_FIELDS)))
        for r in _rows(source.file("results/gene_decisions.tsv.gz", tf=True))))
    grades = dict(conn.execute("SELECT family,confidence_grade FROM tf_families"))
    protein_rows: list[tuple] = []
    family_rows: list[tuple] = []
    for count, row in enumerate(_rows(source.file("results/protein_decisions.tsv.gz", tf=True)), 1):
        protein = row["global_unique_protein_id"]
        protein_rows.append((protein, int(row["protein_length"]), _bool(row["has_any_interpro_hit"]),
                             row["decision_status"], _tf_selected(row["is_tf_inclusive_result"]), _json(_public(row, PROTEIN_FIELDS))))
        for family in _list(row["tf_families"]):
            family_rows.append((protein, family, grades[family]))
        if len(protein_rows) >= 5000:
            conn.executemany("INSERT INTO proteins VALUES(?,?,?,?,?,?)", protein_rows)
            conn.executemany("INSERT INTO protein_families VALUES(?,?,?)", family_rows)
            protein_rows.clear()
            family_rows.clear()
            if count % 250_000 == 0:
                _progress(f"Imported {count:,} unique proteins")
    conn.executemany("INSERT INTO proteins VALUES(?,?,?,?,?,?)", protein_rows)
    conn.executemany("INSERT INTO protein_families VALUES(?,?,?)", family_rows)
    _insert(conn, "INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", (
        (r["assembly_id"], r["transcript_id"], r["gene_id"], r["global_unique_protein_id"],
         "valid", "", _json(_public(r, TRANSCRIPT_FIELDS)))
        for r in _rows(source.file("results/transcript_decisions.tsv.gz", tf=True))))
    for field in ("sequence_md5", "sequence_sha256"):
        _must_be_empty(conn, f"SELECT 1 FROM transcripts t JOIN proteins p USING(protein_id) "
                       f"WHERE json_extract(t.data_json,'$.{field}') IS NOT NULL "
                       f"AND json_extract(t.data_json,'$.{field}') IS NOT json_extract(p.data_json,'$.{field}') LIMIT 1",
                       f"Transcript and protein sequence {field} disagree")
    conn.execute("CREATE TEMP TABLE source_transcripts(assembly_id TEXT,transcript_id TEXT,gene_id TEXT,"
                 "status TEXT,protein_length INTEGER,exception_json TEXT,PRIMARY KEY(assembly_id,transcript_id))")
    if source.full:
        def exception_rows():
            for row in _rows(source.file("results/extraction_exceptions.tsv.gz", tf=True)):
                if row["gene_attribution"] != "assigned" or row["status"] not in EXTRACTION_STATUSES - {"valid"}:
                    raise ValueError("Unsupported extraction exception or gene attribution")
                yield (row["assembly_id"], row["transcript_id"], row["gene_id"], None,
                       row["status"], row["reason"], _json(_public(row, EXCEPTION_FIELDS)))
        _insert(conn, "INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", exception_rows())
    for item in lock["selected_assemblies"]:
        _progress(f"Verify transcript metadata: {item['assembly_id']}")
        metadata_rows: list[tuple] = []
        no_cds: list[tuple] = []
        for row in _rows(source.transcript_metadata(item)):
            if row["assembly_id"] != item["assembly_id"] or row["status"] not in EXTRACTION_STATUSES:
                raise ValueError("Unexpected source transcript assembly or extraction status")
            metadata_rows.append((row["assembly_id"], row["transcript_id"], row["gene_id"],
                                  row["status"], int(row["protein_length"]),
                                  _json(_public(row, EXCEPTION_FIELDS)) if row["status"] != "valid" else None))
            if row["status"] != "valid" and not source.full:
                no_cds.append((row["assembly_id"], row["transcript_id"], row["gene_id"], None,
                               row["status"], row["reason"], _json(_public(row, EXCEPTION_FIELDS))))
            if len(metadata_rows) >= 5000:
                conn.executemany("INSERT INTO source_transcripts VALUES(?,?,?,?,?,?)", metadata_rows)
                conn.executemany("INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", no_cds)
                metadata_rows.clear()
                no_cds.clear()
        conn.executemany("INSERT INTO source_transcripts VALUES(?,?,?,?,?,?)", metadata_rows)
        conn.executemany("INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", no_cds)
    _must_be_empty(conn, "SELECT 1 FROM source_transcripts s LEFT JOIN transcripts t USING(assembly_id,transcript_id) "
                   "LEFT JOIN proteins p USING(protein_id) WHERE t.transcript_id IS NULL OR s.gene_id!=t.gene_id "
                   "OR s.status!=t.status OR (s.status='valid' AND s.protein_length!=p.protein_length) "
                   "OR (s.status!='valid' AND s.exception_json!=t.data_json) LIMIT 1",
                   "Source transcript metadata disagrees with TF transcript mapping")
    _must_be_empty(conn, "SELECT 1 FROM transcripts t LEFT JOIN source_transcripts s USING(assembly_id,transcript_id) "
                   "WHERE s.transcript_id IS NULL LIMIT 1", "TF transcript absent from source metadata")
    conn.execute("DROP TABLE source_transcripts")
    conn.execute("CREATE TEMP TABLE source_mapping(assembly_id TEXT,transcript_id TEXT,protein_id TEXT,"
                 "protein_length INTEGER,sequence_md5 TEXT,sequence_sha256 TEXT,PRIMARY KEY(assembly_id,transcript_id))")
    _insert(conn, "INSERT INTO source_mapping VALUES(?,?,?,?,?,?)", (
        (r["assembly_id"], r["transcript_id"], r["unique_protein_id" if source.full else "global_unique_protein_id"], int(r["protein_length"]),
         r.get("sequence_md5"), r.get("sequence_sha256"))
        for r in _rows(source.input("mapping"))))
    _must_be_empty(conn, "SELECT 1 FROM source_mapping s LEFT JOIN transcripts t USING(assembly_id,transcript_id) "
                   "LEFT JOIN proteins p ON p.protein_id=t.protein_id WHERE t.transcript_id IS NULL "
                   "OR s.protein_id!=t.protein_id OR t.status!='valid' OR s.protein_length!=p.protein_length LIMIT 1",
                   "Global protein mapping disagrees with transcript decisions")
    _must_be_empty(conn, "SELECT 1 FROM transcripts t LEFT JOIN source_mapping s USING(assembly_id,transcript_id) "
                   "WHERE t.status='valid' AND s.transcript_id IS NULL LIMIT 1", "Valid transcript lacks global protein mapping")
    for field in ("sequence_md5", "sequence_sha256"):
        _must_be_empty(conn, f"SELECT 1 FROM source_mapping s JOIN proteins p USING(protein_id) "
                       f"WHERE s.{field} IS NOT NULL AND s.{field} IS NOT json_extract(p.data_json,'$.{field}') LIMIT 1",
                       f"Global protein mapping sequence {field} disagrees")
    conn.execute("DROP TABLE source_mapping")


def _must_be_empty(conn: sqlite3.Connection, sql: str, message: str) -> None:
    if conn.execute(sql).fetchone() is not None:
        raise ValueError(message)


def _load_matches(conn: sqlite3.Connection, source: VerifiedSources) -> None:
    # Bound memory by the input batch, rather than retaining millions of identities.
    identities: dict[str, tuple[int, str]] = {}

    def flush() -> None:
        if not matches:
            return
        marks = ",".join("?" for _ in identities)
        actual = {row[0]: (row[1], row[2]) for row in conn.execute(
            "SELECT protein_id,protein_length,json_extract(data_json,'$.sequence_md5') "
            f"FROM proteins WHERE protein_id IN ({marks})", list(identities))}
        for protein, (length, md5) in identities.items():
            identity = actual.get(protein)
            if identity is None or identity[0] != length:
                raise ValueError("Unexpected global protein identity in InterProScan row")
            if identity[1] is not None and md5 != identity[1]:
                raise ValueError("InterProScan sequence MD5 disagrees with global protein")
        conn.executemany("INSERT INTO matches VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)", matches)
        conn.executemany("INSERT OR IGNORE INTO protein_terms VALUES(?,?,?)", terms)
        matches.clear()
        terms.clear()
        identities.clear()
    matches: list[tuple] = []
    terms: set[tuple] = set()
    for count, line in enumerate(_interpro_rows(source.input("matches")), 1):
        if len(line) != 15:
            raise ValueError("InterProScan row must contain 15 columns")
        protein, md5, length, analysis, signature, description, start, end, *rest = line
        start_i, end_i = int(start), int(end)
        identity = (int(length), md5)
        if analysis not in APPLICATIONS:
            raise ValueError("Unexpected application in InterProScan row")
        if identities.setdefault(protein, identity) != identity:
            raise ValueError("Inconsistent global protein identity in InterProScan rows")
        if not 1 <= start_i <= end_i <= int(length):
            raise ValueError("InterProScan coordinates outside protein")
        score, status, date, interpro, interpro_description, go, _pathways = rest
        matches.append((protein, analysis, signature, description, start_i, end_i, score,
                        status, date, interpro, interpro_description, go))
        terms.add((protein, "signature", signature))
        if interpro != "-":
            terms.add((protein, "interpro", interpro))
        for term in re.findall(r"GO:\d+", go):
            terms.add((protein, "go", term))
        if len(matches) >= 5000:
            flush()
            if count % 250_000 == 0:
                _progress(f"Imported {count:,} unique-protein domain hits")
    flush()
    _insert(conn, "INSERT INTO tf_evidence VALUES(NULL,?,?)", (
        (r["global_unique_protein_id"], _json(_public(r, EVIDENCE_FIELDS)))
        for r in _rows(source.file("results/tf_evidence.tsv.gz", tf=True))))
    conn.execute("CREATE TEMP TABLE no_match(protein_id TEXT PRIMARY KEY,protein_length INTEGER)")
    _insert(conn, "INSERT INTO no_match VALUES(?,?)", (
        (r["unique_protein_id" if source.full else "global_unique_protein_id"], int(r["protein_length"]))
        for r in _rows(source.input("no_match"))))
    _must_be_empty(conn, "SELECT 1 FROM no_match n LEFT JOIN proteins p USING(protein_id) "
                   "WHERE p.protein_id IS NULL OR p.has_hit!=0 OR n.protein_length!=p.protein_length LIMIT 1",
                   "No-match table disagrees with protein decisions")
    _must_be_empty(conn, "SELECT 1 FROM proteins p LEFT JOIN no_match n USING(protein_id) "
                   "WHERE p.has_hit=0 AND n.protein_id IS NULL LIMIT 1", "Missing no-match protein")
    conn.execute("DROP TABLE no_match")


def _validate(conn: sqlite3.Connection, summary: dict, validation: dict) -> dict[str, int]:
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Annotation references do not close")
    _must_be_empty(conn, "SELECT 1 FROM proteins p WHERE has_hit != EXISTS(SELECT 1 FROM matches m "
                   "WHERE m.protein_id=p.protein_id) LIMIT 1", "Protein hit/no-hit partition disagrees")
    _must_be_empty(conn, "SELECT 1 FROM proteins p WHERE NOT EXISTS(SELECT 1 FROM transcripts t "
                   "WHERE t.protein_id=p.protein_id) LIMIT 1", "Protein has no mapped transcript")
    _must_be_empty(conn, "SELECT 1 FROM genes g LEFT JOIN (SELECT assembly_id,gene_id,COUNT(*) total,"
                   "SUM(status='valid') valid,SUM(status!='valid') exceptions,COUNT(DISTINCT protein_id) proteins "
                   "FROM transcripts GROUP BY assembly_id,gene_id) t USING(assembly_id,gene_id) "
                   "WHERE t.total IS NULL OR t.total!=CAST(json_extract(g.data_json,'$.total_transcript_count') AS INTEGER) "
                   "OR t.valid!=CAST(json_extract(g.data_json,'$.valid_transcript_count') AS INTEGER) "
                   "OR t.exceptions!=CAST(json_extract(g.data_json,'$.exception_transcript_count') AS INTEGER) "
                   "OR t.proteins!=CAST(json_extract(g.data_json,'$.distinct_protein_count') AS INTEGER) LIMIT 1",
                   "Per-gene transcript closure failed")
    counts = {
        "genes_total": conn.execute("SELECT COUNT(*) FROM genes").fetchone()[0],
        "transcripts_total": conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0],
        "transcripts_valid": conn.execute("SELECT COUNT(*) FROM transcripts WHERE status='valid'").fetchone()[0],
        "transcripts_exceptions": conn.execute("SELECT COUNT(*) FROM transcripts WHERE status!='valid'").fetchone()[0],
        "unique_proteins_total": conn.execute("SELECT COUNT(*) FROM proteins").fetchone()[0],
        "unique_proteins_selected_tf": conn.execute("SELECT COUNT(*) FROM proteins WHERE is_tf=1").fetchone()[0],
        "genes_selected_tf": conn.execute("SELECT COUNT(*) FROM genes WHERE json_extract(data_json,'$.has_selected_tf_isoform')='true'").fetchone()[0],
        "transcripts_selected_tf": conn.execute("SELECT COUNT(*) FROM transcripts t JOIN proteins p USING(protein_id) WHERE p.is_tf=1").fetchone()[0],
        "relevant_pfam_evidence_rows": conn.execute("SELECT COUNT(*) FROM tf_evidence").fetchone()[0],
        "annotation_hits": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
        "hit_proteins": conn.execute("SELECT COUNT(*) FROM proteins WHERE has_hit=1").fetchone()[0],
        "hit_transcripts": conn.execute("SELECT COUNT(*) FROM transcripts JOIN proteins USING(protein_id) WHERE has_hit=1").fetchone()[0],
    }
    if "tsv_line_count" in validation["counts"]:
        expanded = conn.execute("SELECT COUNT(*) FROM transcripts t JOIN matches m USING(protein_id)").fetchone()[0]
        if expanded != validation["counts"]["annotation_rows"]:
            raise ValueError("Transcript-expanded annotation row count mismatch")
        for status, actual in conn.execute("SELECT status,COUNT(*) FROM transcripts WHERE status!='valid' GROUP BY status"):
            if actual != summary["counts"].get("exception_" + status):
                raise ValueError(f"Extraction exception count mismatch: {status}")
    for key, actual in counts.items():
        if key in summary["counts"] and actual != int(summary["counts"][key]):
            raise ValueError(f"Release count mismatch: {key}: {actual}")
    annotation_counts = {"assemblies": conn.execute("SELECT COUNT(*) FROM assemblies").fetchone()[0],
        "total_transcripts": counts["transcripts_total"], "valid_transcripts": counts["transcripts_valid"],
        "extraction_exceptions": counts["transcripts_exceptions"], "global_unique_proteins": counts["unique_proteins_total"],
        "global_hit_proteins": counts["hit_proteins"],
        "global_no_hit_proteins": counts["unique_proteins_total"] - counts["hit_proteins"],
        "hit_transcripts": counts["hit_transcripts"],
        "no_hit_transcripts": counts["transcripts_valid"] - counts["hit_transcripts"]}
    for key, actual in annotation_counts.items():
        if key in validation["counts"] and actual != int(validation["counts"][key]):
            raise ValueError(f"Annotation release count mismatch: {key}: {actual}")
    for assembly, stats_json in conn.execute("SELECT assembly_id,stats_json FROM assemblies"):
        stats = json.loads(stats_json)
        actual = conn.execute("SELECT COUNT(*),SUM(status='valid'),SUM(status!='valid') FROM transcripts WHERE assembly_id=?", (assembly,)).fetchone()
        expected = (stats["total_transcripts"], stats["valid_transcripts"], stats["extraction_exceptions"])
        if actual != expected:
            raise ValueError(f"Assembly transcript count mismatch: {assembly}")
        actual_genes = conn.execute("SELECT COUNT(*) FROM genes WHERE assembly_id=?", (assembly,)).fetchone()[0]
        if actual_genes != stats["total_genes"]:
            raise ValueError(f"Assembly gene count mismatch: {assembly}")
    family_counts = {}
    for assembly, family, genes, transcripts, proteins in conn.execute(
            "SELECT t.assembly_id,f.family,COUNT(DISTINCT t.gene_id),COUNT(*),COUNT(DISTINCT t.protein_id) "
            "FROM transcripts t JOIN proteins p USING(protein_id) JOIN protein_families f USING(protein_id) "
            "WHERE p.is_tf=1 GROUP BY t.assembly_id,f.family"):
        family_counts[(assembly, family)] = (genes, transcripts, proteins)
    for family, genes, transcripts, proteins in conn.execute(
            "SELECT f.family,COUNT(DISTINCT t.assembly_id||char(0)||t.gene_id),COUNT(*),COUNT(DISTINCT t.protein_id) "
            "FROM transcripts t JOIN proteins p USING(protein_id) JOIN protein_families f USING(protein_id) "
            "WHERE p.is_tf=1 GROUP BY f.family"):
        family_counts[("ALL", family)] = (genes, transcripts, proteins)
    for assembly, family, genes, transcripts, proteins in conn.execute("SELECT * FROM tf_counts"):
        if family_counts.get((assembly, family), (0, 0, 0)) != (genes, transcripts, proteins):
            raise ValueError(f"TF family count mismatch: {assembly}: {family}")
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    return counts


def _download_rows(conn: sqlite3.Connection, assembly: str, category: str) -> Iterator[dict]:
    if category == "annotations":
        sql = """SELECT t.assembly_id,t.transcript_id,t.protein_id AS global_unique_protein_id,
            json_extract(t.data_json,'$.global_transcript_key') AS global_transcript_key,
            json_extract(p.data_json,'$.annotation_origin') AS annotation_origin,
            json_extract(p.data_json,'$.sequence_md5') AS sequence_md5,p.protein_length,m.*
            FROM transcripts t JOIN proteins p USING(protein_id) JOIN matches m USING(protein_id)
            WHERE t.assembly_id=? ORDER BY t.transcript_id,m.hit_id"""
    elif category == "no_match":
        sql = """SELECT t.assembly_id,t.transcript_id,t.protein_id AS global_unique_protein_id,
            json_extract(t.data_json,'$.global_transcript_key') AS global_transcript_key,
            json_extract(p.data_json,'$.annotation_origin') AS annotation_origin,
            json_extract(p.data_json,'$.sequence_md5') AS sequence_md5,p.protein_length
            FROM transcripts t JOIN proteins p USING(protein_id)
            WHERE t.assembly_id=? AND t.status='valid' AND p.has_hit=0 ORDER BY t.transcript_id"""
    elif category == "extraction_exceptions":
        sql = """SELECT assembly_id,transcript_id,json_extract(data_json,'$.global_transcript_key') AS global_transcript_key,
            status,reason FROM transcripts WHERE assembly_id=? AND status!='valid' ORDER BY transcript_id"""
    elif category == "tf_genes":
        sql = """SELECT data_json FROM genes WHERE assembly_id=?
            AND json_extract(data_json,'$.has_selected_tf_isoform')='true' ORDER BY gene_id"""
    else:
        sql = """SELECT t.data_json FROM transcripts t JOIN proteins p USING(protein_id)
            WHERE t.assembly_id=? AND p.is_tf=1 ORDER BY t.transcript_id"""
    cursor = conn.execute(sql, (assembly,))
    names = [column[0] for column in cursor.description]
    for row in cursor:
        if category in {"tf_genes", "tf_transcripts"}:
            yield json.loads(row[0])
        else:
            record = dict(zip(names, row))
            if not record.get("global_transcript_key"):
                record["global_transcript_key"] = f"{assembly}::{record['transcript_id']}"
            yield record


def _write_public_table(destination: Path, fields: Iterable[str], rows: Iterable[dict], *, compressed: bool) -> None:
    fields = tuple(fields)
    with destination.open("wb") as raw:
        stream = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) if compressed else raw
        with io.TextIOWrapper(stream, encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def _stage_downloads(conn: sqlite3.Connection, source: VerifiedSources, target: Path,
                     db_parent: Path) -> None:
    """Generate only the original public column sets from verified database rows."""
    target.mkdir(parents=True, exist_ok=True)
    definitions = (("domains", "annotations", DOMAIN_DOWNLOAD_FIELDS),
                   ("no_match", "no_match", NO_MATCH_DOWNLOAD_FIELDS),
                   ("extraction_exceptions", "extraction_exceptions", EXCEPTION_DOWNLOAD_FIELDS),
                   ("tf_genes", "tf_genes", GENE_FIELDS),
                   ("tf_transcripts", "tf_transcripts", TRANSCRIPT_FIELDS))

    def publish(filename: str, category: str, assembly: str | None, fields: Iterable[str], rows: Iterable[dict]) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=".download-", dir=target)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            _write_public_table(temporary, fields, rows, compressed=filename.endswith(".gz"))
            digest = _sha256(temporary)
            destination = target / f"{digest[:16]}-{filename}"
            if destination.exists():
                if _sha256(destination) != digest:
                    raise ValueError("Existing staged download has incorrect content")
            else:
                os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        file_id = hashlib.sha256(f"{assembly or 'all'}:{filename}".encode()).hexdigest()[:24]
        conn.execute("INSERT INTO downloads VALUES(?,?,?,?,?,?,?)", (
            file_id, filename, destination.relative_to(db_parent).as_posix(), category, assembly,
            destination.stat().st_size, digest))

    for (assembly,) in conn.execute("SELECT assembly_id FROM assemblies ORDER BY display_order"):
        _progress(f"Generate public downloads: {assembly}")
        for suffix, category, fields in definitions:
            publish(f"{assembly.rsplit('/', 1)[-1]}.{suffix}.tsv.gz",
                    "transcription_factors" if category.startswith("tf_") else category,
                    assembly, fields, _download_rows(conn, assembly, category))
    for name in ("planttfdb_public_rules_v5.tsv", "supplemental_candidate_policy.tsv"):
        publish(name, "methods", None, FAMILY_FIELDS, _rows(source.file(f"rules/{name}", tf=True)))


def preflight_sources(source_root: Path, *, source_format: str = "auto",
                      source_project_root: Path | None = None) -> dict:
    """Validate the small manifests and all required entries before a costly build."""
    source = VerifiedSources(Path(source_root).resolve(), source_project_root, source_format)
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(SCHEMA_SQL)
        lock, summary, validation = _load_definitions(conn, source)
    annotation_files = [f"metadata/transcripts/{r['assembly_id']}.transcripts.tsv.gz" for r in lock["selected_assemblies"]] if source.full else []
    annotation_files += (["unique/transcript_to_unique.tsv.gz", "results/unique/interproscan_unique.tsv.gz", "results/unique/no_match_unique.tsv.gz"]
                         if source.full else ["metadata/transcript_to_selected_unique.tsv.gz", "results/unique/interproscan_global_unique.tsv.gz", "results/unique/no_match_global_unique.tsv.gz"])
    tf_files = [f"results/{name}.tsv.gz" for name in ("gene_decisions", "protein_decisions", "transcript_decisions", "tf_evidence")]
    if source.full:
        tf_files.append("results/extraction_exceptions.tsv.gz")
    for namespace, files in (("annotations", annotation_files), ("tf", tf_files)):
        root = source.root / TF_ROOT if namespace == "tf" else source.root
        for relative in files:
            if relative not in source.manifests[namespace] or not (root / relative).is_file():
                raise ValueError(f"Missing required released source: {relative}")
    return {"source_format": "full" if source.full else "five", "assemblies": len(lock["selected_assemblies"]),
            "tf_counts": summary["counts"], "annotation_counts": validation["counts"],
            "verified_definition_files": len(source.checksums)}


def build_database(source_root: Path, output_db: Path, dataset_version: str, *,
                   downloads_dir: Path | None = None,
                   source_project_root: Path | None = None,
                   source_format: str = "auto") -> dict[str, Any]:
    """Verify frozen inputs, build beside the destination, then atomically replace it.

    An exception leaves an existing output database untouched. Source data is read-only.
    Downloads must live beneath the output database's parent; omit to build without them.
    """
    source_root, output_db = Path(source_root).resolve(), Path(output_db).resolve()
    if not dataset_version.strip() or len(dataset_version) > 200:
        raise ValueError("A nonempty dataset version of at most 200 characters is required")
    if output_db.is_relative_to(source_root):
        raise ValueError("Output database must be outside the frozen source directory")
    if downloads_dir is not None:
        downloads_dir = Path(downloads_dir).resolve()
        if not downloads_dir.is_relative_to(output_db.parent) or downloads_dir == output_db.parent:
            raise ValueError("Downloads directory must be beneath the database directory")
        if downloads_dir.is_relative_to(source_root):
            raise ValueError("Downloads directory must be outside the frozen source directory")
    source = VerifiedSources(source_root, Path(source_project_root).resolve() if source_project_root else None, source_format)
    output_db.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output_db.name}-", suffix=".tmp", dir=output_db.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    conn = sqlite3.connect(temporary)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-65536")
        conn.execute("PRAGMA temp_store=FILE")
        conn.executescript(SCHEMA_SQL)
        _progress("Verify source definitions")
        lock, summary, validation = _load_definitions(conn, source)
        _progress("Import genes, proteins, transcripts and verify mappings")
        _load_entities(conn, source, lock)
        _progress("Import unique-protein domain matches and TF evidence")
        _load_matches(conn, source)
        _progress("Build annotation indexes")
        for statement in INDEX_SQL.split(";"):
            if statement.strip():
                _progress(f"Build index: {statement.split()[2]}")
                conn.execute(statement)
        _progress("Validate counts, relationships and SQLite integrity")
        counts = _validate(conn, summary, validation)
        _progress("Build and verify full-text index")
        conn.execute("INSERT INTO matches_fts(matches_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO matches_fts(matches_fts,rank) VALUES('integrity-check',1)")
        if downloads_dir is not None:
            _stage_downloads(conn, source, downloads_dir, output_db.parent)
        metadata = {
            "counts": counts, "software": summary["software"], "method": summary["method_label"],
            "rulesetVersion": summary["ruleset_version"], "limitations": summary["limitations"],
            "sources": summary.get("sources", {}), "applications": sorted(APPLICATIONS),
            "coordinateConvention": "protein amino acids, 1-based inclusive",
            "sourceChecksums": source.checksums,
        }
        built_at = datetime.now(UTC).isoformat()
        conn.execute("INSERT INTO metadata VALUES(1,?,?,?,?)", (
            SCHEMA_VERSION, dataset_version, built_at, _json(metadata)))
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        _progress("Analyze and finalize immutable database")
        conn.execute("ANALYZE")
        conn.commit()
        conn.close()
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, output_db)
        directory_fd = os.open(output_db.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {"schema_version": SCHEMA_VERSION, "dataset_version": dataset_version,
                "built_at": built_at, "counts": counts}
    finally:
        conn.close()
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-db", type=Path, required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--downloads-dir", type=Path)
    parser.add_argument("--source-format", choices=("auto", "five", "full"), default="auto")
    parser.add_argument("--source-project-root", type=Path,
                        help="Read locked transcript metadata from a verified copy of the parent project")
    args = parser.parse_args(argv)
    result = build_database(args.source_root, args.output_db, args.dataset_version,
                            downloads_dir=args.downloads_dir, source_project_root=args.source_project_root,
                            source_format=args.source_format)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

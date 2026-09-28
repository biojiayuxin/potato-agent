"""Build an immutable, public annotation database from the frozen five-genome run.

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
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 3
TF_ROOT = Path("tf_extraction_planttfdb_rules")
APPLICATIONS = {"CDD", "PANTHER", "Pfam", "SMART"}

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
 protein_id TEXT,status TEXT NOT NULL CHECK(status IN ('valid','no_cds')),reason TEXT NOT NULL,
 data_json TEXT NOT NULL,PRIMARY KEY(assembly_id,transcript_id),
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
    for batch in _batches(rows):
        conn.executemany(sql, batch)


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
    def __init__(self, root: Path, source_project_root: Path | None = None):
        self.root = root
        self.source_project_root = source_project_root
        self.manifests = {
            "annotations": _manifest(root, Path("release/SHA256SUMS")),
            "tf": _manifest(root / TF_ROOT, Path("release/SHA256SUMS")),
        }
        self.checksums: dict[str, str] = {}

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


def _load_definitions(conn: sqlite3.Connection, source: VerifiedSources) -> tuple[dict, dict, dict]:
    lock = json.loads(source.file("metadata/source_inputs.lock.json").read_text())
    summary = json.loads(source.file("qc/run_summary.json", tf=True).read_text())
    validation = json.loads(source.file("qc/G08_final_validation.json").read_text())
    if validation.get("status") != "PASS" or not validation.get("checks") or any(
            value != "PASS" for value in validation["checks"].values()):
        raise ValueError("Source annotation release did not pass final validation")
    if summary.get("status") != "PASS" or not all(summary.get("closure_checks", {}).values()):
        raise ValueError("Source TF release did not pass closure checks")
    counts = {row["assembly_id"]: row for row in _rows(source.file("qc/final_counts_by_assembly.tsv"))}
    tf_counts = {row["assembly_id"]: row for row in _rows(source.file("qc/assembly_summary.tsv", tf=True))}
    assemblies = list(_rows(source.file("metadata/selected_assemblies.tsv")))
    if {r["assembly_id"] for r in assemblies} != {r["assembly_id"] for r in lock["selected_assemblies"]}:
        raise ValueError("Selected assembly manifest disagrees with source lock")
    for order, row in enumerate(assemblies):
        assembly = row["assembly_id"]
        stats = dict(counts[assembly], **tf_counts[assembly])
        if stats["closure_status"] != "PASS":
            raise ValueError(f"Source closure failed: {assembly}")
        for key, value in stats.items():
            if value.isdigit():
                stats[key] = int(value)
        conn.execute("INSERT INTO assemblies VALUES(?,?,?,?)", (
            assembly, assembly.rsplit("/", 1)[-1], order, _json(stats)))
    for name in ("planttfdb_public_rules_v5.tsv", "supplemental_candidate_policy.tsv"):
        _insert(conn, "INSERT INTO tf_families VALUES(?,?,?,?)", (
            (row["family"], row["confidence_grade"], row["coverage_status"], _json(row))
            for row in _rows(source.file(f"rules/{name}", tf=True))))
    _insert(conn, "INSERT INTO tf_counts VALUES(?,?,?,?,?)", (
        (row["scope"], row["family"], int(row["selected_genes"]), int(row["selected_transcripts"]),
         int(row["selected_unique_proteins"]))
        for row in _rows(source.file("qc/family_counts.tsv", tf=True))))
    return lock, summary, validation


def _load_entities(conn: sqlite3.Connection, source: VerifiedSources, lock: dict) -> None:
    _insert(conn, "INSERT INTO genes VALUES(?,?,?,?,?,?)", (
        (r["assembly_id"], r["gene_id"], r["gene_decision"], _bool(r["isoform_presence_conflict"]),
         _bool(r["family_conflict"]), _json(r))
        for r in _rows(source.file("results/gene_decisions.tsv.gz", tf=True))))
    grades = dict(conn.execute("SELECT family,confidence_grade FROM tf_families"))
    protein_rows: list[tuple] = []
    family_rows: list[tuple] = []
    for row in _rows(source.file("results/protein_decisions.tsv.gz", tf=True)):
        protein = row["global_unique_protein_id"]
        protein_rows.append((protein, int(row["protein_length"]), _bool(row["has_any_interpro_hit"]),
                             row["decision_status"], _tf_selected(row["is_tf_inclusive_result"]), _json(row)))
        for family in _list(row["tf_families"]):
            family_rows.append((protein, family, grades[family]))
        if len(protein_rows) >= 5000:
            conn.executemany("INSERT INTO proteins VALUES(?,?,?,?,?,?)", protein_rows)
            conn.executemany("INSERT INTO protein_families VALUES(?,?,?)", family_rows)
            protein_rows.clear()
            family_rows.clear()
    conn.executemany("INSERT INTO proteins VALUES(?,?,?,?,?,?)", protein_rows)
    conn.executemany("INSERT INTO protein_families VALUES(?,?,?)", family_rows)
    _insert(conn, "INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", (
        (r["assembly_id"], r["transcript_id"], r["gene_id"], r["global_unique_protein_id"],
         "valid", "", _json(r))
        for r in _rows(source.file("results/transcript_decisions.tsv.gz", tf=True))))
    for field in ("sequence_md5", "sequence_sha256"):
        _must_be_empty(conn, f"SELECT 1 FROM transcripts t JOIN proteins p USING(protein_id) "
                       f"WHERE json_extract(t.data_json,'$.{field}') IS NOT NULL "
                       f"AND json_extract(t.data_json,'$.{field}') IS NOT json_extract(p.data_json,'$.{field}') LIMIT 1",
                       f"Transcript and protein sequence {field} disagree")
    conn.execute("CREATE TEMP TABLE source_transcripts(assembly_id TEXT,transcript_id TEXT,gene_id TEXT,"
                 "status TEXT,protein_length INTEGER,PRIMARY KEY(assembly_id,transcript_id))")
    for item in lock["selected_assemblies"]:
        metadata_rows: list[tuple] = []
        no_cds: list[tuple] = []
        for row in _rows(source.transcript_metadata(item)):
            if row["assembly_id"] != item["assembly_id"] or row["status"] not in {"valid", "no_cds"}:
                raise ValueError("Unexpected source transcript assembly or extraction status")
            metadata_rows.append((row["assembly_id"], row["transcript_id"], row["gene_id"],
                                  row["status"], int(row["protein_length"])))
            if row["status"] == "no_cds":
                no_cds.append((row["assembly_id"], row["transcript_id"], row["gene_id"], None,
                               "no_cds", row["reason"], _json(row)))
            if len(metadata_rows) >= 5000:
                conn.executemany("INSERT INTO source_transcripts VALUES(?,?,?,?,?)", metadata_rows)
                conn.executemany("INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", no_cds)
                metadata_rows.clear()
                no_cds.clear()
        conn.executemany("INSERT INTO source_transcripts VALUES(?,?,?,?,?)", metadata_rows)
        conn.executemany("INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", no_cds)
    _must_be_empty(conn, "SELECT 1 FROM source_transcripts s LEFT JOIN transcripts t USING(assembly_id,transcript_id) "
                   "LEFT JOIN proteins p USING(protein_id) WHERE t.transcript_id IS NULL OR s.gene_id!=t.gene_id "
                   "OR s.status!=t.status OR (s.status='valid' AND s.protein_length!=p.protein_length) LIMIT 1",
                   "Source transcript metadata disagrees with TF transcript mapping")
    _must_be_empty(conn, "SELECT 1 FROM transcripts t LEFT JOIN source_transcripts s USING(assembly_id,transcript_id) "
                   "WHERE s.transcript_id IS NULL LIMIT 1", "TF transcript absent from source metadata")
    conn.execute("DROP TABLE source_transcripts")
    conn.execute("CREATE TEMP TABLE source_mapping(assembly_id TEXT,transcript_id TEXT,protein_id TEXT,"
                 "protein_length INTEGER,sequence_md5 TEXT,sequence_sha256 TEXT,PRIMARY KEY(assembly_id,transcript_id))")
    _insert(conn, "INSERT INTO source_mapping VALUES(?,?,?,?,?,?)", (
        (r["assembly_id"], r["transcript_id"], r["global_unique_protein_id"], int(r["protein_length"]),
         r.get("sequence_md5"), r.get("sequence_sha256"))
        for r in _rows(source.file("metadata/transcript_to_selected_unique.tsv.gz"))))
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
    identities = {row[0]: (row[1], row[2]) for row in conn.execute(
        "SELECT protein_id,protein_length,json_extract(data_json,'$.sequence_md5') FROM proteins")}
    matches: list[tuple] = []
    terms: set[tuple] = set()
    with _text(source.file("results/unique/interproscan_global_unique.tsv.gz")) as handle:
        for line in csv.reader(handle, delimiter="\t"):
            if len(line) != 15:
                raise ValueError("InterProScan row must contain 15 columns")
            protein, md5, length, analysis, signature, description, start, end, *rest = line
            start_i, end_i = int(start), int(end)
            identity = identities.get(protein)
            if analysis not in APPLICATIONS or not identity or identity[0] != int(length):
                raise ValueError("Unexpected application or global protein identity in InterProScan row")
            if identity[1] is not None and md5 != identity[1]:
                raise ValueError("InterProScan sequence MD5 disagrees with global protein")
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
                conn.executemany("INSERT INTO matches VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)", matches)
                conn.executemany("INSERT OR IGNORE INTO protein_terms VALUES(?,?,?)", terms)
                matches.clear()
                terms.clear()
    conn.executemany("INSERT INTO matches VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)", matches)
    conn.executemany("INSERT OR IGNORE INTO protein_terms VALUES(?,?,?)", terms)
    _insert(conn, "INSERT INTO tf_evidence VALUES(NULL,?,?)", (
        (r["global_unique_protein_id"], _json(r))
        for r in _rows(source.file("results/tf_evidence.tsv.gz", tf=True))))
    conn.execute("CREATE TEMP TABLE no_match(protein_id TEXT PRIMARY KEY,protein_length INTEGER)")
    _insert(conn, "INSERT INTO no_match VALUES(?,?)", (
        (r["global_unique_protein_id"], int(r["protein_length"]))
        for r in _rows(source.file("results/unique/no_match_global_unique.tsv.gz"))))
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
                   "SUM(status='valid') valid,SUM(status='no_cds') exceptions,COUNT(DISTINCT protein_id) proteins "
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
        "transcripts_exceptions": conn.execute("SELECT COUNT(*) FROM transcripts WHERE status='no_cds'").fetchone()[0],
        "unique_proteins_total": conn.execute("SELECT COUNT(*) FROM proteins").fetchone()[0],
        "unique_proteins_selected_tf": conn.execute("SELECT COUNT(*) FROM proteins WHERE is_tf=1").fetchone()[0],
        "genes_selected_tf": conn.execute("SELECT COUNT(*) FROM genes WHERE json_extract(data_json,'$.has_selected_tf_isoform')='true'").fetchone()[0],
        "transcripts_selected_tf": conn.execute("SELECT COUNT(*) FROM transcripts t JOIN proteins p USING(protein_id) WHERE p.is_tf=1").fetchone()[0],
        "relevant_pfam_evidence_rows": conn.execute("SELECT COUNT(*) FROM tf_evidence").fetchone()[0],
        "annotation_hits": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
        "hit_proteins": conn.execute("SELECT COUNT(*) FROM proteins WHERE has_hit=1").fetchone()[0],
        "hit_transcripts": conn.execute("SELECT COUNT(*) FROM transcripts JOIN proteins USING(protein_id) WHERE has_hit=1").fetchone()[0],
    }
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
        actual = conn.execute("SELECT COUNT(*),SUM(status='valid'),SUM(status='no_cds') FROM transcripts WHERE assembly_id=?", (assembly,)).fetchone()
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


def _write_domain_download(source: Path, destination: Path) -> None:
    """Publish the source annotation columns except pathways, with stable gzip bytes."""
    with _text(source) as handle, destination.open("wb") as raw:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader)
        if header.count("pathways") != 1:
            raise ValueError("Source annotation download must have one pathways column")
        omitted = header.index("pathways")
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as output:
                writer = csv.writer(output, delimiter="\t", lineterminator="\n")
                writer.writerow(header[:omitted] + header[omitted + 1:])
                for row in reader:
                    if len(row) != len(header):
                        raise ValueError("Malformed source annotation download")
                    writer.writerow(row[:omitted] + row[omitted + 1:])


def _stage_downloads(conn: sqlite3.Connection, source: VerifiedSources, target: Path,
                     db_parent: Path) -> None:
    """Publish approved tables, omitting pathways from annotation downloads."""
    target.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[Path, str, str | None]] = []
    for (assembly,) in conn.execute("SELECT assembly_id FROM assemblies ORDER BY display_order"):
        for suffix, category in (("interproscan.tsv.gz", "annotations"), ("no_match.tsv.gz", "no_match"),
                                 ("extraction_exceptions.tsv.gz", "no_cds")):
            entries.append((source.file(f"results/by_assembly/{assembly}.{suffix}"), category, assembly))
        for suffix in ("tf_genes.tsv.gz", "tf_transcripts.tsv.gz"):
            entries.append((source.file(f"results/by_assembly/{assembly}.{suffix}", tf=True), "transcription_factors", assembly))
    for name in ("planttfdb_public_rules_v5.tsv", "supplemental_candidate_policy.tsv"):
        entries.append((source.file(f"rules/{name}", tf=True), "methods", None))
    for path, category, assembly in entries:
        filename = path.name.replace("interproscan.tsv.gz", "domains.tsv.gz") if category == "annotations" else path.name
        fd, temporary_name = tempfile.mkstemp(prefix=".download-", dir=target)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            if category == "annotations":
                _write_domain_download(path, temporary)
            else:
                shutil.copyfile(path, temporary)
                if _sha256(temporary) != _sha256(path):
                    raise ValueError("Source download changed during staging")
            digest = _sha256(temporary)
            # Hash the published bytes, including the projection, for safe reuse.
            destination = target / f"{digest[:16]}-{filename}"
            if destination.exists():
                if _sha256(destination) != digest:
                    raise ValueError("Existing staged download has incorrect content")
            else:
                os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        file_id = hashlib.sha256(f"{assembly or 'all'}:{path.name}".encode()).hexdigest()[:24]
        conn.execute("INSERT INTO downloads VALUES(?,?,?,?,?,?,?)", (
            file_id, filename, destination.relative_to(db_parent).as_posix(), category, assembly,
            destination.stat().st_size, digest))


def build_database(source_root: Path, output_db: Path, dataset_version: str, *,
                   downloads_dir: Path | None = None,
                   source_project_root: Path | None = None) -> dict[str, Any]:
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
    source = VerifiedSources(source_root, Path(source_project_root).resolve() if source_project_root else None)
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
        lock, summary, validation = _load_definitions(conn, source)
        _load_entities(conn, source, lock)
        _load_matches(conn, source)
        conn.executescript(INDEX_SQL)
        counts = _validate(conn, summary, validation)
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
    parser.add_argument("--source-project-root", type=Path,
                        help="Read locked transcript metadata from a verified copy of the parent project")
    args = parser.parse_args(argv)
    result = build_database(args.source_root, args.output_db, args.dataset_version,
                            downloads_dir=args.downloads_dir, source_project_root=args.source_project_root)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interface import genome_annotations as annotations
from interface.build_genome_annotations_db import INDEX_SQL, SCHEMA_SQL

A = "monoploid/DMv8.2"
B = "monoploid/E4-63"


def fixture_database(path: Path) -> Path:
    """Independent evidence graph: split isoforms, shared proteins, no-hit and no-CDS."""
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute("INSERT INTO metadata VALUES(1,3,'fixture-v1','2026-09-28',?)", (json.dumps({
            "counts": {}, "method": "Public PlantTFDB rules", "limitations": ["Not an official server result"],
        }),))
        for order, assembly in enumerate((A, B)):
            conn.execute("INSERT INTO assemblies VALUES(?,?,?,?)", (assembly, assembly.split("/")[-1], order, "{}"))
        for family, grade in (("WRKY", "A"), ("AP2", "A"), ("Homeobox TF candidate", "C"), ("WOX", "U")):
            conn.execute("INSERT INTO tf_families VALUES(?,?,?,?)", (family, grade, "unavailable" if grade == "U" else "public_rule", "{}"))
        proteins = [("P1", "WRKY", "A", 1), ("P2", "AP2", "A", 1), ("P3", "WRKY", "A", 1),
                    ("P4", "-", "-", 0), ("P5", "Homeobox TF candidate", "C", 1), ("P6", "WRKY;AP2", "A;A", 1)]
        for protein, family, grade, hit in proteins:
            status = "ambiguous_multifamily" if protein == "P6" else ("selected_tf" if family != "-" else "not_selected")
            selected = status == "selected_tf"
            raw = {"global_unique_protein_id": protein, "protein_length": "100", "tf_families": family,
                   "confidence_grades": grade, "decision_status": status, "is_tf_inclusive_result": str(selected).lower(),
                   "selection_basis": "public_rule", "decision_reason": "fixture evidence"}
            conn.execute("INSERT INTO proteins VALUES(?,?,?,?,?,?)", (protein, 100, hit, status, int(selected), json.dumps(raw)))
            for fam, level in zip(family.split(";"), grade.split(";")):
                if fam != "-":
                    conn.execute("INSERT INTO protein_families VALUES(?,?,?)", (protein, fam, level))
        entities = [(A, "split", [("split.1", "P1"), ("split.2", "P2"), ("split.noCDS", None)]),
                    (A, "whole", [("whole.1", "P3")]), (A, "nohit", [("nohit.1", "P4")]),
                    (A, "lnc", [("lnc-mRNA", None)]), (A, "candidate", [("candidate.1", "P5")]),
                    (A, "ambiguous", [("ambiguous.1", "P6")]), (B, "split", [("other.1", "P1")])]
        for assembly, gene, transcripts in entities:
            raw = {"assembly_id": assembly, "gene_id": gene, "global_gene_key": f"{assembly}::{gene}",
                   "total_transcript_count": str(len(transcripts)), "valid_transcript_count": str(sum(bool(p) for _, p in transcripts)),
                   "exception_transcript_count": str(sum(p is None for _, p in transcripts)),
                   "gene_decision": "selected_tf_family_conflict" if gene == "split" else "fixture",
                   "tf_family_union": "WRKY;AP2" if gene == "split" else "WRKY", "confidence_grades": "A",
                   "family_conflict": "true" if gene == "split" and assembly == A else "false"}
            conn.execute("INSERT INTO genes VALUES(?,?,?,?,?,?)", (assembly, gene, raw["gene_decision"], 0, int(raw["family_conflict"] == "true"), json.dumps(raw)))
            for transcript, protein in transcripts:
                conn.execute("INSERT INTO transcripts VALUES(?,?,?,?,?,?,?)", (assembly, transcript, gene, protein,
                             "valid" if protein else "no_cds", "" if protein else "No CDS", json.dumps({"transcript_id": transcript})))
        for protein, signature, start, analysis in [("P1", "PF_A", 1, "Pfam"), ("P1", "PF_A", 15, "Pfam"),
                ("P2", "PF_B", 1, "Pfam"), ("P3", "PF_A", 1, "Pfam"), ("P3", "PF_B", 25, "Pfam"),
                ("P3", "cd12", 30, "CDD"), ("P5", "PF00046", 1, "Pfam"), ("P6", "PF_A", 1, "Pfam")]:
            go = "GO:0000001(Pfam)" if analysis == "Pfam" else "GO:0000002(CDD)"
            conn.execute("INSERT INTO matches VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)", (protein, analysis, signature,
                         "DNA binding domain", start, start + 20, "1E-20", "T", "<RUN_DATE>", "IPR1", "Transcription factor", go))
            for kind, term in (("signature", signature), ("interpro", "IPR1"), ("go", go.split("(")[0])):
                conn.execute("INSERT OR IGNORE INTO protein_terms VALUES(?,?,?)", (protein, kind, term))
        conn.execute("INSERT INTO tf_evidence VALUES(NULL,'P1',?)", (json.dumps({"global_unique_protein_id": "P1", "signature_accession": "PF_A", "start": 1, "end": 21, "rule_roles": "DNA-binding:WRKY"}),))
        conn.execute("INSERT INTO matches_fts(matches_fts) VALUES('rebuild')")
        conn.executescript(INDEX_SQL)
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = fixture_database(tmp_path / "annotations.sqlite")
    monkeypatch.setenv("GENOME_ANNOTATIONS_DB_PATH", str(db))
    app = FastAPI()
    app.include_router(annotations.router)
    with TestClient(app) as client:
        yield client


def query(client, **body):
    response = client.post("/api/genome-annotations/query", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_domains_must_cooccur_in_one_isoform_and_raw_gene_decision_survives(client):
    result = query(client, signatures=["PF_A", "PF_B"])
    assert [r["geneId"] for r in result["items"]] == ["whole"]
    selected = query(client, assemblyIds=[A], ids=["split"], signatures=["PF_A"])["items"][0]
    assert selected["matchedTranscriptIds"] == ["split.1"]
    assert selected["transcriptCount"] == 3
    assert selected["familyConflict"] is True
    assert selected["tfFamilies"] == ["WRKY", "AP2"]
    assert selected["annotationStatus"] == "mixed"


def test_tf_and_domain_use_same_protein_and_analysis_scopes_evidence(client):
    result = query(client, tfFamilies=["WRKY"], signatures=["PF_B"], tfStatus="selected")
    assert [r["geneId"] for r in result["items"]] == ["whole"]
    assert query(client, analyses=["Pfam"], signatures=["cd12"])["total"] == 0
    assert query(client, analyses=["CDD"], signatures=["cd12"])["total"] == 1
    assert query(client, analyses=["Pfam"], goIds=["GO:0000002"])["total"] == 0
    assert query(client, analyses=["CDD"], goIds=["GO:0000002"])["total"] == 1
    with sqlite3.connect(annotations.database_path()) as conn:
        conn.execute("UPDATE matches SET signature_description='Exclusive ATPase description' WHERE analysis='CDD'")
        conn.execute("INSERT INTO matches_fts(matches_fts) VALUES('rebuild')")
    assert query(client, analyses=["Pfam"], q="Exclusive ATPase")["total"] == 0
    assert query(client, analyses=["CDD"], q="Exclusive ATPase")["total"] == 1


def test_no_cds_no_hit_and_ambiguous_candidates_remain_distinct(client):
    assert query(client, view="transcripts", annotationStatus="no_cds")["total"] == 2
    nohit = query(client, annotationStatus="no_match")["items"]
    assert len(nohit) == 1 and nohit[0]["geneId"] == "nohit"
    assert query(client, tfStatus="unassessable")["total"] == 2
    assert query(client, tfStatus="ambiguous", tfFamilies=["WRKY"])["items"][0]["geneId"] == "ambiguous"
    assert query(client, grades=["C"], tfStatus="selected")["items"][0]["geneId"] == "candidate"
    detail = client.get("/api/genome-annotations/transcripts/lnc-mRNA", params={"assembly": A}).json()
    assert detail["matches"] == []
    assert detail["transcript"]["decisionStatus"] == "unassessable_no_valid_protein"


def test_exact_ids_ambiguity_unknown_and_filter_reports(client):
    result = query(client, ids=["split", "nohit", "missing", "split"], signatures=["PF_A"])
    assert result["total"] == 2  # One gene in each assembly, with shared global protein.
    assert result["idReport"] == {"ambiguousIds": ["split"], "unmatchedIds": ["missing"], "filteredIds": ["nohit"]}
    assert query(client, ids=["split.2"])["items"][0]["matchedTranscriptIds"] == ["split.2"]
    assert query(client, ids=["P1"])["total"] == 2
    assert query(client, ids=["' OR 1=1 --"])["total"] == 0


def test_keyword_pagination_details_and_overlapping_hits(client):
    boundary = query(client, limit=2, offset=5)
    assert boundary["total"] == 7
    assert [(row["assemblyId"], row["geneId"]) for row in boundary["items"]] == [(A, "whole"), (B, "split")]
    assert query(client, offset=7)["items"] == []
    assert query(client, conflict="family")["total"] == 1
    result = query(client, q="DNA binding", limit=1)
    assert result["total"] == 5 and result["hasMore"] is True
    next_page = query(client, q="DNA binding", limit=1, offset=1)
    assert next_page["items"][0]["geneId"] != result["items"][0]["geneId"]
    detail = client.get("/api/genome-annotations/transcripts/split.1", params={"assembly": A}).json()
    assert [(h["start"], h["end"]) for h in detail["matches"]] == [(1, 21), (15, 35)]
    assert detail["tfEvidence"][0]["rule_roles"] == "DNA-binding:WRKY"
    assert len(client.get("/api/genome-annotations/genes/split", params={"assembly": A}).json()["transcripts"]) == 3


def unpack(response):
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name).decode() for name in archive.namelist()}


def test_export_all_pages_keeps_matched_isoforms_and_all_their_evidence(client):
    body = {"datasetVersion": "fixture-v1", "query": {"assemblyIds": [A], "signatures": ["PF_A"], "limit": 1},
            "tables": ["genes", "transcripts", "domains", "tf_decisions", "tf_evidence"]}
    files = unpack(client.post("/api/genome-annotations/export", json=body))
    meta = json.loads(files["metadata.json"])
    assert meta["tableCounts"]["genes"] == 3
    rows = list(csv.DictReader(io.StringIO(files["transcripts.tsv"]), delimiter="\t"))
    assert {r["transcript_id"] for r in rows} == {"split.1", "whole.1", "ambiguous.1"}
    domains = list(csv.DictReader(io.StringIO(files["domains.tsv"]), delimiter="\t"))
    assert len(domains) == 6
    assert any(r["signature_accession"] == "PF_B" and r["matches_domain_filter"] == "False" for r in domains)
    assert meta["tableCounts"]["domains"] == len(domains)
    assert {row["protein_length"] for row in domains} == {"100"}
    decisions = list(csv.DictReader(io.StringIO(files["tf_decisions.tsv"]), delimiter="\t"))
    split = next(row for row in decisions if row["transcript_id"] == "split.1")
    assert split["family_conflict"] == "true" and split["tf_family_union"] == "WRKY;AP2"
    assert "limit" not in meta["query"] and "offset" not in meta["query"]


def test_export_selection_versions_empty_tables_and_browser_adapter(client):
    body = {"datasetVersion": "fixture-v1", "query": {"ids": ["split", "missing"]}, "format": "csv",
            "selection": [{"assemblyId": B, "geneId": "split"}], "tables": ["genes", "domains"]}
    files = unpack(client.post("/api/genome-annotations/export-download", data={"payload": json.dumps(body)}))
    assert len(list(csv.DictReader(io.StringIO(files["genes.csv"])))) == 1
    assert "missing\tnot_found" in files["id_report.tsv"]
    body["datasetVersion"] = "old"
    assert client.post("/api/genome-annotations/export", json=body).status_code == 409
    body["datasetVersion"] = "fixture-v1"
    body["selection"] = [{"assemblyId": A, "transcriptId": "split.1"}]
    assert client.post("/api/genome-annotations/export", json=body).status_code == 422
    body["selection"] = []
    body["query"] = {"ids": ["missing"]}
    files = unpack(client.post("/api/genome-annotations/export", json=body))
    assert json.loads(files["metadata.json"])["tableCounts"]["genes"] == 0
    assert files["genes.csv"].startswith("assembly_id,gene_id,")


def test_domain_details_and_exports_exclude_pathways(client):
    response = client.get("/api/genome-annotations/transcripts/split.1", params={"assembly": A})
    assert response.status_code == 200
    hits = response.json()["matches"]
    assert len(hits) == 2
    assert all(set(hit) == {"hitId", "analysis", "signatureAccession", "signatureDescription",
                           "start", "end", "score", "status", "date", "interproAccession",
                           "interproDescription", "goTerms"} for hit in hits)
    for format_, delimiter in (("tsv", "\t"), ("csv", ",")):
        body = {"datasetVersion": "fixture-v1", "query": {"assemblyIds": [A], "ids": ["split.1"]},
                "tables": ["domains"], "format": format_}
        for endpoint in ("export", "export-download"):
            response = client.post("/api/genome-annotations/" + endpoint,
                                   **({"json": body} if endpoint == "export" else {"data": {"payload": json.dumps(body)}}))
            files = unpack(response)
            reader = csv.DictReader(io.StringIO(files[f"domains.{format_}"]), delimiter=delimiter)
            assert reader.fieldnames == annotations.EXPORT_FIELDS["domains"]
            assert "pathways" not in reader.fieldnames and "pathway_set_id" not in reader.fieldnames
            rows = list(reader)
            assert len(rows) == 2 and [row["start"] for row in rows] == ["1", "15"]
            assert all(row["go_terms"] == "GO:0000001(Pfam)" for row in rows)


def test_public_metadata_validation_and_errors(client, monkeypatch, tmp_path):
    response = client.get("/api/genome-annotations/metadata")
    assert response.status_code == 200 and response.json()["schemaVersion"] == 3
    assert str(tmp_path) not in response.text
    assert client.post("/api/genome-annotations/query", json={"assemblyIds": ["DM8.2"]}).status_code == 400
    for payload in ({"limit": 501}, {"limit": 0}, {"ids": ["x"] * 5001}, {"serverPath": "/tmp"},
                    {"ids": ["bad\nID"]}, {"analyses": ["Pfam"] * 5000}, {"grades": ["A"] * 5000}):
        assert client.post("/api/genome-annotations/query", json=payload).status_code == 422
    assert client.post("/api/genome-annotations/query", json={"datasetVersion": "old"}).status_code == 409
    monkeypatch.setenv("GENOME_ANNOTATIONS_DB_PATH", str(tmp_path / "secret-name.sqlite"))
    response = client.get("/api/genome-annotations/metadata")
    assert response.status_code == 503 and "secret-name" not in response.text


@pytest.mark.parametrize("schema", [1, 2])
def test_legacy_database_requires_matching_schema3_release(client, schema):
    with sqlite3.connect(annotations.database_path()) as conn:
        conn.execute("UPDATE metadata SET schema_version=?", (schema,))
    assert client.get("/api/genome-annotations/metadata").status_code == 503


def test_readonly_connections_and_download_allowlist(client, monkeypatch, tmp_path):
    with annotations.connect_db() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM genes")
    db = annotations.database_path()
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO downloads VALUES('bad','private.txt','../private.txt','methods',NULL,6,'digest')")
    (tmp_path.parent / "private.txt").write_text("secret")
    assert client.get("/api/genome-annotations/downloads/bad").status_code == 503
    assert client.get("/api/genome-annotations/downloads/unknown").status_code == 404


def test_router_integration_does_not_refresh_agent_activity():
    from interface import app as interface_app
    from starlette.requests import Request
    for endpoint in ("query", "export", "export-download", "metadata", "tf-families"):
        assert not interface_app._should_refresh_activity_for_request(Request({
            "type": "http", "method": "POST", "path": "/api/genome-annotations/" + endpoint,
        }))
    assert any(route.path == "/api/genome-annotations/query" for route in interface_app.app.routes)


def test_interrupted_export_closes_database_without_creating_files(tmp_path, monkeypatch):
    import hashlib

    db = fixture_database(tmp_path / "annotations.sqlite")
    extra_rows = 5_000
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO matches VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)", (
            ("P1", "Pfam", "PF_A", hashlib.sha256(str(index).encode()).hexdigest(),
             1, 21, "1E-20", "T", "2026-09-28", "IPR1", "DNA binding domain", "GO:0000001")
            for index in range(extra_rows)
        ))
    files_before = set(tmp_path.rglob("*"))
    real_connect = annotations.connect_db
    connections = []

    def track_connection(path=None):
        connection = real_connect(path)
        connections.append(connection)
        return connection

    real_rows = annotations._export_rows
    emitted_rows = 0

    def track_rows(*args):
        nonlocal emitted_rows
        for row in real_rows(*args):
            emitted_rows += 1
            yield row

    monkeypatch.setattr(annotations, "connect_db", track_connection)
    monkeypatch.setattr(annotations, "_export_rows", track_rows)
    request = annotations.ExportRequest(
        datasetVersion="fixture-v1", tables=["domains"],
        query=annotations.AnnotationQuery(assemblyIds=[A], view="transcripts", ids=["split.1"]),
    )
    stream = annotations._export_zip(db, request)
    try:
        first_chunk = next(stream)
        assert len(first_chunk) >= 65_536
        assert 0 < emitted_rows < extra_rows + 2  # Cancellation occurs during the table, before completion.
        assert len(connections) == 1
        assert connections[0].execute("SELECT 1").fetchone()[0] == 1
    finally:
        stream.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")
    assert set(tmp_path.rglob("*")) == files_before


def test_prepared_export_stays_on_original_release_after_symlink_switch(tmp_path, monkeypatch):
    import asyncio

    old_release = tmp_path / "releases/old"
    new_release = tmp_path / "releases/new"
    old_release.mkdir(parents=True)
    new_release.mkdir()
    fixture_database(old_release / "annotations.sqlite")
    new_db = fixture_database(new_release / "annotations.sqlite")
    with sqlite3.connect(new_db) as conn:
        conn.execute("UPDATE metadata SET dataset_version='fixture-v2'")
        conn.execute("UPDATE genes SET data_json=json_set(data_json,'$.gene_decision','new release')")
    current = tmp_path / "current"
    current.symlink_to(old_release, target_is_directory=True)
    monkeypatch.setenv("GENOME_ANNOTATIONS_DB_PATH", str(current / "annotations.sqlite"))
    response = annotations.prepare_export(annotations.ExportRequest(datasetVersion="fixture-v1", tables=["genes"]))

    next_link = tmp_path / "next"
    next_link.symlink_to(new_release, target_is_directory=True)
    next_link.replace(current)
    assert annotations.metadata()["datasetVersion"] == "fixture-v2"

    async def consume():
        return b"".join([chunk async for chunk in response.body_iterator])

    with zipfile.ZipFile(io.BytesIO(asyncio.run(consume()))) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["datasetVersion"] == "fixture-v1"
        assert metadata["tableCounts"]["genes"] == 7
        assert "new release" not in archive.read("genes.tsv").decode()
    assert response.headers["x-annotation-dataset-version"] == "fixture-v1"

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface import app as interface_app_mod
from interface import gene_catalog as gene_catalog_mod
from interface.build_gene_catalog_db import create_schema


def _sequence_row(
    transcript_pk: int,
    sequence_type: str,
    sequence: str,
    *,
    seqid: str | None = None,
    region_start: int | None = None,
    region_end: int | None = None,
    strand: str | None = None,
    anchor_type: str | None = None,
    anchor_pos: int | None = None,
    requested_length: int | None = None,
    was_truncated: bool = False,
) -> tuple[object, ...]:
    encoded = sequence.encode("ascii")
    return (
        transcript_pk,
        sequence_type,
        zlib.compress(encoded),
        len(sequence),
        hashlib.sha256(encoded).hexdigest(),
        seqid,
        region_start,
        region_end,
        strand,
        anchor_type,
        anchor_pos,
        requested_length,
        int(was_truncated),
    )


def _write_catalog_db(path: Path, *, schema_version: int = 1) -> Path:
    with sqlite3.connect(path) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO catalog_sources VALUES (?, ?, ?, ?, ?)",
            ("function_predictions", "predictions.jsonl", "0" * 64, 1, 1),
        )
        conn.execute(
            """
            INSERT INTO catalog_metadata VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schema_version,
                "catalog-2026-07-26",
                "DMv8.2",
                "DMv8.2",
                "2026-07-26T08:00:00Z",
                json.dumps(
                    {
                        "genes": 3,
                        "transcripts": 4,
                        "protein_similarity_hits": 1,
                    }
                ),
                "prediction-1",
                "Catalog-wide expression statistic.",
            ),
        )
        conn.executemany(
            "INSERT INTO genes VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "DM8C01G00010", "dm8c01g00010", "chr01", 100, 900, "+"),
                (2, "DM8C01G00020", "dm8c01g00020", "chr01", 1000, 1800, "-"),
                (3, "StABC%Literal", "stabc%literal", "chr02", 50, 500, "+"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO gene_identifiers(
              gene_pk, identifier_type, identifier, identifier_norm,
              qualifier_type, qualifier_value, display_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "gene_id", "DM8C01G00010", "dm8c01g00010", None, None, 0),
                (1, "gene_symbol", "StABC", "stabc", None, None, 1),
                (
                    1,
                    "reported_id",
                    "PGSC0003DMG400010",
                    "pgsc0003dmg400010",
                    "blast_identity_pct",
                    99.8,
                    2,
                ),
                (2, "gene_id", "DM8C01G00020", "dm8c01g00020", None, None, 0),
                (2, "gene_symbol", "StABC", "stabc", None, None, 1),
                (3, "gene_id", "StABC%Literal", "stabc%literal", None, None, 0),
            ],
        )
        conn.executemany(
            "INSERT INTO transcripts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (11, 1, "DM8C01T00010.1", "dm8c01t00010.1", 1, 0, "chr01", 100, 900, "+"),
                (12, 1, "DM8C01T00010.2", "dm8c01t00010.2", 0, 1, "chr01", 120, 850, "+"),
                (21, 2, "DM8C01T00020.1", "dm8c01t00020.1", 1, 0, "chr01", 1000, 1800, "-"),
                (31, 3, "StABCT001", "stabct001", 1, 0, "chr02", 50, 500, "+"),
            ],
        )
        conn.execute(
            "INSERT INTO gene_descriptions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                11,
                "A predicted ATPase.",
                "F3",
                "Supported by homolog evidence.",
                json.dumps(
                    {
                        "homologs": [{"species": "Arabidopsis"}],
                        "expression_by_tissue": [
                            {
                                "tissue": "leaf",
                                "mean_tpm": 12.5,
                                "sd_tpm": 1.25,
                                "n_sources": 3,
                                "n_runs": 9,
                            }
                        ],
                        "expression_statistic": "Description evidence statistic.",
                    }
                ),
                "primary",
                "function_predictions",
            ),
        )
        conn.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?)",
            (1, "10.1000/example", "10.1000/example", "Potato ATPases"),
        )
        conn.executemany(
            "INSERT INTO paper_local_ids VALUES (?, ?, ?)",
            [
                ("PAPER-1", 1, "Potato ATPases"),
                ("PAPER-2", 1, "Potato ATPases duplicate"),
            ],
        )
        conn.executemany(
            "INSERT INTO gene_paper_refs VALUES (?, ?, ?)",
            [(1, "PAPER-1", 0), (1, "PAPER-2", 1)],
        )
        conn.execute(
            "INSERT INTO gene_annotations VALUES (?, ?, ?, ?)",
            (
                1,
                json.dumps([{"id": "GO:0005524", "name": "ATP binding"}]),
                json.dumps([{"id": "K00001"}]),
                json.dumps([{"id": "IPR000001"}]),
            ),
        )
        conn.execute(
            "INSERT INTO protein_similarity_hits VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "UniRef100", "UniRef100_A0A", 98.5, 1e-40, "ATPase", 0),
        )
        conn.executemany(
            """
            INSERT INTO transcript_sequences VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                _sequence_row(11, "cds", "ATGGCC"),
                _sequence_row(
                    11,
                    "promoter_atg_upstream_2000",
                    "AACCGGTT",
                    seqid="chr01",
                    region_start=92,
                    region_end=99,
                    strand="+",
                    anchor_type="ATG",
                    anchor_pos=100,
                    requested_length=2000,
                    was_truncated=True,
                ),
                _sequence_row(12, "protein", "MA"),
            ],
        )
        conn.commit()
    return path


def _client_for_catalog(monkeypatch, db_path: Path) -> TestClient:
    monkeypatch.setenv("GENE_CATALOG_DB_PATH", str(db_path))
    return TestClient(interface_app_mod.app)


def test_gene_catalog_metadata_and_ranked_literal_search(monkeypatch, tmp_path) -> None:
    client = _client_for_catalog(monkeypatch, _write_catalog_db(tmp_path / "catalog.sqlite"))
    try:
        catalog = client.get("/api/v1/gene-catalog")
        assert catalog.status_code == 200, catalog.text
        assert catalog.json()["catalogVersion"] == "catalog-2026-07-26"
        assert catalog.json()["counts"]["proteinSimilarityHits"] == 1
        assert (
            catalog.json()["expressionStatistic"]
            == "Catalog-wide expression statistic."
        )
        assert catalog.json()["sequenceTypes"] == ["cds", "protein", "genomic", "promoter"]
        assert str(tmp_path) not in catalog.text

        ambiguous = client.get("/api/v1/genes/search", params={"q": "stabc", "limit": 10})
        assert ambiguous.status_code == 200, ambiguous.text
        ambiguous_genes = ambiguous.json()["genes"]
        assert [row["geneId"] for row in ambiguous_genes] == [
            "DM8C01G00010",
            "DM8C01G00020",
            "StABC%Literal",
        ]
        assert ambiguous_genes[0]["descriptionExcerpt"] == "A predicted ATPase."
        assert ambiguous_genes[0]["descriptionIsTruncated"] is False
        assert ambiguous_genes[1]["descriptionExcerpt"] == ""
        assert [row["matchedBy"] for row in ambiguous_genes] == [
            "gene_symbol",
            "gene_symbol",
            "gene_id",
        ]

        normalized = client.get(
            "/api/v1/genes/search", params={"q": "  ＳｔＡＢＣ\t"}
        )
        assert normalized.status_code == 200, normalized.text
        assert [row["geneId"] for row in normalized.json()["genes"][:2]] == [
            "DM8C01G00010",
            "DM8C01G00020",
        ]

        reported = client.get(
            "/api/v1/genes/search", params={"q": "PGSC0003DMG400010"}
        )
        assert reported.status_code == 200, reported.text
        reported_hit = reported.json()["genes"][0]
        assert reported_hit["matchedBy"] == "reported_id"
        assert reported_hit["qualifier"] == {
            "type": "blast_identity_pct",
            "value": 99.8,
        }

        literal = client.get("/api/v1/genes/search", params={"q": "StABC%"})
        assert literal.status_code == 200, literal.text
        assert [row["geneId"] for row in literal.json()["genes"]] == [
            "StABC%Literal"
        ]
    finally:
        client.close()


def test_gene_search_merges_ranked_bidirectional_st_symbol_variants(
    monkeypatch, tmp_path
) -> None:
    db_path = _write_catalog_db(tmp_path / "catalog.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO genes VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (4, "GeneBareExact", "genebareexact", "chr03", 1, 10, "+"),
                (5, "GeneStExact", "genestexact", "chr03", 11, 20, "+"),
                (6, "GeneBarePrefix", "genebareprefix", "chr03", 21, 30, "+"),
                (7, "GeneStPrefix", "genestprefix", "chr03", 31, 40, "+"),
                (8, "GeneBoth", "geneboth", "chr03", 41, 50, "+"),
                (9, "GeneReported", "genereported", "chr03", 51, 60, "+"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO gene_identifiers(
              gene_pk, identifier_type, identifier, identifier_norm,
              qualifier_type, qualifier_value, display_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (4, "gene_symbol", "SN2", "sn2", None, None, 0),
                (5, "gene_symbol", "StSN2", "stsn2", None, None, 0),
                (6, "gene_symbol", "SN2A", "sn2a", None, None, 0),
                (7, "gene_symbol", "StSN2A", "stsn2a", None, None, 0),
                (8, "gene_symbol", "DUO", "duo", None, None, 0),
                (8, "gene_symbol", "StDUO", "stduo", None, None, 1),
                (9, "reported_id", "StONLY", "stonly", None, None, 0),
            ],
        )
        conn.commit()

    client = _client_for_catalog(monkeypatch, db_path)
    try:
        prefixed = client.get(
            "/api/v1/genes/search", params={"q": "StSN2", "limit": 10}
        )
        assert prefixed.status_code == 200, prefixed.text
        assert [row["geneId"] for row in prefixed.json()["genes"]] == [
            "GeneStExact",
            "GeneStPrefix",
            "GeneBareExact",
            "GeneBarePrefix",
        ]

        bare = client.get(
            "/api/v1/genes/search", params={"q": "SN2", "limit": 10}
        )
        assert bare.status_code == 200, bare.text
        assert [row["geneId"] for row in bare.json()["genes"]] == [
            "GeneBareExact",
            "GeneBarePrefix",
            "GeneStExact",
            "GeneStPrefix",
        ]

        first_page = client.get(
            "/api/v1/genes/search", params={"q": "StSN2", "limit": 2}
        ).json()
        second_page = client.get(
            "/api/v1/genes/search",
            params={"q": "StSN2", "limit": 2, "offset": 2},
        ).json()
        assert [row["geneId"] for row in first_page["genes"]] == [
            "GeneStExact",
            "GeneStPrefix",
        ]
        assert first_page["hasMore"] is True
        assert [row["geneId"] for row in second_page["genes"]] == [
            "GeneBareExact",
            "GeneBarePrefix",
        ]
        assert second_page["hasMore"] is False

        deduplicated = client.get(
            "/api/v1/genes/search", params={"q": "DUO", "limit": 10}
        ).json()
        assert [row["geneId"] for row in deduplicated["genes"]] == ["GeneBoth"]
        assert deduplicated["genes"][0]["matchedIdentifier"] == "DUO"

        symbol_only = client.get(
            "/api/v1/genes/search", params={"q": "ONLY", "limit": 10}
        ).json()
        assert symbol_only["genes"] == []
    finally:
        client.close()


def test_gene_search_description_excerpt_has_a_stable_limit(monkeypatch, tmp_path) -> None:
    db_path = _write_catalog_db(tmp_path / "catalog.sqlite")
    long_description = " ".join(["potato"] * 100)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE gene_descriptions SET predicted_function = ? WHERE gene_pk = 1",
            (long_description,),
        )
        conn.commit()

    client = _client_for_catalog(monkeypatch, db_path)
    try:
        response = client.get("/api/v1/genes/search", params={"q": "DM8C01G00010"})
        assert response.status_code == 200, response.text
        gene = response.json()["genes"][0]
        assert len(gene["descriptionExcerpt"]) <= 280
        assert gene["descriptionExcerpt"].endswith("...")
        assert gene["descriptionIsTruncated"] is True
    finally:
        client.close()


def test_gene_detail_returns_summaries_without_raw_payloads(monkeypatch, tmp_path) -> None:
    client = _client_for_catalog(monkeypatch, _write_catalog_db(tmp_path / "catalog.sqlite"))
    try:
        response = client.get("/api/v1/genes/dm8c01g00010")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["gene"]["geneId"] == "DM8C01G00010"
        assert payload["gene"]["symbols"] == ["StABC"]
        assert payload["description"] == {
            "transcriptId": "DM8C01T00010.1",
            "predictedFunction": "A predicted ATPase.",
            "reliabilityGrade": "F3",
        }
        assert payload["papers"] == [
            {
                "doi": "10.1000/example",
                "title": "Potato ATPases",
                "localPaperIds": ["PAPER-1", "PAPER-2"],
            }
        ]
        assert payload["annotations"]["goTerms"][0]["id"] == "GO:0005524"
        assert payload["annotations"]["keggTerms"][0]["id"] == "K00001"
        assert payload["proteinSimilarityHits"][0]["subjectId"] == "UniRef100_A0A"
        assert payload["expression"]["statistic"] == "Description evidence statistic."
        assert payload["expression"]["tissues"][0]["meanTpm"] == 12.5
        assert payload["transcripts"][0]["sequenceTypes"] == ["cds", "promoter"]
        assert {row["sequenceType"] for row in payload["sequenceAvailability"]} == {
            "cds",
            "protein",
            "promoter",
        }
        assert "evidence" not in payload["description"]
        assert "gradeReason" not in payload["description"]
        assert "sourceRun" not in payload["description"]
        assert "sourceKey" not in payload["description"]
        assert all("sequence" not in row for row in payload["sequenceAvailability"])
    finally:
        client.close()


def test_gene_evidence_and_promoter_sequence_are_loaded_on_demand(
    monkeypatch, tmp_path
) -> None:
    client = _client_for_catalog(monkeypatch, _write_catalog_db(tmp_path / "catalog.sqlite"))
    try:
        evidence = client.get(
            "/api/v1/genes/DM8C01G00010/description/evidence"
        )
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["evidence"]["homologs"] == [
            {"species": "Arabidopsis"}
        ]
        assert evidence.json()["sourceRun"] == "primary"
        assert evidence.json()["sourceKey"] == "function_predictions"

        promoter = client.get("/api/v1/genes/DM8C01G00010/sequences/promoter")
        assert promoter.status_code == 200, promoter.text
        assert promoter.json()["sequenceType"] == "promoter"
        assert promoter.json()["sequence"] == "AACCGGTT"
        assert promoter.json()["location"] == {
            "seqid": "chr01",
            "start": 92,
            "end": 99,
            "strand": "+",
        }
        assert promoter.json()["anchor"] == {
            "type": "ATG",
            "position": 100,
            "requestedLength": 2000,
            "wasTruncated": True,
        }

        protein = client.get(
            "/api/v1/genes/DM8C01G00010/sequences/protein",
            params={"transcript_id": "dm8c01t00010.2"},
        )
        assert protein.status_code == 200, protein.text
        assert protein.json()["transcriptId"] == "DM8C01T00010.2"
        assert protein.json()["sequence"] == "MA"
        assert protein.json()["location"] is None
        assert protein.json()["anchor"] is None
    finally:
        client.close()


def test_gene_catalog_errors_are_generic(monkeypatch, tmp_path) -> None:
    missing_path = tmp_path / "missing.sqlite"
    client = _client_for_catalog(monkeypatch, missing_path)
    try:
        missing = client.get("/api/v1/gene-catalog")
        assert missing.status_code == 503
        assert missing.json() == {"detail": gene_catalog_mod.CATALOG_UNAVAILABLE_DETAIL}
        assert str(missing_path) not in missing.text
    finally:
        client.close()

    incompatible_path = _write_catalog_db(
        tmp_path / "incompatible.sqlite", schema_version=2
    )
    client = _client_for_catalog(monkeypatch, incompatible_path)
    try:
        incompatible = client.get("/api/v1/gene-catalog")
        assert incompatible.status_code == 503
        assert incompatible.json() == {
            "detail": gene_catalog_mod.CATALOG_UNAVAILABLE_DETAIL
        }
    finally:
        client.close()


def test_gene_catalog_validates_requests_and_missing_records(monkeypatch, tmp_path) -> None:
    client = _client_for_catalog(monkeypatch, _write_catalog_db(tmp_path / "catalog.sqlite"))
    try:
        assert client.get("/api/v1/genes/search").status_code == 400
        assert client.get(
            "/api/v1/genes/search", params={"q": "abc", "limit": 101}
        ).status_code == 400
        assert client.get(
            "/api/v1/genes/search", params={"q": "abc", "offset": 10_001}
        ).status_code == 400
        assert client.get("/api/v1/genes/missing").status_code == 404
        assert client.get(
            "/api/v1/genes/DM8C01G00010/sequences/rna"
        ).status_code == 400
        assert client.get(
            "/api/v1/genes/DM8C01G00010/sequences/protein",
            params={"transcript_id": "missing"},
        ).status_code == 404
    finally:
        client.close()


def test_gene_catalog_connection_is_read_only(monkeypatch, tmp_path) -> None:
    db_path = _write_catalog_db(tmp_path / "catalog.sqlite")
    monkeypatch.setenv("GENE_CATALOG_DB_PATH", str(db_path))
    with gene_catalog_mod.connect_db() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO genes VALUES (99, 'x', 'x', 'chr1', 1, 2, '+')")


def test_gene_catalog_sequence_integrity_failure_is_generic(monkeypatch, tmp_path) -> None:
    db_path = _write_catalog_db(tmp_path / "catalog.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE transcript_sequences SET sha256 = ? WHERE transcript_pk = 11 AND sequence_type = 'cds'",
            ("0" * 64,),
        )
        conn.commit()

    client = _client_for_catalog(monkeypatch, db_path)
    try:
        response = client.get("/api/v1/genes/DM8C01G00010/sequences/cds")
        assert response.status_code == 500
        assert response.json() == {"detail": gene_catalog_mod.QUERY_FAILED_DETAIL}
        assert "checksum" not in response.text
    finally:
        client.close()


def test_gene_catalog_api_does_not_refresh_runtime_activity() -> None:
    class Request:
        def __init__(self, path: str) -> None:
            self.scope = {"path": path}

    for path in (
        "/api/v1/gene-catalog",
        "/api/v1/genes/search",
        "/api/v1/genes/DM8C01G00010",
        "/api/v1/genes/DM8C01G00010/sequences/cds",
    ):
        assert interface_app_mod._should_refresh_activity_for_request(Request(path)) is False

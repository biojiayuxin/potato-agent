from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interface import app as interface_app_mod
from interface import pan_genome as pan_genome_mod
from interface.build_pan_genome_db import BuildConfig, build_database


def _write_database(tmp_path: Path) -> Path:
    source = tmp_path / "Orthogroups.tsv"
    source.write_text(
        "Orthogroup\tGenomeA\tGenomeB\tGenomeC\n"
        "OG0001\tA_gene1, A_gene2\tShared_gene\tC_gene1\n"
        "OG0002\tA_gene3\tB_gene1, B_gene2\tShared_gene\n"
        "OG0003\t\tB_gene3\tC_gene2\n",
        encoding="utf-8",
    )
    output = tmp_path / "pan_genome.sqlite"
    build_database(
        BuildConfig(
            source_tsv=source,
            output_db=output,
            dataset_version="fixture-v1",
            expected_genome_count=3,
            expected_orthogroup_count=3,
            expected_gene_membership_count=10,
            expected_core_orthogroup_count=2,
            expected_soft_core_orthogroup_count=0,
            expected_dispensable_orthogroup_count=1,
            expected_private_orthogroup_count=0,
        )
    )
    return output


def _client(monkeypatch, db_path: Path) -> TestClient:
    monkeypatch.setenv("PAN_GENOME_DB_PATH", str(db_path))
    return TestClient(interface_app_mod.app)


def test_pan_genome_public_metadata_and_genomes(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _write_database(tmp_path))
    try:
        metadata = client.get("/api/pan-genome/metadata")
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["datasetVersion"] == "fixture-v1"
        assert metadata.json()["counts"] == {
            "genomes": 3,
            "orthogroups": 3,
            "geneMemberships": 10,
            "occupiedCells": 8,
            "orthogroupCategories": {
                "core": 2,
                "soft-core": 0,
                "dispensable": 1,
                "private": 0,
            },
        }
        assert metadata.json()["pagination"]["maxMemberLimit"] == 1000
        assert str(tmp_path) not in metadata.text

        genomes = client.get("/api/pan-genome/genomes")
        assert genomes.status_code == 200, genomes.text
        assert genomes.json()["count"] == 3
        assert genomes.json()["genomes"][0] == {
            "name": "GenomeA",
            "displayOrder": 0,
            "geneCount": 3,
            "orthogroupCount": 2,
        }
    finally:
        client.close()


def test_pan_genome_gene_lookup_supports_optional_genome(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _write_database(tmp_path))
    try:
        shared = client.get(
            "/api/pan-genome/genes/lookup", params={"gene_id": "shared_GENE"}
        )
        assert shared.status_code == 200, shared.text
        assert shared.json()["matches"] == [
            {
                "genome": "GenomeB",
                "geneId": "Shared_gene",
                "orthogroup": "OG0001",
                "category": "core",
            },
            {
                "genome": "GenomeC",
                "geneId": "Shared_gene",
                "orthogroup": "OG0002",
                "category": "core",
            },
        ]

        scoped = client.get(
            "/api/pan-genome/genes/lookup",
            params={"gene_id": "shared_gene", "genome": "genomec"},
        )
        assert scoped.status_code == 200, scoped.text
        assert scoped.json()["matches"][0]["orthogroup"] == "OG0002"
    finally:
        client.close()


def test_pan_genome_orthogroup_detail_and_paginated_members(
    monkeypatch, tmp_path
) -> None:
    client = _client(monkeypatch, _write_database(tmp_path))
    try:
        detail = client.get("/api/pan-genome/orthogroups/og0001")
        assert detail.status_code == 200, detail.text
        assert detail.json() == {
            "orthogroup": "OG0001",
            "geneCount": 4,
            "genomeCount": 3,
            "category": "core",
            "genomes": [
                {"name": "GenomeA", "geneCount": 2},
                {"name": "GenomeB", "geneCount": 1},
                {"name": "GenomeC", "geneCount": 1},
            ],
        }

        first_page = client.get(
            "/api/pan-genome/orthogroups/OG0001/members",
            params={"limit": 2},
        )
        assert first_page.status_code == 200, first_page.text
        assert first_page.json()["pagination"] == {
            "limit": 2,
            "offset": 0,
            "total": 4,
            "returned": 2,
            "hasMore": True,
        }
        assert [row["geneId"] for row in first_page.json()["members"]] == [
            "A_gene1",
            "A_gene2",
        ]

        filtered = client.get(
            "/api/pan-genome/orthogroups/OG0001/members",
            params={"genome": "GenomeB", "limit": 10},
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["members"] == [
            {"genome": "GenomeB", "geneId": "Shared_gene"}
        ]
        assert filtered.json()["pagination"]["total"] == 1
        assert filtered.json()["category"] == "core"
    finally:
        client.close()


def test_pan_genome_lists_orthogroups_by_category(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, _write_database(tmp_path))
    try:
        response = client.get(
            "/api/pan-genome/orthogroups",
            params={"category": "soft_core", "limit": 2},
        )
        assert response.status_code == 200, response.text
        assert response.json()["category"] == "soft-core"
        assert response.json()["orthogroups"] == []
        assert response.json()["pagination"]["total"] == 0

        response = client.get(
            "/api/pan-genome/orthogroups",
            params={"category": "dispensable", "limit": 1},
        )
        assert response.status_code == 200, response.text
        assert response.json()["orthogroups"] == [
            {
                "orthogroup": "OG0003",
                "geneCount": 2,
                "genomeCount": 2,
                "category": "dispensable",
            }
        ]
        assert response.json()["pagination"]["total"] == 1
    finally:
        client.close()


def test_pan_genome_validates_requests_and_hides_database_errors(
    monkeypatch, tmp_path
) -> None:
    client = _client(monkeypatch, _write_database(tmp_path))
    try:
        assert client.get("/api/pan-genome/genes/lookup").status_code == 400
        assert (
            client.get(
                "/api/pan-genome/genes/lookup",
                params={"gene_id": "missing"},
            ).status_code
            == 404
        )
        assert client.get("/api/pan-genome/orthogroups/missing").status_code == 404
        assert (
            client.get(
                "/api/pan-genome/orthogroups", params={"category": "unknown"}
            ).status_code
            == 400
        )
        assert (
            client.get(
                "/api/pan-genome/orthogroups/OG0001/members",
                params={"limit": 1001},
            ).status_code
            == 400
        )
        assert (
            client.get(
                "/api/pan-genome/orthogroups/OG0001/members",
                params={"genome": "missing"},
            ).status_code
            == 404
        )
    finally:
        client.close()

    missing_path = tmp_path / "missing.sqlite"
    client = _client(monkeypatch, missing_path)
    try:
        response = client.get("/api/pan-genome/metadata")
        assert response.status_code == 503
        assert response.json() == {"detail": pan_genome_mod.DATABASE_UNAVAILABLE_DETAIL}
        assert str(missing_path) not in response.text
    finally:
        client.close()


def test_pan_genome_connection_is_read_only(monkeypatch, tmp_path) -> None:
    db_path = _write_database(tmp_path)
    monkeypatch.setenv("PAN_GENOME_DB_PATH", str(db_path))
    with pan_genome_mod.connect_db() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                """
                INSERT INTO orthogroups
                VALUES (99, 'OG9999', 99, 1, 1, 'private')
                """
            )


def test_pan_genome_api_does_not_refresh_runtime_activity() -> None:
    class Request:
        def __init__(self, path: str) -> None:
            self.scope = {"path": path}

    for path in (
        "/api/pan-genome/metadata",
        "/api/pan-genome/genomes",
        "/api/pan-genome/genes/lookup",
        "/api/pan-genome/orthogroups",
        "/api/pan-genome/orthogroups/OG0001",
        "/api/pan-genome/orthogroups/OG0001/members",
    ):
        assert (
            interface_app_mod._should_refresh_activity_for_request(Request(path))
            is False
        )

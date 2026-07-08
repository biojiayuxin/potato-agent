from __future__ import annotations

import math
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface import app as interface_app_mod
from interface import bulk_rnaseq_viewer as bulk_rnaseq_viewer_mod
from interface.build_bulk_rnaseq_db import build_database


def _build_bulk_fixture(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "sample_tissue_list.tsv").write_text(
        "\n".join(
            [
                "sample_column\tsample_name\ttissue",
                "S1\tMat1\tleaf",
                "S2\tMat1\tleaf",
                "S3\tMat2\troot",
                "S4\tPG0009\tleaf",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (source / "transcript_tpm_matrix_merged.tsv").write_text(
        "\n".join(
            [
                "transcript_id\tgene_id\tgene_name\tS1\tS2\tS3\tS4",
                "TxA\tGeneA\tNameA\t1\t3\t7\t100",
                "TxB\tGeneB\t\t0\t0\t4\t100",
                "TxC1\tGeneC\t\t1\t2\t3\t100",
                "TxC2\tGeneC\t\t4\t5\t6\t100",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output_db = root / "bulk_rnaseq.sqlite"
    build_database(source, output_db)
    return output_db


def test_bulk_rnaseq_entry_and_static_paths_are_prefixed() -> None:
    lite_index = (REPO_ROOT / "interface/static/lite/index.html").read_text(encoding="utf-8")
    assert '<a class="portal-nav-item" href="/bulk-rnaseq">Bulk RNA-Seq</a>' in lite_index

    bulk_index = (REPO_ROOT / "interface/static/bulk_rnaseq/index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/static/bulk_rnaseq/styles.css' in bulk_index
    assert 'src="/static/bulk_rnaseq/app.js' in bulk_index
    assert 'href="/bulk-rnaseq" aria-current="page"' in bulk_index
    assert 'id="selection-detail"' not in bulk_index

    bulk_css = (REPO_ROOT / "interface/static/bulk_rnaseq/styles.css").read_text(
        encoding="utf-8"
    )
    assert "background: url('../background.png')" in bulk_css


def test_bulk_rnaseq_page_route_serves_static_page() -> None:
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/bulk-rnaseq")
        assert response.status_code == 200
        assert "Bulk RNA-Seq" in response.text
    finally:
        client.close()


def test_bulk_rnaseq_api_does_not_refresh_runtime_activity() -> None:
    class Request:
        class Url:
            path = "/api/bulk-rnaseq/status"

        url = Url()

    assert interface_app_mod._should_refresh_activity_for_request(Request()) is False


def test_bulk_rnaseq_status_without_database_returns_503(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BULK_RNASEQ_DB_PATH", str(tmp_path / "missing.sqlite"))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/api/bulk-rnaseq/status")
        assert response.status_code == 503
        assert "Bulk RNA-Seq database not found" in response.text
    finally:
        client.close()


def test_bulk_rnaseq_expression_api_returns_grouped_values(monkeypatch, tmp_path) -> None:
    db_path = _build_bulk_fixture(tmp_path)
    monkeypatch.setenv("BULK_RNASEQ_DB_PATH", str(db_path))

    client = TestClient(interface_app_mod.app)
    try:
        status = client.get("/api/bulk-rnaseq/status")
        assert status.status_code == 200, status.text
        assert status.json()["counts"] == {"genes": 3, "samples": 3}
        assert status.json()["groups"]["sample_name"] == 2

        response = client.get(
            "/api/bulk-rnaseq/expression",
            params={"genes": "GeneA", "scope": "tissue", "transform": "log2_tpm"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert [column["id"] for column in payload["columns"]] == ["leaf", "root"]
        assert payload["rawValues"] == [[2.0, 7.0]]
        assert payload["nValues"] == [[2, 1]]
        assert payload["values"][0][0] == round(math.log2(3.0), 6)
        assert len(payload["replicates"][0]["samples"]) == 2

        aggregate = client.get(
            "/api/bulk-rnaseq/expression",
            params={"genes": "GeneC", "scope": "sample", "transform": "tpm"},
        )
        assert aggregate.status_code == 200, aggregate.text
        aggregate_payload = aggregate.json()
        assert aggregate_payload["genes"][0]["transcriptCount"] == 2
        assert aggregate_payload["values"] == [[5.0, 7.0, 9.0]]
    finally:
        client.close()


def test_bulk_rnaseq_parse_gene_list_deduplicates_and_limits() -> None:
    assert bulk_rnaseq_viewer_mod.parse_gene_list(" GeneA, GeneB\nGeneA ") == [
        "GeneA",
        "GeneB",
    ]

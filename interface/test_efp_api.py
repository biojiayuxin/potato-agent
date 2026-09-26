from __future__ import annotations

import json
import re
import subprocess
import zlib

import pytest
from fastapi.testclient import TestClient

from interface import efp_viewer
from interface.preview_efp import create_preview_app
from interface.test_bulk_rnaseq_viewer import _build_bulk_fixture


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BULK_RNASEQ_DB_PATH", str(_build_bulk_fixture(tmp_path)))
    with TestClient(create_preview_app()) as client:
        yield client


def pdf_metadata(pdf):
    match = re.search(rb"/PotatoExpression <([0-9a-fA-F]+)>", pdf)
    assert match
    return json.loads(bytes.fromhex(match[1].decode()).decode("utf-16"))


def drawing_stream(pdf):
    for match in re.finditer(rb"<< /Length (\d+)([^>]*) >>\nstream\n", pdf):
        content = pdf[match.end():match.end() + int(match[1])]
        if b"/FlateDecode" in match[2]:
            content = zlib.decompress(content)
        if content.startswith(b"q 0.75"):
            return content
    raise AssertionError("Missing vector drawing stream")


def test_source_is_available_without_database_or_renderer(client, monkeypatch, tmp_path):
    monkeypatch.setenv("BULK_RNASEQ_DB_PATH", str(tmp_path / "missing.sqlite"))
    monkeypatch.setattr(efp_viewer.shutil, "which", lambda _: None)
    response = client.get("/api/efp/source")
    assert response.status_code == 200
    data = response.json()
    assert data["description"]
    assert data["figure"]["page"] == "/efp"
    assert data["figure"]["exportEndpoint"] == "/api/efp/export.pdf"
    assert data["data"]["page"] == "/bulk-rnaseq"
    assert data["data"]["expressionEndpoint"] == "/api/bulk-rnaseq/expression"
    assert data["data"]["scope"] == "tissue"
    assert data["data"]["grouping"] == "Tissue mean"
    assert data["data"]["unit"] == "TPM"
    for page in (data["figure"]["page"], data["data"]["page"]):
        assert client.get(page).status_code == 200


@pytest.mark.parametrize("transform", ["tpm", "log2_tpm", "row_zscore"])
def test_expression_and_vector_pdf_share_page_data(client, transform):
    search = client.get("/api/efp/genes", params={"q": "NameA", "limit": 1})
    assert search.status_code == 200
    assert search.json()["genes"][0]["geneId"] == "GeneA"
    params = {"gene": "GeneA", "transform": transform}
    response = client.get("/api/efp/expression", params=params)
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "tissue"
    assert data["transform"] == transform
    assert data["rawTpm"]["root"] == 7
    assert data["rawTpm"]["leaf"] == 2  # The importer excludes the PG0009 fixture material.
    assert data["rawTpm"]["flower"] is None
    raw = client.get("/api/bulk-rnaseq/expression", params={
        "genes": "GeneA", "scope": "tissue", "transform": transform,
    }).json()
    for row in data["tissueValues"]:
        index = next(i for i, col in enumerate(raw["columns"]) if col["tissue"] == row["tissue"])
        assert row["value"] == raw["values"][0][index]
    response = client.get("/api/efp/export.pdf", params=params)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'attachment; filename="GeneA_efp_{transform}.pdf"'
    pdf = response.content
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"/Subtype /Image" not in pdf and b"/SMask" not in pdf
    assert pdf.count(b"/FontFile2 ") == 2
    metadata = pdf_metadata(pdf)
    assert metadata == {key: data[key] for key in metadata}
    drawing = drawing_stream(pdf)
    assert drawing.count(b" c\n") > 3000
    assert b"(GeneA) Tj" in drawing
    xref = int(re.search(rb"startxref\n(\d+)", pdf)[1])
    assert pdf[xref:xref + 4] == b"xref"


def test_mapping_missing_zero_and_scale_preserve_page_rules(client, monkeypatch):
    tissues = ["root", "flower", "stolon", "stolon tip", "tuber", "carpel", "stamen"]
    values = [0, 2, 4, None, 8, 32, 100]
    monkeypatch.setattr(efp_viewer, "load_expression", lambda *_: {
        "dataset": "fixture", "scope": "tissue", "transform": "tpm", "genes": [{"geneId": "GeneA"}],
        "columns": [{"tissue": tissue} for tissue in tissues], "values": [values], "rawValues": [values],
    })
    params = {"gene": "GeneA", "transform": "tpm"}
    data = client.get("/api/efp/expression", params=params).json()
    assert data["rawTpm"]["mature_tuber"] == 8
    assert data["rawTpm"]["stolon_tip_S1"] is None
    assert data["scale"]["max"] == 100  # Includes stamen, despite its omission from the page.
    assert data["unmappedTissues"] == ["carpel"]
    regions = {row["id"]: row for row in data["regions"]}
    assert regions["root"]["missing"] is False
    assert regions["root"]["colour"] != "#C9CED0"
    assert regions["stolon_tip_S1"]["missing"] is True
    assert regions["stolon_tip_S1"]["colour"] == "#C9CED0"
    assert regions["flower"]["value"] == 2
    assert "stamen" not in json.dumps(data)
    pdf = client.get("/api/efp/export.pdf", params=params).content
    assert pdf_metadata(pdf)["tissueValues"] == data["tissueValues"]


@pytest.mark.parametrize("path,params,status", [
    ("expression", {}, 422),
    ("expression", {"gene": ""}, 422),
    ("expression", {"gene": "a" * 201}, 422),
    ("expression", {"gene": "GeneA,GeneB"}, 400),
    ("export.pdf", {"gene": "GeneA;GeneB"}, 400),
    ("export.pdf", {"gene": "GeneA\n"}, 400),
    ("export.pdf", {"gene": "GeneA", "transform": "invalid"}, 422),
    ("expression", {"gene": "Missing"}, 404),
    ("export.pdf", {"gene": "Missing"}, 404),
    ("genes", {"limit": 101}, 422),
])
def test_api_errors(client, path, params, status):
    response = client.get("/api/efp/" + path, params=params)
    assert response.status_code == status
    assert "detail" in response.json()
    assert "content-disposition" not in response.headers


def test_missing_database_and_renderer(client, monkeypatch, tmp_path):
    with monkeypatch.context() as patch:
        patch.setenv("BULK_RNASEQ_DB_PATH", str(tmp_path / "missing.sqlite"))
        assert client.get("/api/efp/expression?gene=GeneA").status_code == 503
    monkeypatch.delenv("POTATO_EFP_NODE", raising=False)
    monkeypatch.setattr(efp_viewer.shutil, "which", lambda _: None)
    response = client.get("/api/efp/export.pdf?gene=GeneA")
    assert response.status_code == 503
    assert "Node.js" in response.json()["detail"]


def test_busy_and_timed_out_renderers_release_capacity(client, monkeypatch):
    efp_viewer._RENDER_SLOTS.acquire()
    efp_viewer._RENDER_SLOTS.acquire()
    try:
        response = client.get("/api/efp/export.pdf?gene=GeneA")
        assert response.status_code == 503
        assert response.headers["retry-after"] == "2"
    finally:
        efp_viewer._RENDER_SLOTS.release()
        efp_viewer._RENDER_SLOTS.release()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("node", 20)

    with monkeypatch.context() as patch:
        patch.setattr(efp_viewer.subprocess, "run", timeout)
        assert client.get("/api/efp/export.pdf?gene=GeneA").status_code == 504
    assert client.get("/api/efp/expression?gene=GeneA").status_code == 200


def test_efp_public_api_does_not_refresh_agent_activity():
    from interface.app import _should_refresh_activity_for_request, app

    for endpoint in ("source", "genes", "expression", "export.pdf"):
        class Request:
            scope = {"path": "/api/efp/" + endpoint}

        assert _should_refresh_activity_for_request(Request()) is False
    schema = app.openapi()
    assert "application/pdf" in schema["paths"]["/api/efp/export.pdf"]["get"]["responses"]["200"]["content"]

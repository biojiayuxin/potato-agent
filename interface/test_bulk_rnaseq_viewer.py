from __future__ import annotations

import math
import shutil
import subprocess
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
    assert 'data-portal-module="lite"' in lite_index

    bulk_index = (REPO_ROOT / "interface/static/bulk_rnaseq/index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/static/bulk_rnaseq/styles.css' in bulk_index
    assert 'src="/static/bulk_rnaseq/app.js' in bulk_index
    assert 'src="/static/bulk_rnaseq/pdf_export.js' in bulk_index
    assert 'data-portal-module="bulk_rnaseq"' in bulk_index
    assert "Gene Expression Across Materials and Tissues" in bulk_index
    assert "Bulk RNA-Seq" not in bulk_index
    assert 'id="download-pdf"' in bulk_index
    assert 'id="download-png"' not in bulk_index
    assert 'id="selection-detail"' not in bulk_index

    bulk_css = (REPO_ROOT / "interface/static/bulk_rnaseq/styles.css").read_text(
        encoding="utf-8"
    )
    assert "background: url('../background.png')" in bulk_css


def test_bulk_rnaseq_pdf_export_is_vector_only() -> None:
    node = shutil.which("node")
    if node is None:
        return
    exporter = REPO_ROOT / "interface/static/bulk_rnaseq/pdf_export.js"
    script = r"""
const assert = require('node:assert/strict');
const {
  createVectorContext,
  drawViewerChrome,
  viewerExportHeight,
} = require(process.argv[1]);
const stageHeight = 180;
const pdf = createVectorContext(640, viewerExportHeight(stageHeight));
const ctx = pdf.context;
ctx.fillStyle = '#ff0000';
ctx.fillRect(20, 30, 80, 40);
ctx.strokeStyle = 'rgba(100, 116, 139, 0.28)';
ctx.strokeRect(20, 30, 80, 40);
ctx.font = '750 11px Inter, sans-serif';
ctx.fillText('GeneA', 20, 90);
drawViewerChrome(ctx, 640, stageHeight, {
  title: 'DM8.2_chr01G00010',
  summary: 'Material by tissue | Row z-score | 3 columns',
  legendMin: '-1.5',
  legendMax: '1.5',
  legendColors: ['#0000ff', '#f8fafc', '#ff0000'],
});
const bytes = pdf.toUint8Array();
const source = new TextDecoder('ascii').decode(bytes);
assert.match(source, /^%PDF-1\.4/);
assert.match(source, / re f/);
assert.match(source, /\(GeneA\) Tj/);
assert.match(source, /\(DM8\.2_chr01G00010\) Tj/);
assert.match(source, /\(Material by tissue \| Row z-score \| 3 columns\) Tj/);
assert.match(source, /\(-1\.5\) Tj/);
assert.match(source, /\(1\.5\) Tj/);
assert.match(source, /\/ExtGState/);
assert.match(source, /\/ca 0\.28 \/CA 0\.28/);
assert.doesNotMatch(source, /\/Subtype\s*\/Image/);
const xrefOffset = Number(source.match(/startxref\n(\d+)\n%%EOF/)[1]);
assert.equal(source.slice(xrefOffset, xrefOffset + 4), 'xref');
"""
    subprocess.run(
        [node, "-e", script, str(exporter)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_bulk_rnaseq_pdf_records_the_canvas_render() -> None:
    node = shutil.which("node")
    if node is None:
        return
    exporter = REPO_ROOT / "interface/static/bulk_rnaseq/pdf_export.js"
    script = r"""
const assert = require('node:assert/strict');
const { createMirroredContext, createVectorContext } = require(process.argv[1]);
const canvasCalls = [];
const canvas = {
  fillStyle: '#000000',
  strokeStyle: '#000000',
  lineWidth: 1,
  font: '10px sans-serif',
  textAlign: 'start',
  textBaseline: 'alphabetic',
  fillRect(...args) { canvasCalls.push(['fillRect', ...args]); },
  fillText(...args) { canvasCalls.push(['fillText', ...args]); },
  measureText() { return { width: 42 }; },
};
const pdf = createVectorContext(320, 180);
const ctx = createMirroredContext(canvas, pdf.context);
ctx.fillStyle = '#ff0000';
ctx.font = '700 11px Inter, sans-serif';
ctx.fillRect(20, 30, 80, 40);
ctx.fillText('GeneA', 20, 90);
assert.deepEqual(canvasCalls, [
  ['fillRect', 20, 30, 80, 40],
  ['fillText', 'GeneA', 20, 90],
]);
const source = new TextDecoder('ascii').decode(pdf.toUint8Array());
assert.match(source, / re f/);
assert.match(source, /\(GeneA\) Tj/);
assert.match(source, /\/F2 11 Tf/);
assert.match(source, / Tz/);
assert.doesNotMatch(source, /\/Subtype\s*\/Image/);
"""
    subprocess.run(
        [node, "-e", script, str(exporter)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_bulk_rnaseq_page_route_serves_static_page() -> None:
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/bulk-rnaseq")
        assert response.status_code == 200
        assert "Gene Expression Across Materials and Tissues" in response.text
    finally:
        client.close()


def test_bulk_rnaseq_api_does_not_refresh_runtime_activity() -> None:
    class Request:
        scope = {"path": "/api/bulk-rnaseq/status"}

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

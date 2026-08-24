from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_spatial_page_loads_vector_pdf_export_before_app() -> None:
    index = (REPO_ROOT / "interface/static/spatial/index.html").read_text(encoding="utf-8")
    exporter_script = 'src="/static/spatial/pdf_export.js?v='
    app_script = 'src="/static/spatial/app.js?v='

    assert 'id="exportPdf"' in index
    assert 'title="Download vector PDF"' in index
    assert exporter_script in index
    assert index.index(exporter_script) < index.index(app_script)

    app = (REPO_ROOT / "interface/static/spatial/app.js").read_text(encoding="utf-8")
    assert "contourCellsForExport" in app
    assert "window.SpatialExpressionPdf.render" in app
    assert 'state.displayMode === "gene" && state.dotplot.payload' in app
    assert 'loadTile(spatial, key, { retry: true, strict: true })' in app
    assert "const preloadCount = 3" in app

    css = (REPO_ROOT / "interface/static/spatial/style.css").read_text(encoding="utf-8")
    assert ".toolbar #resetView,\n.toolbar #exportPdf {\n  width: 62px;\n}" in css
    assert ".toolbar #exportPdf {\n  position: relative;\n}" in css


def test_spatial_pdf_export_is_compressed_multipage_and_vector_only() -> None:
    node = shutil.which("node")
    if node is None:
        return
    exporter = REPO_ROOT / "interface/static/spatial/pdf_export.js"
    script = r"""
const assert = require('node:assert/strict');
const zlib = require('node:zlib');
const pdf = require(process.argv[1]);

function contentStreams(bytes) {
  const buffer = Buffer.from(bytes);
  const source = buffer.toString('latin1');
  const streams = [];
  let cursor = 0;
  while (true) {
    const lengthStart = source.indexOf('/Length ', cursor);
    if (lengthStart < 0) break;
    const lengthMatch = source.slice(lengthStart).match(/^\/Length (\d+)(?: \/Filter \/FlateDecode)? >>\nstream\n/);
    if (!lengthMatch) {
      cursor = lengthStart + 8;
      continue;
    }
    const streamStart = lengthStart + lengthMatch[0].length;
    const length = Number(lengthMatch[1]);
    const compressed = source.slice(lengthStart, streamStart).includes('/FlateDecode');
    const data = buffer.subarray(streamStart, streamStart + length);
    streams.push((compressed ? zlib.inflateSync(data) : data).toString('latin1'));
    cursor = streamStart + length;
  }
  return streams;
}

(async () => {
  const panel = {
    id: 'rep1', label: 'REP1', x: 10, y: 10, width: 80, height: 80,
    bbox: [0, 0, 9, 9], scaleX: 8, scaleY: 8, assignedCellCount: 1,
  };
  const spatial = {
    title: 'GeneA spatial expression',
    subtitle: 'Toy Dataset | S1',
    layoutWidth: 100,
    layoutHeight: 100,
    panels: [panel],
    cellToPanel: new Map([[1, panel]]),
    cells: [{
      id: 1,
      bbox: [1, 1, 3, 3],
      contours: [[[0, 0], [2, 0], [2, 2], [0, 2]]],
    }],
    colorForCell: () => [203, 24, 29],
    legend: {type: 'gradient', label: 'Expression', min: 0, max: 3.5, colors: pdf.REDS},
  };
  const result = await pdf.render({
    title: 'GeneA spatial expression',
    spatial,
    dotplot: {
      gene: 'GeneA',
      clusters: [{
        id: '0', label: '0', name: 'Toy cluster cells', avgExpr: 3.5,
        avgExprScaled: 0, pctExpr: 100,
      }],
    },
  });

  const source = Buffer.from(result.bytes).toString('latin1');
  assert.equal(result.pageCount, 2);
  assert.equal(result.drawnCells, 1);
  assert.match(source, /^%PDF-1\.4/);
  assert.match(source, /\/Count 2/);
  assert.match(source, /\/Filter \/FlateDecode/);
  assert.doesNotMatch(source, /\/Subtype\s*\/Image/);
  const xrefOffset = Number(source.match(/startxref\n(\d+)\n%%EOF/)[1]);
  assert.equal(source.slice(xrefOffset, xrefOffset + 4), 'xref');

  const streams = contentStreams(result.bytes);
  assert.equal(streams.length, 2);
  assert.match(streams[0], / h B Q/);
  assert.match(streams[0], /\(GeneA spatial expression\) Tj/);
  assert.match(streams[0], /\(Expression\) Tj/);
  assert.match(streams[1], / c .* B Q/);
  assert.match(streams[1], /\(Seurat Clusters\) Tj/);
  assert.match(streams[1], /\(0 - Toy cluster cells\) Tj/);

  const categorical = await pdf.render({
    title: 'Cluster map',
    spatial: {
      ...spatial,
      title: 'Seurat cluster spatial map',
      legend: {
        type: 'categories',
        items: [
          {label: '0 - Toy cluster cells', color: [255, 0, 0]},
          {label: 'Other cells', color: [214, 219, 226]},
        ],
      },
    },
  });
  assert.equal(categorical.pageCount, 1);
  const categoryStreams = contentStreams(categorical.bytes);
  assert.equal(categoryStreams.length, 1);
  assert.match(categoryStreams[0], /\(Color key\) Tj/);
  assert.match(categoryStreams[0], /\(Other cells\) Tj/);
})();
"""
    subprocess.run(
        [node, "-e", script, str(exporter)],
        check=True,
        capture_output=True,
        text=True,
    )

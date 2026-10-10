from __future__ import annotations

import shutil
import subprocess
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interface.preview_efp import create_preview_app


STATIC = Path(__file__).parent / "static" / "efp"


def test_efp_page_and_module_assets() -> None:
    from interface.app import app

    client = TestClient(app)
    try:
        response = client.get("/efp")
        assert response.status_code == 200
        assert 'data-portal-module="efp"' in response.text
        assert "Tissue Expression Map" in response.text
        for module in ("app.mjs", "expression.mjs", "potato-efp.mjs", "export.mjs", "figure.mjs", "pdf.mjs", "viewport.mjs"):
            response = client.get(f"/static/efp/{module}")
            assert response.status_code == 200
            assert "javascript" in response.headers["content-type"]
        assert client.get("/static/efp/potato-template.svg").status_code == 200
    finally:
        client.close()


def test_efp_pdf_assets_match_the_svg_and_embedded_fonts() -> None:
    svg = (STATIC / 'potato-template.svg').read_bytes()
    geometry = json.loads((STATIC / 'pdf-geometry.json').read_text())
    assert geometry['templateSha256'] == hashlib.sha256(svg).hexdigest()
    root = ET.fromstring(svg)
    tissues = [node.attrib['data-tissue'] for node in root.iter() if 'data-tissue' in node.attrib]
    assert len(tissues) == len(set(tissues)) == 17
    assert 'carpel' in tissues and 'pistil' not in tissues
    assert set(geometry['regions']) == set(tissues)
    assert geometry['viewBox'] == [float(n) for n in root.attrib['viewBox'].split()]
    ids = [node.attrib['id'] for node in root.iter() if 'id' in node.attrib]
    assert len(ids) == len(set(ids))
    references = set()
    for node in root.iter():
        for name, value in node.attrib.items():
            if name.rsplit('}', 1)[-1] == 'href':
                assert value.startswith('#')
                references.add(value[1:])
            elif name in {'aria-labelledby', 'aria-describedby'}:
                references.update(value.split())
            references.update(re.findall(r'url\(\s*[\'"]?#([^\s)\'"]+)', value))
    assert references <= set(ids)
    for font in geometry['fonts']:
        assert hashlib.sha256((STATIC / font['file']).read_bytes()).hexdigest() == font['sha256']
        assert len(font['widths']) == 95
    assert (STATIC / 'fonts/LICENSE.txt').is_file()


def test_efp_adapter_and_scale_contract() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend numerical regression checks")
    script = r'''
import assert from 'node:assert/strict';
import {adaptExpression, formatValue} from './expression.mjs';
import {prepareExpression, MISSING_COLOUR, DIVERGING_PALETTE} from './potato-efp.mjs';
const data = {
  scope: 'tissue', transform: 'row_zscore', dataset: 'Fixture', genes: [{geneId: 'GeneA'}],
  columns: ['root', 'young leaf', 'mature leaf', 'flower', 'tuber'].map(tissue => ({tissue})),
  values: [[-1, 0, 1, 2, 3]], rawValues: [[0, 2, 10, 20, 40]],
};
const adapted = adaptExpression(data);
assert.equal(adapted.payload.expression.root, -1);
assert.equal(adapted.rawTpm.root, 0);
assert.equal(adapted.payload.expression.young_leaf, 0);
assert.equal(adapted.payload.expression.flower, 2);
assert.equal(adapted.rawTpm.flower, 20);
assert.equal(adapted.payload.expression.flower_bud, null);
assert.equal(adapted.payload.expression.mature_tuber, 3);
assert.equal(adapted.payload.expression.young_tuber_S3, null);
assert.equal(adapted.payload.scale.min, -3);
assert.equal(adapted.payload.scale.max, 3);
assert.deepEqual(adapted.unmappedTissues, []);
assert.deepEqual(adapted.tissueValues.find(row => row.tissue === 'flower').diagramIds, ['flower']);
assert.equal(adapted.tissueValues.length, 5);
assert.deepEqual(adapted.tissueValues.at(-1), {tissue: 'tuber', value: 3, rawTpm: 40, diagramIds: ['mature_tuber']});
assert.equal(adapted.tissueValues.find(row => row.tissue === 'root').rawTpm, 0);
assert.deepEqual(adapted.tissueValues.find(row => row.tissue === 'root').diagramIds, ['root']);
assert.equal(formatValue(1.584963), '1.584963');
assert.equal(formatValue(0), '0');
assert.equal(formatValue(null), 'NA');
const result = prepareExpression(adapted.payload);
assert.equal(result.validCount, 5);
assert.equal(result.rows.find(r => r.id === 'young_leaf').colour, DIVERGING_PALETTE[1]);
assert.equal(result.rows.find(r => r.id === 'flower_bud').colour, MISSING_COLOUR);
assert.notEqual(result.rows.find(r => r.id === 'root').colour, MISSING_COLOUR);
for (const transform of ['tpm', 'log2_tpm', 'row_zscore']) {
  const constant = prepareExpression(adaptExpression({...data, transform, values: [[0, 0, 0, 0, 0]]}).payload);
  assert.equal(constant.rows.find(r => r.id === 'root').missing, false);
  assert.ok(constant.scale.max > constant.scale.min);
}
const logged = adaptExpression({...data, transform: 'log2_tpm', values: [[0, 1, 2, 3, 4]]});
assert.equal(logged.payload.expression.mature_leaf, 2); // Not logarithmically transformed again.
assert.equal(logged.payload.scale.type, 'linear');
const missing = adaptExpression({...data, values: [[null, 0, 1, 2, 3]], rawValues: [[null, 2, 10, 20, 40]]});
assert.equal(missing.payload.expression.root, null);
const floral = adaptExpression({...data,
  columns: ['flower', 'perianth', 'anther', 'flower bud', 'carpel'].map(tissue => ({tissue})),
});
assert.deepEqual([floral.payload.expression.flower, floral.payload.expression.perianth,
  floral.payload.expression.anther, floral.payload.expression.flower_bud,
  floral.payload.expression.carpel], [-1, 0, 1, 2, 3]);
assert.deepEqual(floral.unmappedTissues, []);
assert.equal(floral.payload.scale.max, 3);
assert.throws(() => adaptExpression({...data, values: [[NaN, 0, 1, 2, 3]]}), /invalid value/);
assert.throws(() => adaptExpression({...data, rawValues: [['0', 2, 10, 20, 40]]}), /invalid value/);
assert.throws(() => adaptExpression({...data, values: [[0]]}), /Expected/);
assert.throws(() => adaptExpression({...data, scope: 'sample'}), /Expected/);
assert.throws(() => adaptExpression({...data, columns: [{tissue:'root'}, {tissue:'root'}, ...data.columns.slice(2)]}), /Ambiguous/);
assert.throws(() => prepareExpression({...logged.payload, expression: {root: -1}}), /invalid/);
console.log('eFP adapter and scale checks passed');
'''
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=STATIC, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_efp_floral_organs_use_independent_exact_api_tissues() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend numerical regression checks")
    script = r'''
import assert from 'node:assert/strict';
import {adaptExpression} from './expression.mjs';
import {prepareExpression, MISSING_COLOUR} from './potato-efp.mjs';
const tissues = ['flower', 'perianth', 'anther', 'carpel', 'pistil'];
const raw = [40, 0, 10, 20, 100];
const data = {
  scope: 'tissue', genes: [{geneId: 'FloralGene'}],
  columns: tissues.map(tissue => ({tissue})), rawValues: [raw],
};
for (const [transform, values, min, max] of [
  ['tpm', raw, 0, 100],
  ['log2_tpm', [5.357552, 0, 3.459432, 4.392317, 6.658211], 0, 6.658211],
  ['row_zscore', [-1, 0, 1, -2, 4], -4, 4],
]) {
  const adapted = adaptExpression({...data, transform, values: [values]});
  const result = prepareExpression(adapted.payload);
  for (const [index, tissue] of tissues.slice(0, 4).entries()) {
    assert.equal(adapted.payload.expression[tissue], values[index]);
    assert.equal(adapted.rawTpm[tissue], raw[index]);
    assert.deepEqual(adapted.tissueValues.find(row => row.tissue === tissue).diagramIds, [tissue]);
    const region = result.rows.find(row => row.id === tissue);
    assert.equal(region.missing, false);
    assert.notEqual(region.colour, MISSING_COLOUR);
  }
  assert.equal(result.rows.find(row => row.id === 'carpel').label, 'Carpel');
  assert.equal(result.scale.min, min);
  assert.equal(result.scale.max, max); // Includes the unmapped pistil column.
  assert.deepEqual(adapted.unmappedTissues, ['pistil']);
  assert.deepEqual(adapted.tissueValues.find(row => row.tissue === 'pistil').diagramIds, []);
  assert.ok(!Object.hasOwn(adapted.payload.expression, 'pistil'));
}
for (const tissue of tissues.slice(0, 4)) {
  const index = tissues.indexOf(tissue);
  for (const value of [0, null]) {
    const values = raw.map((original, i) => i === index ? value : original);
    const adapted = adaptExpression({...data, transform: 'tpm', values: [values], rawValues: [values]});
    const result = prepareExpression(adapted.payload);
    for (const [i, id] of tissues.slice(0, 4).entries()) {
      const region = result.rows.find(row => row.id === id);
      assert.equal(region.value, values[i]);
      assert.equal(region.missing, values[i] === null);
      assert.equal(region.colour === MISSING_COLOUR, values[i] === null);
    }
  }
}
const absent = adaptExpression({
  ...data, transform: 'tpm', columns: [{tissue: 'flower'}, {tissue: 'pistil'}],
  values: [[40, 20]], rawValues: [[40, 20]],
});
assert.equal(absent.payload.expression.flower, 40);
for (const id of ['perianth', 'anther', 'carpel']) assert.equal(absent.payload.expression[id], null);
assert.deepEqual(absent.unmappedTissues, ['pistil']); // No alias and no generic flower fallback.
console.log('eFP independent floral tissue checks passed');
'''
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=STATIC, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_efp_tuber_mapping_stamen_omission_and_independent_stolon_tip() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend numerical regression checks")
    script = r'''
import assert from 'node:assert/strict';
import {adaptExpression} from './expression.mjs';
import {prepareExpression} from './potato-efp.mjs';
const small = 'immature small tuber (transection diameter < 1 cm)';
const big = 'immature big tuber (1 cm <transection diameter < 5 cm)';
const data = {
  scope: 'tissue', transform: 'log2_tpm', genes: [{geneId: 'GeneA'}],
  columns: ['tuber', 'stamen', small, 'stolon', big].map(tissue => ({tissue})),
  values: [[3, 9, 0, 2, 1]], rawValues: [[7, 511, 0, 3, 1]],
};
const adapted = adaptExpression(data);
assert.deepEqual(adapted.tissueValues.map(row => row.tissue), ['stolon', 'tuber', small, big]);
assert.deepEqual(adapted.unmappedTissues, []);
assert.ok(!JSON.stringify(adapted).includes('stamen'));
assert.equal(adapted.payload.expression.mature_tuber, 3);
assert.equal(adapted.rawTpm.mature_tuber, 7);
assert.equal(adapted.payload.expression.young_tuber_S3, 0);
assert.equal(adapted.rawTpm.young_tuber_S3, 0);
assert.equal(adapted.payload.expression.young_tuber_S4, 1);
assert.equal(adapted.rawTpm.young_tuber_S4, 1);
assert.equal(adapted.payload.expression.stolon, 2);
assert.equal(adapted.payload.expression.stolon_tip_S1, 2);
assert.equal(adapted.rawTpm.stolon_tip_S1, 3);
assert.deepEqual(adapted.tissueValues[0].diagramIds, ['stolon', 'stolon_tip_S1']);
assert.equal(prepareExpression(adapted.payload).rows.find(row => row.id === 'stolon_tip_S1').label, 'Stolon');
assert.equal(adapted.payload.scale.max, 9); // Keep the API's all-atlas reference range.
const zscore = adaptExpression({...data, transform: 'row_zscore', values: [[-1, 4, -2, 0, 1]]});
assert.equal(zscore.payload.expression.young_tuber_S3, -2); // Reuse API values without renormalizing.
assert.equal(zscore.payload.expression.stolon_tip_S1, 0);
assert.equal(zscore.payload.scale.min, -4);
assert.equal(zscore.payload.scale.max, 4);
for (const alias of ['stolon tip', 'stolon_tip', 'stolon_tip_S1', 'stolon tip (S1)']) {
  for (const value of [5, 0, null]) {
    const explicit = adaptExpression({...data,
      columns: [...data.columns, {tissue: alias}],
      values: [[...data.values[0], value]], rawValues: [[...data.rawValues[0], value]],
    });
    assert.equal(explicit.payload.expression.stolon, 2);
    assert.equal(explicit.payload.expression.stolon_tip_S1, value);
    assert.equal(explicit.rawTpm.stolon_tip_S1, value);
    assert.deepEqual(explicit.tissueValues.find(row => row.tissue === 'stolon').diagramIds, ['stolon']);
    assert.deepEqual(explicit.tissueValues.find(row => row.tissue === alias).diagramIds, ['stolon_tip_S1']);
    const tip = prepareExpression(explicit.payload).rows.find(row => row.id === 'stolon_tip_S1');
    assert.equal(tip.label, 'Stolon tip (S1)');
    assert.equal(tip.missing, value === null);
  }
}
console.log('eFP tissue correspondence checks passed');
'''
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=STATIC, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_preview_only_proxies_expression_endpoints() -> None:
    with TestClient(create_preview_app("http://127.0.0.1:1")) as client:
        assert client.get("/efp").status_code == 200
        assert client.get("/api/bulk-rnaseq/secrets").status_code == 404
        assert client.post("/api/bulk-rnaseq/expression").status_code == 405
        assert client.get("/api/bulk-rnaseq/status").status_code == 503
        assert client.get("/api/efp/secrets").status_code == 404
        assert client.post("/api/efp/export.pdf").status_code == 405
        assert client.get("/api/efp/expression?gene=GeneA").status_code == 503


@pytest.mark.parametrize("origin", ["file:///tmp/example", "https://user:password@example.org", "https://example.org/path"])
def test_preview_rejects_non_origin_configuration(origin) -> None:
    with pytest.raises(ValueError):
        create_preview_app(origin)

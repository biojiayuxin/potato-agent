/** Shared, DOM-free layout for browser and API vector exports. */
import {PALETTES, MISSING_COLOUR, mixColour} from './potato-efp.mjs?v=20260923-flower';
import {formatNumber, formatValue} from './expression.mjs?v=20260923-flower';

function wrapLabel(text, limit = 22) {
  const lines = [];
  let line = '';
  for (const word of text.split(/\s+/).flatMap(word => word.match(/.{1,22}/gu) || [])) {
    if (line && line.length + word.length + 1 > limit) { lines.push(line); line = ''; }
    line += (line ? ' ' : '') + word;
  }
  if (line) lines.push(line);
  return lines;
}

export function expressionMetadata(result, data) {
  return {
    geneId: result.geneId, dataset: data.dataset, scope: 'tissue', transform: data.transform,
    scale: result.scale, rawTpm: data.rawTpm, tissueValues: data.tissueValues,
    values: Object.fromEntries(result.rows.map(row => [row.id, row.value])),
    unmappedTissues: data.unmappedTissues,
  };
}

export function buildFigure(result, data) {
  const width = 1120, diagramHeight = 1089.4;
  const rows = data.tissueValues.map(row => {
    const lines = wrapLabel(row.tissue + (row.diagramIds.length ? '' : ' *'));
    return {...row, lines, height: Math.max(40, lines.length * 18 + 20)};
  });
  const tableBottom = 144 + rows.reduce((sum, row) => sum + row.height, 0);
  const height = Math.max(diagramHeight + 204, tableBottom + 80);
  const elements = [];
  const add = (type, attrs, text) => elements.push({type, attrs, text});
  add('rect', {width, height, fill: '#ffffff'});
  add('text', {x: 32, y: 34, 'font-size': 15}, 'Tissue Expression Map (eFP)');
  const titleAttrs = {x: 32, y: 67, 'font-size': 24, 'font-weight': 700};
  if (result.geneId.length > 44) Object.assign(titleAttrs, {textLength: width - 64, lengthAdjust: 'spacingAndGlyphs'});
  add('text', titleAttrs, result.geneId);
  add('text', {x: 32, y: 95, 'font-size': 14}, `Tissue mean | ${result.unit}`);
  add('plant', {x: 24, y: 150, width: 667, height: diagramHeight});
  add('text', {x: 32, y: 137, 'font-size': 13, 'font-weight': 600}, result.unit);
  add('gradient', {x: 32, y: 150, width: 210, height: 12, palette: PALETTES[result.scale.palette]});
  result.ticks.forEach((value, index) => add('text', {
    x: 32 + 210 * index / (result.ticks.length - 1), y: 180, 'font-size': 12,
    'text-anchor': index === 0 ? 'start' : index === result.ticks.length - 1 ? 'end' : 'middle',
  }, formatNumber(value)));
  add('rect', {x: 32, y: 194, width: 10, height: 10, fill: MISSING_COLOUR});
  add('text', {x: 48, y: 203, 'font-size': 12}, 'NA: no matching tissue or no data');
  add('line', {x1: 716, x2: 716, y1: 118, y2: height - 28, stroke: '#d6dfef'});
  const rawX = data.transform === 'tpm' ? 1088 : 982;
  add('rect', {x: 736, y: 118, width: 364, height: 26, fill: '#f7f9fc'});
  add('text', {x: 744, y: 135, 'font-size': 13}, 'Tissue');
  add('text', {x: rawX, y: 135, 'font-size': 13, 'text-anchor': 'end'}, 'TPM');
  if (data.transform !== 'tpm') add('text', {x: 1088, y: 135, 'font-size': 13, 'text-anchor': 'end'}, result.unit);
  let rowY = 144;
  for (const row of rows) {
    add('rect', {x: 744, y: rowY + 12, width: 10, height: 10, fill: mixColour(row.value, result.scale)});
    row.lines.forEach((line, index) => add('text', {x: 760, y: rowY + 22 + index * 18, 'font-size': 13}, line));
    add('text', {x: rawX, y: rowY + 22, 'font-size': 13, 'text-anchor': 'end'}, formatValue(row.rawTpm));
    if (data.transform !== 'tpm') add('text', {x: 1088, y: rowY + 22, 'font-size': 13, 'text-anchor': 'end'}, formatValue(row.value));
    rowY += row.height;
    add('line', {x1: 744, x2: 1096, y1: rowY, y2: rowY, stroke: '#e6eaf1'});
  }
  if (data.unmappedTissues.length) add('text', {x: 744, y: tableBottom + 25, 'font-size': 12, fill: '#64748b'}, '* Tissue not mapped to the diagram.');
  add('text', {x: 32, y: height - 26, 'font-size': 12, fill: '#64748b'}, 'Scale covers all atlas tissues.');
  return {width, height, elements, regions: result.rows, metadata: expressionMetadata(result, data)};
}

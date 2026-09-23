import {PALETTES, MISSING_COLOUR, mixColour} from './potato-efp.mjs?v=20260923-flower';
import {formatNumber, formatValue} from './expression.mjs?v=20260923-flower';
import {vectorPdf} from './pdf.mjs?v=20260923-pdf1';

const NS = 'http://www.w3.org/2000/svg';

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

/** Export the full diagram, top-left legend and all tissue values, regardless of zoom. */
export function exportSvg(plant, result, data) {
  const doc = document.implementation.createDocument(NS, 'svg', null);
  const root = doc.documentElement;
  const width = 1120;
  const diagramHeight = 1089.4;
  const rows = data.tissueValues.map(row => {
    const lines = wrapLabel(row.tissue + (row.diagramIds.length ? '' : ' *'));
    return {...row, lines, height: Math.max(40, lines.length * 18 + 20)};
  });
  const tableBottom = 144 + rows.reduce((sum, row) => sum + row.height, 0);
  const height = Math.max(diagramHeight + 204, tableBottom + 80);
  const node = (tag, attrs = {}, content) => {
    const el = doc.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    if (content !== undefined) el.textContent = content;
    return el;
  };
  for (const [key, value] of Object.entries({width, height, viewBox: `0 0 ${width} ${height}`})) {
    root.setAttribute(key, value);
  }
  root.setAttribute('font-family', 'system-ui, "Noto Sans", "Liberation Sans", Arial, sans-serif');
  root.setAttribute('fill', '#14213d');
  root.setAttribute('role', 'img');
  root.setAttribute('aria-label', `${result.geneId} — Tissue Expression Map (eFP)`);
  root.append(node('desc', {}, `Tissue mean; ${result.unit}. Full diagram and displayed transcriptome tissue values. Gray: no matching tissue or no data.`));
  root.append(node('metadata', {}, JSON.stringify({
    geneId: result.geneId, dataset: data.dataset, scope: 'tissue', transform: data.transform,
    scale: result.scale, rawTpm: data.rawTpm, tissueValues: data.tissueValues,
    values: Object.fromEntries(result.rows.map(row => [row.id, row.value])),
    unmappedTissues: data.unmappedTissues,
  })));
  root.append(node('rect', {width, height, fill: '#ffffff'}));
  root.append(node('text', {x: 32, y: 34, 'font-size': 15}, 'Tissue Expression Map (eFP)'));
  const titleAttrs = {x: 32, y: 67, 'font-size': 24, 'font-weight': 700};
  if (result.geneId.length > 44) Object.assign(titleAttrs, {textLength: width - 64, lengthAdjust: 'spacingAndGlyphs'});
  root.append(node('text', titleAttrs, result.geneId));
  root.append(node('text', {x: 32, y: 95, 'font-size': 14}, `Tissue mean | ${result.unit}`));
  const drawing = doc.importNode(plant, true);
  drawing.removeAttribute('style');
  drawing.setAttribute('viewBox', plant.dataset.originalViewBox || plant.getAttribute('viewBox'));
  drawing.removeAttribute('data-zoom');
  drawing.setAttribute('x', '24');
  drawing.setAttribute('y', '150');
  drawing.setAttribute('width', '667');
  drawing.setAttribute('height', diagramHeight);
  drawing.querySelectorAll('[tabindex]').forEach(el => el.removeAttribute('tabindex'));
  root.append(drawing);
  const gradientId = `${plant.querySelector('[id]').id}-export-gradient`;
  const defs = node('defs');
  const gradient = node('linearGradient', {id: gradientId, x1: '0%', x2: '100%'});
  const palette = PALETTES[result.scale.palette];
  palette.forEach((color, index) => gradient.append(node('stop', {
    offset: `${100 * index / (palette.length - 1)}%`, 'stop-color': color,
  })));
  defs.append(gradient);
  root.append(defs);
  const legend = node('g', {'aria-label': 'Expression color scale'});
  legend.append(node('text', {x: 32, y: 137, 'font-size': 13, 'font-weight': 600}, result.unit));
  legend.append(node('rect', {x: 32, y: 150, width: 210, height: 12, fill: `url(#${gradientId})`}));
  result.ticks.forEach((value, index) => legend.append(node('text', {
    x: 32 + 210 * index / (result.ticks.length - 1), y: 180, 'font-size': 12,
    'text-anchor': index === 0 ? 'start' : index === result.ticks.length - 1 ? 'end' : 'middle',
  }, formatNumber(value))));
  legend.append(node('rect', {x: 32, y: 194, width: 10, height: 10, fill: MISSING_COLOUR}));
  legend.append(node('text', {x: 48, y: 203, 'font-size': 12}, 'NA: no matching tissue or no data'));
  root.append(legend);
  root.append(node('line', {x1: 716, x2: 716, y1: 118, y2: height - 28, stroke: '#d6dfef'}));
  const rawX = data.transform === 'tpm' ? 1088 : 982;
  root.append(node('rect', {x: 736, y: 118, width: 364, height: 26, fill: '#f7f9fc'}));
  root.append(node('text', {x: 744, y: 135, 'font-size': 13}, 'Tissue'));
  root.append(node('text', {x: rawX, y: 135, 'font-size': 13, 'text-anchor': 'end'}, 'TPM'));
  if (data.transform !== 'tpm') root.append(node('text', {x: 1088, y: 135, 'font-size': 13, 'text-anchor': 'end'}, result.unit));
  let rowY = 144;
  for (const row of rows) {
    const group = node('g', {'data-atlas-tissue': row.tissue});
    group.append(node('rect', {x: 744, y: rowY + 12, width: 10, height: 10, fill: mixColour(row.value, result.scale)}));
    row.lines.forEach((line, index) => group.append(node('text', {x: 760, y: rowY + 22 + index * 18, 'font-size': 13}, line)));
    group.append(node('text', {x: rawX, y: rowY + 22, 'font-size': 13, 'text-anchor': 'end'}, formatValue(row.rawTpm)));
    if (data.transform !== 'tpm') group.append(node('text', {x: 1088, y: rowY + 22, 'font-size': 13, 'text-anchor': 'end'}, formatValue(row.value)));
    rowY += row.height;
    group.append(node('line', {x1: 744, x2: 1096, y1: rowY, y2: rowY, stroke: '#e6eaf1'}));
    root.append(group);
  }
  if (data.unmappedTissues.length) root.append(node('text', {x: 744, y: tableBottom + 25, 'font-size': 12, fill: '#64748b'}, '* Tissue not mapped to the diagram.'));
  root.append(node('text', {x: 32, y: height - 26, 'font-size': 12, fill: '#64748b'}, 'Scale covers all atlas tissues.'));
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(doc);
}

export async function downloadPdf(plant, result, data) {
  // Capture the figure before loading assets, so a concurrent query cannot
  // change the gene or colors halfway through an export.
  const blob = await vectorPdf(exportSvg(plant, result, data));
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${result.geneId.replace(/[^a-zA-Z0-9_.-]/g, '_')}_efp_${data.transform}.pdf`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

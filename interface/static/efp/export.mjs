import {buildFigure} from './figure.mjs?v=20260926-api1';
import {vectorPdf} from './pdf.mjs?v=20260926-api1';

const NS = 'http://www.w3.org/2000/svg';

/** SVG reference uses the same complete layout as both PDF exporters. */
export function exportSvg(plant, result, data) {
  const figure = buildFigure(result, data);
  const doc = document.implementation.createDocument(NS, 'svg', null);
  const root = doc.documentElement;
  const node = (tag, attrs = {}, content) => {
    const el = doc.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    if (content !== undefined) el.textContent = content;
    return el;
  };
  const {width, height} = figure;
  for (const [key, value] of Object.entries({width, height, viewBox: `0 0 ${width} ${height}`})) root.setAttribute(key, value);
  root.setAttribute('font-family', 'system-ui, "Noto Sans", "Liberation Sans", Arial, sans-serif');
  root.setAttribute('fill', '#14213d');
  root.setAttribute('role', 'img');
  root.setAttribute('aria-label', `${result.geneId} — Tissue Expression Map (eFP)`);
  root.append(node('desc', {}, `Tissue mean; ${result.unit}. Full diagram and displayed transcriptome tissue values. Gray: no matching tissue or no data.`));
  root.append(node('metadata', {}, JSON.stringify(figure.metadata)));
  for (const {type, attrs, text} of figure.elements) {
    if (type === 'plant') {
      const drawing = doc.importNode(plant, true);
      drawing.removeAttribute('style');
      drawing.setAttribute('viewBox', plant.dataset.originalViewBox || plant.getAttribute('viewBox'));
      drawing.removeAttribute('data-zoom');
      for (const [key, value] of Object.entries(attrs)) drawing.setAttribute(key, value);
      drawing.querySelectorAll('[tabindex]').forEach(el => el.removeAttribute('tabindex'));
      root.append(drawing);
    } else if (type === 'gradient') {
      const {palette, ...rect} = attrs;
      const id = `${plant.querySelector('[id]').id}-export-gradient`;
      const defs = node('defs');
      const gradient = node('linearGradient', {id, x1: '0%', x2: '100%'});
      palette.forEach((color, index) => gradient.append(node('stop', {
        offset: `${100 * index / (palette.length - 1)}%`, 'stop-color': color,
      })));
      defs.append(gradient);
      root.append(defs, node('rect', {...rect, fill: `url(#${id})`}));
    } else root.append(node(type, attrs, text));
  }
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(doc);
}

export async function downloadPdf(plant, result, data) {
  // Snapshot the data before loading assets, independent of viewport zoom.
  const figure = buildFigure(result, data);
  figure.viewBox = (plant.dataset.originalViewBox || plant.getAttribute('viewBox')).split(/\s+/).map(Number);
  const blob = await vectorPdf(figure);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${result.geneId.replace(/[^a-zA-Z0-9_.-]/g, '_')}_efp_${data.transform}.pdf`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

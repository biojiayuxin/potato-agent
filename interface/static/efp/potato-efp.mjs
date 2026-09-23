/** Dependency-free renderer for the supplied, trusted potato-template.svg. */
export const TISSUES = Object.freeze([
  ['flower', 'Flower'],
  ['perianth', 'Perianth'], ['flower_bud', 'Flower bud'], ['fruit', 'Fruit'], ['anther', 'Anther'],
  ['stem', 'Stem'], ['young_leaf', 'Young leaf'], ['leaf', 'Leaf'], ['mature_leaf', 'Mature leaf'],
  ['root', 'Root'], ['stolon', 'Stolon'], ['stolon_tip_S1', 'Stolon tip (S1)'],
  ['swelled_stolon_S2', 'Swelled stolon (S2)'], ['young_tuber_S3', 'Young tuber (S3)'],
  ['young_tuber_S4', 'Young tuber (S4)'], ['mature_tuber', 'Mature tuber'],
].map(([id, label]) => Object.freeze({id, label})));

export const PALETTE = Object.freeze([
  '#FFFFCC', '#FFEDA0', '#FED976', '#FEB24C', '#FD8D3C', '#FC4E2A', '#E31A1C', '#BD0026', '#800026',
]);
export const DIVERGING_PALETTE = Object.freeze(['#2166AC', '#F7F7F7', '#B2182B']);
export const PALETTES = Object.freeze({YlOrRd: PALETTE, diverging: DIVERGING_PALETTE});
export const MISSING_COLOUR = '#C9CED0';
const NS = 'http://www.w3.org/2000/svg';
const KNOWN_IDS = new Set(TISSUES.map(t => t.id));
const sessionToken = Math.random().toString(36).slice(2, 10);
let instanceNumber = 0;
const finite = n => typeof n === 'number' && Number.isFinite(n);
const record = o => o !== null && typeof o === 'object' && !Array.isArray(o);

function checkedScale(input) {
  if (!record(input)) throw new TypeError('必须提供 scale；真实数据的范围由项目确定。');
  const {min = 0, max, type = 'linear', palette = 'YlOrRd'} = input;
  if (!finite(min) || !finite(max) || max <= min) throw new TypeError('Scale requires finite min < max.');
  if (!['linear', 'log1p'].includes(type)) throw new TypeError('scale.type 仅支持 linear 或 log1p。');
  if (!Object.hasOwn(PALETTES, palette)) throw new TypeError('Unknown color palette.');
  if (type === 'log1p' && min < 0) throw new TypeError('Logarithmic scales require nonnegative values.');
  if (palette === 'YlOrRd' && min < 0) throw new TypeError('Sequential scales require nonnegative values.');
  return {min, max, type, palette};
}

export function mixColour(value, scale) {
  if (value === null) return MISSING_COLOUR;
  const transform = scale.type === 'log1p' ? Math.log1p : x => x;
  const palette = PALETTES[scale.palette];
  const t = Math.max(0, Math.min(1, (transform(value) - transform(scale.min)) /
    (transform(scale.max) - transform(scale.min)))) * (palette.length - 1);
  const index = Math.min(Math.floor(t), palette.length - 2), f = t - index;
  const [a, b] = palette.slice(index, index + 2);
  const rgb = [1, 3, 5].map(i => Math.round(parseInt(a.slice(i, i + 2), 16) * (1 - f) + parseInt(b.slice(i, i + 2), 16) * f));
  return '#' + rgb.map(n => n.toString(16).padStart(2, '0')).join('').toUpperCase();
}

/** Validate everything before touching the live SVG. Missing keys are NA, never zero. */
export function prepareExpression(payload, options = {}) {
  if (!record(payload) || !record(payload.expression)) throw new TypeError('需要包含 expression 对象的表达数据。');
  if (typeof payload.gene_id !== 'string' || !payload.gene_id.trim()) throw new TypeError('需要非空 gene_id。');
  if (typeof payload.unit !== 'string' || !payload.unit.trim()) throw new TypeError('需要明确 unit。');
  if (payload.tissue_labels !== undefined && (!record(payload.tissue_labels) ||
      Object.entries(payload.tissue_labels).some(([id, label]) => !KNOWN_IDS.has(id) || typeof label !== 'string' || !label.trim()))) {
    throw new TypeError('Tissue labels must contain known IDs and nonempty text.');
  }
  const unknown = Object.keys(payload.expression).filter(id => !KNOWN_IDS.has(id));
  if (unknown.length) throw new TypeError(`未知组织 ID：${unknown.join(', ')}`);
  const scale = checkedScale(options.scale ?? payload.scale);
  const rows = TISSUES.map(({id, label: defaultLabel}) => {
    const label = payload.tissue_labels?.[id] ?? defaultLabel;
    const raw = Object.prototype.hasOwnProperty.call(payload.expression, id) ? payload.expression[id] : null;
    if (raw !== null && (!finite(raw) || (raw < 0 && (scale.palette !== 'diverging' || scale.type !== 'linear')))) {
      throw new TypeError(`${id}: invalid expression value for this scale.`);
    }
    return {id, label, value: raw, missing: raw === null, colour: mixColour(raw, scale),
      clipped: raw !== null && (raw < scale.min || raw > scale.max)};
  });
  const transform = scale.type === 'log1p' ? Math.log1p : x => x;
  const inverse = scale.type === 'log1p' ? Math.expm1 : x => x;
  const ticks = Array.from({length: 5}, (_, i) => {
    if (i === 0) return scale.min;
    if (i === 4) return scale.max;
    return inverse(transform(scale.min) + (transform(scale.max) - transform(scale.min)) * i / 4);
  });
  return {geneId: payload.gene_id, unit: payload.unit, simulated: payload.data_status === 'simulated',
    scale, rows, ticks, missingIds: rows.filter(r => r.missing).map(r => r.id),
    validCount: rows.filter(r => !r.missing).length};
}

/** Namespace every SVG DOM ID while preserving the public data-tissue keys. */
function namespaceIds(svg, prefix) {
  const nodes = [svg, ...svg.querySelectorAll('*')];
  const ids = new Map();
  nodes.filter(node => node.hasAttribute('id')).forEach(node => {
    const id = node.getAttribute('id');
    if (ids.has(id)) throw new Error(`模板中存在重复 ID：${id}`);
    ids.set(id, prefix + id);
  });
  nodes.forEach(node => {
    for (const attr of Array.from(node.attributes)) {
      let value = attr.value;
      if (attr.name === 'id') value = ids.get(value);
      else if (attr.localName === 'href' && value.startsWith('#')) {
        const mapped = ids.get(value.slice(1));
        if (!mapped) throw new Error(`模板引用不存在：${value}`);
        value = '#' + mapped;
      } else if (['aria-labelledby', 'aria-describedby'].includes(attr.name)) {
        value = value.split(/\s+/).map(id => ids.get(id) ?? id).join(' ');
      } else {
        value = value.replace(/url\(\s*(['"]?)#([^)'"\s]+)\1\s*\)/g, (_, quote, id) => {
          if (!ids.has(id)) throw new Error(`模板引用不存在：${id}`);
          return `url(#${ids.get(id)})`;
        });
      }
      if (value !== attr.value) node.setAttributeNS(attr.namespaceURI, attr.name, value);
    }
  });
}

/**
 * Mount a trusted local template. Use one viewer instance for each displayed gene.
 * Database payload strings are inserted with textContent, not parsed as SVG/HTML.
 */
export function mountPotatoEFP(container, svgText) {
  if (!container || typeof container.replaceChildren !== 'function') throw new TypeError('需要一个 DOM 容器。');
  const parsed = new DOMParser().parseFromString(svgText, 'image/svg+xml');
  if (parsed.querySelector('parsererror')) throw new Error('SVG 无法解析。');
  const source = parsed.documentElement;
  if (source.localName !== 'svg' || source.namespaceURI !== NS) throw new Error('输入不是 SVG 模板。');
  const svg = container.ownerDocument.importNode(source, true);
  namespaceIds(svg, `pefp-${sessionToken}-${++instanceNumber}-`);
  // The page supplies one custom tooltip. Native SVG titles otherwise overlap
  // it, including a diagram-wide tooltip when hovering on empty space.
  svg.querySelectorAll('title').forEach(title => title.remove());
  svg.removeAttribute('aria-labelledby');
  svg.setAttribute('aria-label', 'Potato tissue expression map');
  const groups = new Map(Array.from(svg.querySelectorAll('[data-tissue]')).map(g => [g.dataset.tissue, g]));
  if (groups.size !== TISSUES.length || TISSUES.some(t => !groups.has(t.id))) throw new Error(`模板应包含指定的 ${TISSUES.length} 个组织。`);
  svg.setAttribute('width', '100%');
  svg.removeAttribute('height');
  svg.style.display = 'block';
  svg.style.width = '100%';
  svg.style.height = 'auto';
  svg.dataset.efpTemplate = 'v8';
  container.replaceChildren(svg);

  function render(payload, options = {}) {
    if (!container.contains(svg)) throw new Error('此 viewer 已被卸载。');
    const result = prepareExpression(payload, options);
    for (const row of result.rows) {
      const group = groups.get(row.id);
      group.setAttribute('fill', row.colour);
      group.style.fill = row.colour;
      group.dataset.expression = row.missing ? 'NA' : String(row.value);
      group.dataset.unit = result.unit;
      group.dataset.label = row.label;
      group.setAttribute('aria-label', `${result.geneId} · ${row.label}: ${row.missing ? 'NA / no data' : row.value + ' ' + result.unit}${row.clipped ? ' (color clipped to scale)' : ''}`);
      group.setAttribute('tabindex', '0');
    }
    for (const child of svg.children) {
      if (child.localName === 'desc') child.textContent = `${TISSUES.length} tissues; ${result.unit}; scale ${result.scale.min} to ${result.scale.max}. Gray means no data; zero is a valid value.`;
    }
    svg.dataset.geneId = result.geneId;
    svg.setAttribute('aria-label', `${result.geneId} · Potato tissue expression map`);
    return result;
  }

  return {svg, render, destroy() { svg.remove(); }};
}

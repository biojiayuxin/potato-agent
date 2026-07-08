const API_BASE = '/api/bulk-rnaseq';

const SCOPE_LABELS = {
  sample_tissue: 'Material by tissue',
  tissue: 'Tissue mean',
  sample_name: 'Material mean',
  sample: 'All runs',
};

const TRANSFORM_LABELS = {
  log2_tpm: 'log2(TPM + 1)',
  row_zscore: 'Row z-score',
  tpm: 'TPM',
};

const state = {
  payload: null,
  cells: [],
  selectedCell: null,
  loading: false,
};

const dom = {
  queryForm: document.getElementById('query-form'),
  genesInput: document.getElementById('genes-input'),
  scopeSelect: document.getElementById('scope-select'),
  transformSelect: document.getElementById('transform-select'),
  submitButton: document.getElementById('submit-button'),
  graphTitle: document.getElementById('graph-title'),
  graphSummary: document.getElementById('graph-summary'),
  viewerPanel: document.querySelector('.viewer-panel'),
  heatmap: document.getElementById('heatmap'),
  heatmapEmpty: document.getElementById('heatmap-empty'),
  tooltip: document.getElementById('heatmap-tooltip'),
  statusDetail: document.getElementById('status-detail'),
  legendMin: document.getElementById('legend-min'),
  legendMax: document.getElementById('legend-max'),
  legendBar: document.getElementById('legend-bar'),
  downloadPng: document.getElementById('download-png'),
  downloadTsv: document.getElementById('download-tsv'),
};

const escapeHtml = (value) => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#39;');

const formatNumber = (value, digits = 3) => {
  if (value === null || value === undefined || value === '') return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return '';
  if (Math.abs(number) >= 1000) return String(Math.round(number));
  return String(Number(number.toFixed(digits)));
};

const api = async (path) => {
  const response = await fetch(path, {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    let detail = response.statusText || 'Request failed';
    try {
      const payload = await response.json();
      detail = payload?.detail || detail;
    } catch {
      // Keep status text.
    }
    throw new Error(detail);
  }
  return response.json();
};

const parseGenes = (value) => Array.from(new Set(
  String(value || '')
    .split(/[\s,;]+/)
    .map((gene) => gene.trim())
    .filter(Boolean),
));

const shortGeneLabel = (geneId) => {
  const text = String(geneId || '');
  const match = text.match(/chr([0-9A-Za-z]+)G([0-9]+)$/);
  if (match) return `chr${match[1]}G${match[2]}`;
  return text.length > 22 ? `${text.slice(0, 12)}...${text.slice(-7)}` : text;
};

const shortLabel = (value, maxLength = 22) => {
  const text = String(value || '');
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
};

const columnLabel = (column, scope) => {
  if (scope === 'sample_tissue') return `${column.sampleName} ${column.tissue}`;
  if (scope === 'sample_name') return column.sampleName || column.label;
  if (scope === 'tissue') return column.tissue || column.label;
  return column.sampleColumn || column.label;
};

const hexToRgb = (hex) => {
  const normalized = hex.replace('#', '');
  return {
    r: parseInt(normalized.slice(0, 2), 16),
    g: parseInt(normalized.slice(2, 4), 16),
    b: parseInt(normalized.slice(4, 6), 16),
  };
};

const rgbToCss = ({ r, g, b }) => `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`;

const mixRgb = (left, right, amount) => ({
  r: left.r + (right.r - left.r) * amount,
  g: left.g + (right.g - left.g) * amount,
  b: left.b + (right.b - left.b) * amount,
});

const sequentialStops = [
  [0, '#f8fafc'],
  [1, '#ff0000'],
].map(([stop, color]) => [stop, hexToRgb(color)]);

const divergingStops = [
  [0, '#0000ff'],
  [0.5, '#f8fafc'],
  [1, '#ff0000'],
].map(([stop, color]) => [stop, hexToRgb(color)]);

const colorFromStops = (value, stops) => {
  const t = Math.max(0, Math.min(1, value));
  for (let index = 1; index < stops.length; index += 1) {
    const [rightStop, rightColor] = stops[index];
    const [leftStop, leftColor] = stops[index - 1];
    if (t <= rightStop) {
      const local = (t - leftStop) / Math.max(0.000001, rightStop - leftStop);
      return rgbToCss(mixRgb(leftColor, rightColor, local));
    }
  }
  return rgbToCss(stops[stops.length - 1][1]);
};

const displayRange = (payload) => {
  const minValue = Number(payload?.summary?.valueMin || 0);
  const maxValue = Number(payload?.summary?.valueMax || 0);
  if (payload?.transform === 'row_zscore') {
    const maxAbs = Math.max(1, Math.abs(minValue), Math.abs(maxValue));
    return { min: -maxAbs, max: maxAbs };
  }
  return { min: Math.min(0, minValue), max: Math.max(1, maxValue) };
};

const valueColor = (value, range, transform) => {
  if (transform === 'row_zscore') {
    const t = (value - range.min) / Math.max(0.000001, range.max - range.min);
    return colorFromStops(t, divergingStops);
  }
  const t = (value - range.min) / Math.max(0.000001, range.max - range.min);
  return colorFromStops(t, sequentialStops);
};

const setLoading = (loading) => {
  state.loading = loading;
  dom.submitButton.disabled = loading;
  dom.submitButton.textContent = loading ? 'Loading' : 'Search';
};

const updateLegend = (payload) => {
  if (!payload) {
    dom.legendMin.textContent = '0';
    dom.legendMax.textContent = 'max';
    dom.legendBar.style.background = 'linear-gradient(90deg, #f8fafc, #ff0000)';
    return;
  }
  const range = displayRange(payload);
  dom.legendMin.textContent = formatNumber(range.min, 2);
  dom.legendMax.textContent = formatNumber(range.max, 2);
  dom.legendBar.style.background = payload.transform === 'row_zscore'
    ? 'linear-gradient(90deg, #0000ff, #f8fafc, #ff0000)'
    : 'linear-gradient(90deg, #f8fafc, #ff0000)';
};

const resizeCanvas = () => {
  const rect = dom.heatmap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  if (dom.heatmap.width !== Math.floor(width * ratio)) {
    dom.heatmap.width = Math.floor(width * ratio);
  }
  if (dom.heatmap.height !== Math.floor(height * ratio)) {
    dom.heatmap.height = Math.floor(height * ratio);
  }
  const ctx = dom.heatmap.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
};

const drawNoData = () => {
  const { ctx, width, height } = resizeCanvas();
  ctx.clearRect(0, 0, width, height);
};

const maxTextWidth = (ctx, labels, cap) => Math.min(
  cap,
  Math.max(0, ...labels.map((label) => ctx.measureText(label).width)),
);

const wrapLabel = (label, maxChars) => {
  const text = String(label || '').replace(/\s*\([^)]*\)/g, '').trim();
  if (!text) return [''];
  const limit = Math.max(6, maxChars);
  if (text.length <= limit) return [text];
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [shortLabel(text, limit)];

  const lines = [];
  let current = '';
  words.forEach((word) => {
    const next = current ? `${current} ${word}` : word;
    if (next.length <= limit || !current) {
      current = next;
      return;
    }
    lines.push(current);
    current = word;
  });
  if (current) lines.push(current);
  if (lines.length <= 2) return lines.map((line) => shortLabel(line, limit));
  return [
    shortLabel(lines[0], limit),
    shortLabel(lines.slice(1).join(' '), limit),
  ];
};

const drawWrappedColumnLabels = (ctx, labels, left, top, cellWidth, availableCount, options = {}) => {
  ctx.save();
  ctx.fillStyle = '#334155';
  ctx.font = '750 11px Inter, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const maxChars = Math.max(6, Math.floor((cellWidth - 6) / 6.2));
  const minLabelGap = options.minLabelGap || 38;
  const step = availableCount <= 24 ? 1 : Math.max(1, Math.ceil(minLabelGap / Math.max(1, cellWidth)));
  for (let index = 0; index < availableCount; index += step) {
    const x = left + index * cellWidth + cellWidth / 2;
    const lines = wrapLabel(labels[index], maxChars).slice(0, 2);
    const y = lines.length > 1 ? top - 30 : top - 22;
    ctx.fillText(lines[0], x, y, Math.max(20, cellWidth - 6));
    if (lines[1]) {
      ctx.fillText(lines[1], x, y + 14, Math.max(20, cellWidth - 6));
    }
  }
  ctx.restore();
};

const drawColumnLabels = (ctx, labels, left, top, cellWidth, availableCount, options = {}) => {
  if (options.hidden) return;
  if (options.mode === 'wrapped') {
    drawWrappedColumnLabels(ctx, labels, left, top, cellWidth, availableCount, options);
    return;
  }
  ctx.save();
  ctx.fillStyle = '#334155';
  ctx.font = '700 11px Inter, system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  const minLabelGap = options.minLabelGap || 54;
  const maxLabelLength = options.maxLabelLength || 24;
  const maxWidth = options.maxWidth || 118;
  const step = Math.max(1, Math.ceil(minLabelGap / Math.max(1, cellWidth)));
  for (let index = 0; index < availableCount; index += step) {
    const x = left + index * cellWidth + cellWidth / 2;
    ctx.save();
    ctx.translate(x, top - 8);
    ctx.rotate(-Math.PI / 4);
    ctx.fillText(shortLabel(labels[index], maxLabelLength), 0, 0, maxWidth);
    ctx.restore();
  }
  ctx.restore();
};

const drawRowLabels = (ctx, labels, left, top, cellHeight, availableCount) => {
  ctx.save();
  ctx.fillStyle = '#334155';
  ctx.font = '700 11px Inter, system-ui, sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  const step = Math.max(1, Math.ceil(10 / Math.max(1, cellHeight)));
  for (let index = 0; index < availableCount; index += step) {
    const y = top + index * cellHeight + cellHeight / 2;
    ctx.fillText(shortLabel(labels[index], 26), left - 8, y, left - 16);
  }
  ctx.restore();
};

const drawGridFrame = (ctx, left, top, width, height) => {
  ctx.save();
  ctx.strokeStyle = 'rgba(100, 116, 139, 0.28)';
  ctx.lineWidth = 1;
  ctx.strokeRect(left, top, width, height);
  ctx.restore();
};

const addCell = (cell) => {
  state.cells.push(cell);
};

const compactGridHeight = (rowCount, availableHeight) => {
  if (rowCount <= 1) return Math.min(38, availableHeight);
  if (rowCount <= 3) return Math.min(rowCount * 34, availableHeight);
  if (rowCount <= 12) return Math.min(rowCount * 28, availableHeight);
  return availableHeight;
};

const drawSingleGenePivot = (ctx, payload, width, height) => {
  const gene = payload.genes[0];
  const tissues = [];
  const sampleNames = [];
  const tissueSeen = new Set();
  const sampleSeen = new Set();
  const columnByPair = new Map();
  payload.columns.forEach((column, columnIndex) => {
    if (column.tissue && !tissueSeen.has(column.tissue)) {
      tissueSeen.add(column.tissue);
      tissues.push(column.tissue);
    }
    if (column.sampleName && !sampleSeen.has(column.sampleName)) {
      sampleSeen.add(column.sampleName);
      sampleNames.push(column.sampleName);
    }
    columnByPair.set(`${column.sampleName}\t${column.tissue}`, { column, columnIndex });
  });

  ctx.font = '700 11px Inter, system-ui, sans-serif';
  const left = Math.max(92, maxTextWidth(ctx, sampleNames, 162) + 16);
  const top = 56;
  const right = 24;
  const bottom = 24;
  const gridWidth = Math.max(1, width - left - right);
  const gridHeight = Math.max(1, height - top - bottom);
  const cellWidth = gridWidth / Math.max(1, tissues.length);
  const cellHeight = gridHeight / Math.max(1, sampleNames.length);
  const range = displayRange(payload);

  drawColumnLabels(ctx, tissues, left, top, cellWidth, tissues.length, {
    mode: 'wrapped',
    maxLabelLength: 34,
    maxWidth: 150,
    minLabelGap: 38,
  });
  drawRowLabels(ctx, sampleNames, left, top, cellHeight, sampleNames.length);

  sampleNames.forEach((sampleName, rowIndex) => {
    tissues.forEach((tissue, tissueIndex) => {
      const x = left + tissueIndex * cellWidth;
      const y = top + rowIndex * cellHeight;
      const pair = columnByPair.get(`${sampleName}\t${tissue}`);
      if (!pair) {
        ctx.fillStyle = 'rgba(248, 250, 252, 0.82)';
        ctx.fillRect(x, y, cellWidth, cellHeight);
        return;
      }
      const value = Number(payload.values[0][pair.columnIndex] || 0);
      const raw = Number(payload.rawValues[0][pair.columnIndex] || 0);
      const sd = Number(payload.sdValues[0][pair.columnIndex] || 0);
      const n = Number(payload.nValues[0][pair.columnIndex] || 0);
      ctx.fillStyle = valueColor(value, range, payload.transform);
      ctx.fillRect(x, y, Math.max(0.5, cellWidth), Math.max(0.5, cellHeight));
      addCell({
        x,
        y,
        width: cellWidth,
        height: cellHeight,
        gene,
        column: pair.column,
        columnIndex: pair.columnIndex,
        value,
        raw,
        sd,
        n,
        sampleName,
        tissue,
      });
    });
  });

  if (cellWidth >= 5 && cellHeight >= 5) {
    ctx.save();
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.56)';
    ctx.lineWidth = 1;
    for (let index = 0; index <= tissues.length; index += 1) {
      const x = left + index * cellWidth;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, top + gridHeight);
      ctx.stroke();
    }
    for (let index = 0; index <= sampleNames.length; index += 1) {
      const y = top + index * cellHeight;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(left + gridWidth, y);
      ctx.stroke();
    }
    ctx.restore();
  }
  drawGridFrame(ctx, left, top, gridWidth, gridHeight);
};

const drawMatrixHeatmap = (ctx, payload, width, height) => {
  const rowLabels = payload.genes.map((gene) => shortGeneLabel(gene.geneId));
  const colLabels = payload.columns.map((column) => columnLabel(column, payload.scope));
  const hideColumnLabels = payload.scope === 'sample';
  const compactWrappedLabels = payload.genes.length === 1 && payload.columns.length <= 24;
  ctx.font = '700 11px Inter, system-ui, sans-serif';
  const left = Math.max(92, maxTextWidth(ctx, rowLabels, 190) + 16);
  const top = hideColumnLabels ? 24 : (compactWrappedLabels ? 56 : 78);
  const right = 24;
  const bottom = 24;
  const gridWidth = Math.max(1, width - left - right);
  const availableHeight = Math.max(1, height - top - bottom);
  const rowCount = payload.genes.length;
  const colCount = payload.columns.length;
  const gridHeight = compactGridHeight(rowCount, availableHeight);
  const cellWidth = gridWidth / Math.max(1, colCount);
  const cellHeight = gridHeight / Math.max(1, rowCount);
  const range = displayRange(payload);

  drawColumnLabels(ctx, colLabels, left, top, cellWidth, colCount, {
    hidden: hideColumnLabels,
    mode: compactWrappedLabels ? 'wrapped' : 'rotated',
    maxLabelLength: payload.scope === 'sample_tissue' ? 30 : 24,
    maxWidth: payload.scope === 'sample_tissue' ? 140 : 118,
    minLabelGap: payload.columns.length > 80 ? 74 : 54,
  });
  drawRowLabels(ctx, rowLabels, left, top, cellHeight, rowCount);

  payload.genes.forEach((gene, rowIndex) => {
    payload.columns.forEach((column, columnIndex) => {
      const x = left + columnIndex * cellWidth;
      const y = top + rowIndex * cellHeight;
      const value = Number(payload.values[rowIndex][columnIndex] || 0);
      const raw = Number(payload.rawValues[rowIndex][columnIndex] || 0);
      const sd = Number(payload.sdValues[rowIndex][columnIndex] || 0);
      const n = Number(payload.nValues[rowIndex][columnIndex] || 0);
      ctx.fillStyle = valueColor(value, range, payload.transform);
      ctx.fillRect(x, y, Math.max(0.5, cellWidth), Math.max(0.5, cellHeight));
      addCell({
        x,
        y,
        width: cellWidth,
        height: cellHeight,
        gene,
        column,
        columnIndex,
        value,
        raw,
        sd,
        n,
        sampleName: column.sampleName || '',
        tissue: column.tissue || '',
      });
    });
  });

  if (cellWidth >= 7 && cellHeight >= 7) {
    ctx.save();
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.56)';
    ctx.lineWidth = 1;
    for (let index = 0; index <= colCount; index += 1) {
      const x = left + index * cellWidth;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, top + gridHeight);
      ctx.stroke();
    }
    for (let index = 0; index <= rowCount; index += 1) {
      const y = top + index * cellHeight;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(left + gridWidth, y);
      ctx.stroke();
    }
    ctx.restore();
  }
  drawGridFrame(ctx, left, top, gridWidth, gridHeight);
};

const renderHeatmap = () => {
  state.cells = [];
  const compactPanel = Boolean(
    state.payload
    && state.payload.genes.length === 1
    && state.payload.scope !== 'sample_tissue',
  );
  dom.viewerPanel.classList.toggle('is-compact', compactPanel);
  const { ctx, width, height } = resizeCanvas();
  ctx.clearRect(0, 0, width, height);
  updateLegend(state.payload);

  if (!state.payload) {
    dom.heatmapEmpty.hidden = false;
    return;
  }
  dom.heatmapEmpty.hidden = true;
  if (state.payload.scope === 'sample_tissue' && state.payload.genes.length === 1) {
    drawSingleGenePivot(ctx, state.payload, width, height);
  } else {
    drawMatrixHeatmap(ctx, state.payload, width, height);
  }
};

const cellAt = (x, y) => state.cells.find((cell) => (
  x >= cell.x
  && x <= cell.x + cell.width
  && y >= cell.y
  && y <= cell.y + cell.height
));

const replicateLines = (cell) => {
  const replicatePayload = state.payload?.replicates?.find(
    (entry) => entry.columnIndex === cell.columnIndex,
  );
  const samples = replicatePayload?.samples || [];
  if (!samples.length) return '';
  const lines = samples.slice(0, 6).map((sample) => (
    `${escapeHtml(sample.sampleColumn)}: ${formatNumber(sample.tpm)}`
  ));
  if (samples.length > 6) lines.push(`+ ${samples.length - 6} more`);
  return `<div class="tooltip-samples">${lines.join('<br>')}</div>`;
};

const showTooltip = (event, cell) => {
  dom.tooltip.hidden = false;
  dom.tooltip.innerHTML = `
    <div class="tooltip-title">${escapeHtml(cell.gene.geneId)}</div>
    <div class="tooltip-grid">
      <span>Column</span><span>${escapeHtml(cell.column.label || cell.column.id || '')}</span>
      <span>Value</span><span>${formatNumber(cell.value)}</span>
      <span>Mean TPM</span><span>${formatNumber(cell.raw)}</span>
      <span>SD TPM</span><span>${formatNumber(cell.sd)}</span>
      <span>n</span><span>${cell.n}</span>
    </div>
    ${replicateLines(cell)}
  `;
  const stageRect = dom.heatmap.parentElement.getBoundingClientRect();
  const tooltipRect = dom.tooltip.getBoundingClientRect();
  const offset = 8;
  const pointerX = event.clientX - stageRect.left;
  const pointerY = event.clientY - stageRect.top;
  let left = pointerX + offset;
  let top = pointerY + offset;
  if (left + tooltipRect.width + offset > stageRect.width) {
    left = pointerX - tooltipRect.width - offset;
  }
  if (top + tooltipRect.height + offset > stageRect.height) {
    top = pointerY - tooltipRect.height - offset;
  }
  dom.tooltip.style.left = `${Math.max(8, left)}px`;
  dom.tooltip.style.top = `${Math.max(8, top)}px`;
};

const hideTooltip = () => {
  dom.tooltip.hidden = true;
};

const updateTitle = (payload) => {
  if (!payload) {
    dom.graphTitle.textContent = 'No query loaded';
    dom.graphSummary.textContent = 'Enter one or more gene IDs.';
    return;
  }
  const geneCount = payload.genes.length;
  const firstGene = payload.genes[0]?.geneId || '';
  dom.graphTitle.textContent = geneCount === 1
    ? firstGene
    : `${geneCount} genes`;
  dom.graphSummary.textContent = [
    SCOPE_LABELS[payload.scope],
    TRANSFORM_LABELS[payload.transform],
    `${payload.columns.length} columns`,
  ].join(' | ');
};

const setError = (message) => {
  state.payload = null;
  state.selectedCell = null;
  dom.graphTitle.textContent = 'Query failed';
  dom.graphSummary.textContent = message;
  renderHeatmap();
};

const buildQueryUrl = () => {
  const params = new URLSearchParams();
  params.set('genes', dom.genesInput.value.trim());
  params.set('scope', dom.scopeSelect.value);
  params.set('transform', dom.transformSelect.value);
  return `${API_BASE}/expression?${params.toString()}`;
};

const runQuery = async () => {
  const genes = parseGenes(dom.genesInput.value);
  if (!genes.length) {
    setError('At least one gene ID is required.');
    return;
  }
  setLoading(true);
  dom.graphTitle.textContent = 'Loading expression';
  dom.graphSummary.textContent = genes.join(', ');
  try {
    const payload = await api(buildQueryUrl());
    state.payload = payload;
    state.selectedCell = null;
    updateTitle(payload);
    renderHeatmap();
  } catch (error) {
    setError(error.message || 'Query failed');
  } finally {
    setLoading(false);
  }
};

const loadStatus = async () => {
  try {
    const status = await api(`${API_BASE}/status`);
    dom.statusDetail.classList.remove('muted');
    dom.statusDetail.innerHTML = `
      <dl class="kv">
        <dt>Dataset</dt><dd>${escapeHtml(status.dataset || '')}</dd>
        <dt>Genes</dt><dd>${formatNumber(status.counts?.genes || 0, 0)}</dd>
        <dt>Samples</dt><dd>${formatNumber(status.counts?.samples || 0, 0)}</dd>
        <dt>Tissues</dt><dd>${formatNumber(status.groups?.tissue || 0, 0)}</dd>
      </dl>
    `;
  } catch (error) {
    dom.statusDetail.classList.add('muted');
    dom.statusDetail.textContent = error.message || 'Database unavailable.';
  }
};

const downloadBlob = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const downloadPng = () => {
  if (!state.payload) return;
  dom.heatmap.toBlob((blob) => {
    if (blob) downloadBlob(blob, 'bulk_rnaseq_heatmap.png');
  }, 'image/png');
};

const tsvValue = (value) => {
  const text = String(value ?? '');
  if (/[\t\n\r"]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
};

const downloadTsv = () => {
  const payload = state.payload;
  if (!payload) return;
  const lines = [
    [
      'gene_id',
      'transcript_id',
      'column_id',
      'column_label',
      'sample_name',
      'tissue',
      'transform',
      'value',
      'mean_tpm',
      'sd_tpm',
      'n',
    ].join('\t'),
  ];
  payload.genes.forEach((gene, rowIndex) => {
    payload.columns.forEach((column, columnIndex) => {
      lines.push([
        gene.geneId,
        gene.transcriptId,
        column.id,
        column.label,
        column.sampleName || '',
        column.tissue || '',
        payload.transform,
        payload.values[rowIndex][columnIndex],
        payload.rawValues[rowIndex][columnIndex],
        payload.sdValues[rowIndex][columnIndex],
        payload.nValues[rowIndex][columnIndex],
      ].map(tsvValue).join('\t'));
    });
  });
  downloadBlob(
    new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' }),
    'bulk_rnaseq_expression.tsv',
  );
};

dom.queryForm.addEventListener('submit', (event) => {
  event.preventDefault();
  runQuery();
});

dom.downloadPng.addEventListener('click', downloadPng);
dom.downloadTsv.addEventListener('click', downloadTsv);

dom.heatmap.addEventListener('pointermove', (event) => {
  if (!state.payload) return;
  const rect = dom.heatmap.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const cell = cellAt(x, y);
  if (!cell) {
    hideTooltip();
    return;
  }
  showTooltip(event, cell);
});

dom.heatmap.addEventListener('pointerleave', () => {
  hideTooltip();
});

window.addEventListener('resize', () => {
  renderHeatmap();
});

drawNoData();
updateLegend(null);
loadStatus();

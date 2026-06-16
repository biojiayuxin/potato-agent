const canvas = document.getElementById("plot");
const ctx = canvas.getContext("2d");
const dotplotCanvas = document.getElementById("dotplot");
const dotCtx = dotplotCanvas.getContext("2d");

const MAX_SCALE = 0.3;
const MIN_SCALE = 0.0005;
const PANEL_PADDING = 160;
const PANEL_GUTTER = 420;
const PANEL_ROW_GUTTER = 520;
const PANEL_LABEL_HEIGHT = 220;
const OUTLINE_MIN_SCREEN_PX = 0.15;
const OUTLINE_MAX_SCREEN_PX = 1.25;
const OUTLINE_BASE_SCREEN_PX = 0.22;
const API_BASE = "/api/spatial";
const DATA_BASE = `${API_BASE}/data`;
const DATASET_LABEL_OVERRIDES = new Map([
  ["S1 + S2", "Stolon and tuber"],
  ["S1 + Stem", "Stolon and Stem"],
]);
const SAMPLE_LABEL_OVERRIDES = new Map([
  ["S1", "Stolon (S1)"],
  ["S2", "Early Swelling Tuber (S2)"],
]);

const state = {
  datasetCatalog: [],
  currentDataset: null,
  samples: {},
  expressions: {},
  clusterMeta: { clusters: [], maps: {}, names: new Map() },
  tissueMeta: { tissues: [], maps: {}, error: "" },
  categoryColors: { clusters: new Map(), tissues: new Map() },
  displayMode: "gene",
  selectedCluster: "",
  selectedTissue: "",
  currentSample: "",
  currentGene: "",
  expressionRange: { vmin: 0, vmax: 0 },
  view: { scale: 1, x: 0, y: 0 },
  dragging: false,
  lastPointer: null,
  devicePixelRatio: window.devicePixelRatio || 1,
  drawPending: false,
  dotplot: { payload: null, error: "", loading: false },
  dotplotDrawPending: false,
  dotplotPoints: [],
};

const els = {
  form: document.getElementById("geneForm"),
  input: document.getElementById("geneInput"),
  geneList: document.getElementById("geneList"),
  modeSelect: document.getElementById("modeSelect"),
  datasetSelect: document.getElementById("datasetSelect"),
  sampleToggle: document.querySelector(".sample-toggle"),
  modePanels: document.querySelectorAll("[data-mode-panel]"),
  viewer: document.querySelector(".viewer"),
  clusterSelect: document.getElementById("clusterSelect"),
  tissueSelect: document.getElementById("tissueSelect"),
  dotplotTooltip: document.getElementById("dotplotTooltip"),
  legend: document.querySelector(".legend"),
  legendMax: document.getElementById("legendMax"),
  legendMin: document.getElementById("legendMin"),
  scaleText: document.getElementById("scaleText"),
};

function datasetDisplayLabel(dataset) {
  const label = dataset.label || dataset.id;
  return DATASET_LABEL_OVERRIDES.get(label) || label;
}

function sampleDisplayLabel(sample) {
  const id = String(sample.id || "");
  const label = sample.label || id;
  return SAMPLE_LABEL_OVERRIDES.get(id) || SAMPLE_LABEL_OVERRIDES.get(label) || label;
}

const REDS = [
  [255, 245, 240],
  [254, 224, 210],
  [252, 187, 161],
  [252, 146, 114],
  [251, 106, 74],
  [239, 59, 44],
  [203, 24, 29],
  [165, 15, 21],
  [103, 0, 13],
];

const CATEGORY_MUTED = [214, 219, 226];
const FALLBACK_CATEGORY_COLORS = [
  [78, 121, 167],
  [242, 142, 43],
  [225, 87, 89],
  [118, 183, 178],
  [89, 161, 79],
  [237, 201, 72],
  [176, 122, 161],
  [255, 157, 167],
  [156, 117, 95],
  [186, 176, 172],
];
const DEFAULT_TISSUE_MENU_ORDER = [
  "epidermis/periderm",
  "cortex",
  "perimedullary region",
  "pith",
  "others",
  "unknown",
];
const STEM_TISSUE_MENU_ORDER = [
  "epidermis",
  "vascular tissue",
  "ground tissue",
  "unknown",
];

function setStatus(_message) {}

function formatNumber(value, digits = 3) {
  if (!Number.isFinite(value)) return "-";
  if (Math.abs(value) >= 1000) return value.toLocaleString();
  return Number(value.toFixed(digits)).toString();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => (
    {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#039;",
    }[char]
  ));
}

function tileKey(x, y) {
  return `${x},${y}`;
}

function bboxWidth(bbox) {
  return bbox[2] - bbox[0] + 1;
}

function bboxHeight(bbox) {
  return bbox[3] - bbox[1] + 1;
}

function bboxIntersects(a, b) {
  return !(a[2] < b.left || a[0] > b.right || a[3] < b.top || a[1] > b.bottom);
}

function rectsIntersect(a, b) {
  return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
}

function sampleConfig(sampleId) {
  const dataset = state.currentDataset;
  return dataset ? (dataset.samples || []).find((sample) => sample.id === sampleId) : null;
}

function sampleLabel(sampleId) {
  const sample = sampleConfig(sampleId);
  return sample ? sampleDisplayLabel(sample) : sampleId;
}

function datasetParam() {
  return state.currentDataset ? `dataset=${encodeURIComponent(state.currentDataset.id)}` : "";
}

function apiUrl(path, params = {}) {
  const query = new URLSearchParams();
  if (state.currentDataset) query.set("dataset", state.currentDataset.id);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, value);
    }
  }
  const qs = query.toString();
  const apiPath = path.startsWith("/api/") ? `${API_BASE}${path.slice(4)}` : path;
  return qs ? `${apiPath}?${qs}` : apiPath;
}

function spatialDataUrl(path, datasetId = null) {
  const raw = String(path || "").trim();
  if (!raw) return "";
  if (raw.startsWith(DATA_BASE)) return raw;
  if (raw.startsWith("/dataset-data/")) {
    const parts = raw.replace(/^\/+/, "").split("/");
    const selectedDataset = parts[1] || datasetId || (state.currentDataset ? state.currentDataset.id : "");
    const rest = parts.slice(2).map(encodeURIComponent).join("/");
    return `${DATA_BASE}/${encodeURIComponent(selectedDataset)}${rest ? `/${rest}` : ""}`;
  }
  if (raw === "/data") return `${DATA_BASE}/_root/data`;
  if (raw.startsWith("/data/")) {
    return `${DATA_BASE}/_root/data/${raw.slice("/data/".length).split("/").map(encodeURIComponent).join("/")}`;
  }
  if (raw.startsWith("/")) return raw;
  const selectedDataset = datasetId || (state.currentDataset ? state.currentDataset.id : "");
  return `${DATA_BASE}/${encodeURIComponent(selectedDataset)}/${raw.split("/").map(encodeURIComponent).join("/")}`;
}

function normalizeManifestUrls(manifest, datasetId) {
  for (const tile of manifest.tiles || []) {
    tile.url = spatialDataUrl(tile.url, datasetId);
  }
  return manifest;
}

function panelColumns(sample, replicateCount) {
  const config = sampleConfig(sample);
  if (config && Number(config.columns) > 0) return Number(config.columns);
  return Math.max(1, replicateCount);
}

function prepareSpatial(manifest, replicatePayload) {
  const tileMap = new Map();
  for (const tile of manifest.tiles || []) {
    tileMap.set(tileKey(tile.x, tile.y), tile);
  }

  const replicates = replicatePayload.replicates || [];
  const panels = [];
  const cellToPanel = new Map();
  const tileToPanels = new Map();
  const referenceRep = replicates.find((rep) => rep.id === `${manifest.sample.toLowerCase()}_rep1`) || replicates[0];
  const displayWidth = referenceRep ? bboxWidth(referenceRep.bbox) : manifest.width;
  const displayHeight = referenceRep ? bboxHeight(referenceRep.bbox) : manifest.height;
  const columns = panelColumns(manifest.sample, replicates.length);
  const rows = Math.max(1, Math.ceil(replicates.length / columns));
  let assignedCellCount = 0;

  for (const [index, rep] of replicates.entries()) {
    const col = index % columns;
    const row = Math.floor(index / columns);
    const sourceWidth = bboxWidth(rep.bbox);
    const sourceHeight = bboxHeight(rep.bbox);
    const panel = {
      ...rep,
      x: PANEL_PADDING + col * (displayWidth + PANEL_GUTTER),
      y: PANEL_LABEL_HEIGHT + PANEL_PADDING + row * (displayHeight + PANEL_ROW_GUTTER),
      width: displayWidth,
      height: displayHeight,
      sourceWidth,
      sourceHeight,
      scaleX: displayWidth / sourceWidth,
      scaleY: displayHeight / sourceHeight,
    };
    panel.bounds = {
      left: panel.x,
      top: panel.y,
      right: panel.x + displayWidth,
      bottom: panel.y + displayHeight,
    };

    panels.push(panel);
    assignedCellCount += rep.assignedCellCount || rep.cellIds.length;

    for (const cellId of rep.cellIds) {
      cellToPanel.set(cellId, panel);
    }
    for (const key of rep.tileKeys || []) {
      if (!tileToPanels.has(key)) tileToPanels.set(key, []);
      tileToPanels.get(key).push(panel);
    }

  }

  const layoutWidth = Math.max(
    1,
    panels.length ? PANEL_PADDING * 2 + columns * displayWidth + (columns - 1) * PANEL_GUTTER : manifest.width
  );
  const layoutHeight = panels.length
    ? PANEL_LABEL_HEIGHT + PANEL_PADDING * 2 + rows * displayHeight + (rows - 1) * PANEL_ROW_GUTTER
    : manifest.height;

  return {
    ...manifest,
    panels,
    panelColumns: columns,
    panelRows: rows,
    cellToPanel,
    tileToPanels,
    layoutWidth,
    layoutHeight,
    assignedCellCount,
    tileMap,
    loadedTiles: new Map(),
    loadingTiles: new Set(),
    failedTiles: new Set(),
    pathCache: new Map(),
  };
}

function requestDraw() {
  if (state.drawPending) return;
  state.drawPending = true;
  window.requestAnimationFrame(() => {
    state.drawPending = false;
    draw();
  });
}

function requestDotplotDraw() {
  if (state.dotplotDrawPending) return;
  state.dotplotDrawPending = true;
  window.requestAnimationFrame(() => {
    state.dotplotDrawPending = false;
    drawDotplot();
  });
}

function resizeCanvasElement(targetCanvas) {
  const rect = targetCanvas.getBoundingClientRect();
  targetCanvas.width = Math.max(1, Math.floor(rect.width * state.devicePixelRatio));
  targetCanvas.height = Math.max(1, Math.floor(rect.height * state.devicePixelRatio));
}

function resizeCanvas() {
  state.devicePixelRatio = window.devicePixelRatio || 1;
  resizeCanvasElement(canvas);
  resizeCanvasElement(dotplotCanvas);
  requestDraw();
  requestDotplotDraw();
}

function currentSpatial() {
  return state.samples[state.currentSample];
}

function currentExpression() {
  return state.expressions[state.currentSample] || { map: new Map(), max: 0, min: 0, nonzero: 0 };
}

function currentClusterMap() {
  return state.clusterMeta.maps[state.currentSample] || new Map();
}

function currentTissueMap() {
  return state.tissueMeta.maps[state.currentSample] || new Map();
}

function selectedClusterLabel() {
  return state.selectedCluster ? `Cluster ${state.selectedCluster}` : "all clusters";
}

function selectedTissueLabel() {
  if (!state.selectedTissue) return "all tissues";
  const tissue = state.tissueMeta.tissues.find((item) => String(item.id) === state.selectedTissue);
  return tissue ? tissue.label || tissue.id : state.selectedTissue;
}

function activeHighlightMode() {
  if (state.displayMode === "cluster") return "cluster";
  if (state.displayMode === "tissue") return "tissue";
  return "expression";
}

function fitView() {
  const spatial = currentSpatial();
  if (!spatial) return;

  const rect = canvas.getBoundingClientRect();
  const margin = 36;
  const sx = Math.max(1, rect.width - margin * 2) / spatial.layoutWidth;
  const sy = Math.max(1, rect.height - margin * 2) / spatial.layoutHeight;
  const scale = Math.max(MIN_SCALE, Math.min(sx, sy));

  spatial.fitScale = scale;
  state.view.scale = scale;
  state.view.x = (rect.width - spatial.layoutWidth * scale) / 2;
  state.view.y = (rect.height - spatial.layoutHeight * scale) / 2;
  updateScaleText();
}

function updateScaleText() {
  const spatial = currentSpatial();
  if (!spatial) {
    els.scaleText.textContent = "-";
    return;
  }
  const fitted = Math.min(canvas.clientWidth / spatial.layoutWidth, canvas.clientHeight / spatial.layoutHeight);
  els.scaleText.textContent = `${Math.round((state.view.scale / fitted) * 100)}%`;
}

function colorForValue(value, range) {
  const min = Number.isFinite(range.vmin) ? range.vmin : 0;
  const max = Number.isFinite(range.vmax) ? range.vmax : 0;
  const t = max > min ? Math.max(0, Math.min(1, (value - min) / (max - min))) : 0;
  const scaled = t * (REDS.length - 1);
  const idx = Math.min(REDS.length - 2, Math.floor(scaled));
  const local = scaled - idx;
  const a = REDS[idx];
  const b = REDS[idx + 1];
  return a.map((component, i) => Math.round(component + (b[i] - component) * local));
}

function rgbCss(rgb) {
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}

function hexToRgb(hex) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!match) return null;
  const value = Number.parseInt(match[1], 16);
  return [
    (value >> 16) & 255,
    (value >> 8) & 255,
    value & 255,
  ];
}

function fallbackCategoryColor(id) {
  const text = String(id || "");
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
  }
  return FALLBACK_CATEGORY_COLORS[hash % FALLBACK_CATEGORY_COLORS.length];
}

function categoryColor(kind, id) {
  const colorMap = kind === "tissue" ? state.categoryColors.tissues : state.categoryColors.clusters;
  const configured = colorMap.get(String(id));
  return configured || fallbackCategoryColor(id);
}

function maybeMutedCategoryColor(rgb, id, selectedId) {
  if (selectedId && String(id) !== selectedId) return CATEGORY_MUTED;
  return rgb;
}

function invertRgb(rgb) {
  return [255 - rgb[0], 255 - rgb[1], 255 - rgb[2]];
}

function outlineScreenWidth(spatial, scale) {
  const fitScale = spatial.fitScale || scale || 1;
  const relativeZoom = scale / fitScale;
  return Math.max(
    OUTLINE_MIN_SCREEN_PX,
    Math.min(OUTLINE_MAX_SCREEN_PX, OUTLINE_BASE_SCREEN_PX * relativeZoom)
  );
}

function createCellPath(cell) {
  const path = new Path2D();
  const x0 = cell.bbox[0];
  const y0 = cell.bbox[1];

  for (const contour of cell.contours || []) {
    if (!contour.length) continue;
    path.moveTo(x0 + contour[0][0], y0 + contour[0][1]);
    for (let i = 1; i < contour.length; i += 1) {
      path.lineTo(x0 + contour[i][0], y0 + contour[i][1]);
    }
    path.closePath();
  }

  return path;
}

function getCellPath(spatial, cell) {
  let path = spatial.pathCache.get(cell.id);
  if (!path) {
    path = createCellPath(cell);
    spatial.pathCache.set(cell.id, path);
  }
  return path;
}

function visibleBounds(rect) {
  const { scale, x, y } = state.view;
  return {
    left: -x / scale,
    top: -y / scale,
    right: (-x + rect.width) / scale,
    bottom: (-y + rect.height) / scale,
  };
}

function originalBoundsForPanel(panel, bounds) {
  const left = Math.max(panel.bounds.left, bounds.left);
  const top = Math.max(panel.bounds.top, bounds.top);
  const right = Math.min(panel.bounds.right, bounds.right);
  const bottom = Math.min(panel.bounds.bottom, bounds.bottom);
  return {
    left: Math.max(panel.bbox[0], panel.bbox[0] + (left - panel.x) / panel.scaleX),
    top: Math.max(panel.bbox[1], panel.bbox[1] + (top - panel.y) / panel.scaleY),
    right: Math.min(panel.bbox[2], panel.bbox[0] + (right - panel.x) / panel.scaleX),
    bottom: Math.min(panel.bbox[3], panel.bbox[1] + (bottom - panel.y) / panel.scaleY),
  };
}

function tileKeysForOriginalBounds(spatial, bounds) {
  const keys = [];
  const maxTileX = Math.ceil(spatial.width / spatial.tileSize) - 1;
  const maxTileY = Math.ceil(spatial.height / spatial.tileSize) - 1;
  const tx0 = Math.max(0, Math.floor(Math.max(0, bounds.left) / spatial.tileSize));
  const ty0 = Math.max(0, Math.floor(Math.max(0, bounds.top) / spatial.tileSize));
  const tx1 = Math.min(maxTileX, Math.floor(Math.min(spatial.width - 1, bounds.right) / spatial.tileSize));
  const ty1 = Math.min(maxTileY, Math.floor(Math.min(spatial.height - 1, bounds.bottom) / spatial.tileSize));

  for (let ty = ty0; ty <= ty1; ty += 1) {
    for (let tx = tx0; tx <= tx1; tx += 1) {
      const key = tileKey(tx, ty);
      if (spatial.tileMap.has(key)) keys.push(key);
    }
  }
  return keys;
}

function visibleTileKeys(spatial, bounds) {
  const keys = new Set();
  for (const panel of spatial.panels) {
    if (!rectsIntersect(panel.bounds, bounds)) continue;
    const originalBounds = originalBoundsForPanel(panel, bounds);
    for (const key of tileKeysForOriginalBounds(spatial, originalBounds)) {
      keys.add(key);
    }
  }
  return Array.from(keys);
}

async function loadTile(spatial, key) {
  const tile = spatial.tileMap.get(key);
  if (!tile || spatial.loadedTiles.has(key) || spatial.loadingTiles.has(key) || spatial.failedTiles.has(key)) {
    return;
  }

  spatial.loadingTiles.add(key);
  try {
    const response = await fetch(tile.url);
    if (!response.ok) throw new Error(`Failed to load contour tile ${tile.url}`);
    const payload = await response.json();
    spatial.loadedTiles.set(key, payload);
  } catch (error) {
    spatial.failedTiles.add(key);
    console.error(error);
    setStatus(error.message);
  } finally {
    spatial.loadingTiles.delete(key);
    requestDraw();
  }
}

function ensureTiles(spatial, keys) {
  let missing = 0;
  for (const key of keys) {
    if (!spatial.loadedTiles.has(key) && !spatial.failedTiles.has(key)) {
      missing += 1;
      loadTile(spatial, key);
    }
  }
  return missing;
}

function translatedBBox(cell, panel) {
  return [
    panel.x + (cell.bbox[0] - panel.bbox[0]) * panel.scaleX,
    panel.y + (cell.bbox[1] - panel.bbox[1]) * panel.scaleY,
    panel.x + (cell.bbox[2] - panel.bbox[0] + 1) * panel.scaleX,
    panel.y + (cell.bbox[3] - panel.bbox[1] + 1) * panel.scaleY,
  ];
}

function cellFillColor(cell, expression, clusterMap, tissueMap) {
  if (state.displayMode === "cluster") {
    const clusterId = clusterMap.get(cell.id);
    if (clusterId === undefined) return CATEGORY_MUTED;
    return maybeMutedCategoryColor(categoryColor("cluster", clusterId), clusterId, state.selectedCluster);
  }

  if (state.displayMode === "tissue") {
    const tissueId = tissueMap.get(cell.id);
    if (tissueId === undefined) return CATEGORY_MUTED;
    return maybeMutedCategoryColor(categoryColor("tissue", tissueId), tissueId, state.selectedTissue);
  }

  const value = expression.map.get(cell.id) || 0;
  return colorForValue(value, state.expressionRange);
}

function drawPanels(spatial, bounds, scale) {
  ctx.save();
  ctx.lineWidth = 1 / scale;

  for (const panel of spatial.panels) {
    if (!rectsIntersect(panel.bounds, bounds)) continue;

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(panel.x, panel.y, panel.width, panel.height);
    ctx.strokeStyle = "#b8c1cc";
    ctx.strokeRect(panel.x, panel.y, panel.width, panel.height);
  }

  ctx.restore();
}

function drawPanelLabels(spatial, bounds, scale, viewX, viewY, rect) {
  ctx.save();
  ctx.font = "13px Arial, \"Noto Sans SC\", sans-serif";
  ctx.textBaseline = "bottom";

  for (const panel of spatial.panels) {
    if (!rectsIntersect(panel.bounds, bounds)) continue;

    const screenLeft = viewX + panel.x * scale;
    const screenTop = viewY + panel.y * scale;
    const screenRight = viewX + (panel.x + panel.width) * scale;
    if (screenRight < 0 || screenLeft > rect.width || screenTop < 0 || screenTop > rect.height) {
      continue;
    }

    ctx.fillStyle = "rgba(255,255,255,0.88)";
    ctx.fillRect(screenLeft, Math.max(0, screenTop - 23), 170, 20);
    ctx.fillStyle = "#253044";
    ctx.fillText(
      `${panel.label} · ${panel.assignedCellCount.toLocaleString()} cells`,
      screenLeft + 6,
      Math.max(15, screenTop - 7)
    );
  }

  ctx.restore();
}

function drawCells(spatial, expression, clusterMap, tissueMap, keys, bounds, scale) {
  const drawn = new Set();
  let drawnCells = 0;
  const outlinePx = outlineScreenWidth(spatial, scale);

  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  for (const key of keys) {
    const tile = spatial.loadedTiles.get(key);
    if (!tile) continue;

    for (const cell of tile.cells || []) {
      if (drawn.has(cell.id)) continue;
      const panel = spatial.cellToPanel.get(cell.id);
      if (!panel) continue;

      const layoutBBox = translatedBBox(cell, panel);
      if (!bboxIntersects(layoutBBox, bounds)) continue;
      drawn.add(cell.id);

      const rgb = cellFillColor(cell, expression, clusterMap, tissueMap);
      const path = getCellPath(spatial, cell);

      ctx.save();
      ctx.translate(panel.x, panel.y);
      ctx.scale(panel.scaleX, panel.scaleY);
      ctx.translate(-panel.bbox[0], -panel.bbox[1]);
      ctx.lineWidth = outlinePx / (scale * Math.max(panel.scaleX, panel.scaleY));
      ctx.fillStyle = rgbCss(rgb);
      ctx.strokeStyle = rgbCss(invertRgb(rgb));
      ctx.fill(path);
      ctx.stroke(path);
      ctx.restore();

      drawnCells += 1;
    }
  }

  return drawnCells;
}

function draw() {
  const spatial = currentSpatial();
  const rect = canvas.getBoundingClientRect();
  ctx.setTransform(state.devicePixelRatio, 0, 0, state.devicePixelRatio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = "#eef1f4";
  ctx.fillRect(0, 0, rect.width, rect.height);

  if (!spatial) return;

  const expression = currentExpression();
  const clusterMap = currentClusterMap();
  const tissueMap = currentTissueMap();
  const bounds = visibleBounds(rect);
  const keys = visibleTileKeys(spatial, bounds);
  const missingTiles = ensureTiles(spatial, keys);
  const { scale, x, y } = state.view;

  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);

  drawPanels(spatial, bounds, scale);
  const drawnCells = drawCells(spatial, expression, clusterMap, tissueMap, keys, bounds, scale);

  ctx.restore();
  drawPanelLabels(spatial, bounds, scale, x, y, rect);

  updateScaleText();
  if (missingTiles > 0) {
    setStatus(`Loading ${spatial.sample} contour tiles ${spatial.loadedTiles.size}/${spatial.tileCount}...`);
  } else if (state.displayMode === "cluster") {
    setStatus(`Showing ${selectedClusterLabel()}; ${drawnCells.toLocaleString()} cells drawn in the current view`);
  } else if (state.displayMode === "tissue") {
    setStatus(`Showing ${selectedTissueLabel()}; ${drawnCells.toLocaleString()} cells drawn in the current view`);
  } else if (state.currentGene) {
    setStatus(`Loaded ${state.currentGene}; ${drawnCells.toLocaleString()} cells drawn in the current view`);
  }
}

function ellipsizeText(context, text, maxWidth) {
  if (context.measureText(text).width <= maxWidth) return text;
  const ellipsis = "...";
  let low = 0;
  let high = text.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    const candidate = `${text.slice(0, mid)}${ellipsis}`;
    if (context.measureText(candidate).width <= maxWidth) {
      low = mid;
    } else {
      high = mid - 1;
    }
  }
  return `${text.slice(0, low)}${ellipsis}`;
}

function dotRadius(pctExpr, pctRange) {
  const minRadius = 3;
  const maxRadius = 15;
  if (!Number.isFinite(pctExpr)) return minRadius;
  if (!pctRange || pctRange.max <= pctRange.min) return (minRadius + maxRadius) / 2;
  const t = Math.max(0, Math.min(1, (pctExpr - pctRange.min) / (pctRange.max - pctRange.min)));
  return minRadius + t * (maxRadius - minRadius);
}

function drawDotplotMessage(rect, message) {
  dotCtx.fillStyle = "#ffffff";
  dotCtx.fillRect(0, 0, rect.width, rect.height);
  dotCtx.fillStyle = "#667085";
  dotCtx.font = "13px Arial, \"Noto Sans SC\", sans-serif";
  dotCtx.textAlign = "center";
  dotCtx.textBaseline = "middle";
  dotCtx.fillText(message, rect.width / 2, rect.height / 2);
}

function scaledColorForValue(value, range) {
  const min = Number.isFinite(range.vmin) ? range.vmin : -2.5;
  const max = Number.isFinite(range.vmax) ? range.vmax : 2.5;
  const t = max > min ? Math.max(0, Math.min(1, (value - min) / (max - min))) : 0.5;
  const scaled = t * (REDS.length - 1);
  const idx = Math.min(REDS.length - 2, Math.floor(scaled));
  const local = scaled - idx;
  const a = REDS[idx];
  const b = REDS[idx + 1];
  return a.map((component, i) => Math.round(component + (b[i] - component) * local));
}

function drawDotplotLegend(colorRange, pctRange, right, centerY) {
  const legendWidth = 116;
  const legendHeight = 10;
  const x = Math.max(170, right - legendWidth);
  const y = Math.max(28, centerY - 36);
  const gradient = dotCtx.createLinearGradient(x, 0, x + legendWidth, 0);

  for (let i = 0; i < REDS.length; i += 1) {
    gradient.addColorStop(i / (REDS.length - 1), rgbCss(REDS[i]));
  }

  dotCtx.fillStyle = "#253044";
  dotCtx.font = "11px Arial, \"Noto Sans SC\", sans-serif";
  dotCtx.textAlign = "left";
  dotCtx.textBaseline = "bottom";
  dotCtx.fillText("Scaled avg expr", x, y - 4);
  dotCtx.fillStyle = gradient;
  dotCtx.fillRect(x, y, legendWidth, legendHeight);
  dotCtx.strokeStyle = "#d0b4aa";
  dotCtx.strokeRect(x, y, legendWidth, legendHeight);
  dotCtx.fillStyle = "#667085";
  dotCtx.textBaseline = "top";
  dotCtx.fillText(formatNumber(colorRange.vmin, 1), x, y + legendHeight + 3);
  dotCtx.textAlign = "right";
  dotCtx.fillText(formatNumber(colorRange.vmax, 1), x + legendWidth, y + legendHeight + 3);

  const sizeY = y + 64;
  const sizes = pctRange.max > pctRange.min
    ? [
        pctRange.min,
        pctRange.min + (pctRange.max - pctRange.min) / 2,
        pctRange.max,
      ]
    : [pctRange.min];
  dotCtx.textAlign = "left";
  dotCtx.textBaseline = "middle";
  dotCtx.fillStyle = "#253044";
  dotCtx.fillText("% cells", x, sizeY - 22);
  for (const [index, pct] of sizes.entries()) {
    const cx = sizes.length === 1 ? x + 56 : x + 12 + index * 40;
    const radius = dotRadius(pct, pctRange);
    dotCtx.beginPath();
    dotCtx.arc(cx, sizeY, radius, 0, Math.PI * 2);
    dotCtx.fillStyle = "#fcbba1";
    dotCtx.fill();
    dotCtx.strokeStyle = "#8a1d18";
    dotCtx.stroke();
    dotCtx.fillStyle = "#667085";
    dotCtx.textAlign = "center";
    dotCtx.fillText(formatNumber(pct, 1), cx, sizeY + 21);
  }
}

function clusterDisplayName(cluster) {
  const name = String(cluster.name || "").trim();
  return name || `Cluster ${cluster.label || cluster.id}`;
}

function clusterNameForId(clusterId) {
  return state.clusterMeta.names.get(String(clusterId)) || "";
}

function clusterOptionLabel(cluster) {
  const id = String(cluster.label || cluster.id);
  const name = clusterNameForId(cluster.id);
  return name ? `${id} - ${name}` : id;
}

function drawDotplot() {
  const rect = dotplotCanvas.getBoundingClientRect();
  dotCtx.setTransform(state.devicePixelRatio, 0, 0, state.devicePixelRatio, 0, 0);
  dotCtx.clearRect(0, 0, rect.width, rect.height);
  state.dotplotPoints = [];

  if (state.displayMode !== "gene") {
    return;
  }

  if (state.dotplot.loading) {
    drawDotplotMessage(rect, "Loading cluster dotplot...");
    return;
  }
  if (state.dotplot.error) {
    drawDotplotMessage(rect, state.dotplot.error);
    return;
  }

  const payload = state.dotplot.payload;
  const clusters = payload ? payload.clusters || [] : [];
  if (!clusters.length) {
    drawDotplotMessage(rect, "No dotplot data");
    return;
  }

  dotCtx.fillStyle = "#ffffff";
  dotCtx.fillRect(0, 0, rect.width, rect.height);

  const compact = rect.width < 680;
  const left = compact ? 118 : 158;
  const right = compact ? rect.width - 24 : rect.width - 176;
  const titleY = 20;
  const plotWidth = Math.max(1, right - left);
  const centerY = Math.min(rect.height - 42, titleY + 44);
  const band = plotWidth / clusters.length;
  const scaledValues = clusters.map((cluster) => Number(cluster.avgExprScaled)).filter(Number.isFinite);
  const pctValues = clusters.map((cluster) => Number(cluster.pctExpr)).filter(Number.isFinite);
  const colorRange = scaledValues.length
    ? { vmin: Math.min(...scaledValues), vmax: Math.max(...scaledValues) }
    : { vmin: 0, vmax: Math.max(0, ...clusters.map((cluster) => Number(cluster.avgExpr) || 0)) };
  const pctRange = pctValues.length
    ? { min: Math.min(...pctValues), max: Math.max(...pctValues) }
    : { min: 0, max: 100 };

  dotCtx.fillStyle = "#253044";
  dotCtx.font = "700 18px Arial, \"Noto Sans SC\", sans-serif";
  dotCtx.textAlign = "left";
  dotCtx.textBaseline = "middle";
  dotCtx.fillText("Seurat Clusters", left, titleY);

  dotCtx.strokeStyle = "#d7dde5";
  dotCtx.lineWidth = 1;
  dotCtx.beginPath();
  dotCtx.moveTo(left, centerY);
  dotCtx.lineTo(right, centerY);
  dotCtx.stroke();

  dotCtx.font = "700 14px Arial, \"Noto Sans SC\", sans-serif";
  dotCtx.textAlign = "right";
  dotCtx.textBaseline = "middle";
  dotCtx.fillStyle = "#253044";
  dotCtx.fillText(ellipsizeText(dotCtx, payload.gene || state.currentGene || "-", left - 28), left - 14, centerY);

  for (const [index, cluster] of clusters.entries()) {
    const x = left + band * (index + 0.5);
    const avgExpr = Number(cluster.avgExpr) || 0;
    const avgExprScaled = Number(cluster.avgExprScaled);
    const pctExpr = Number(cluster.pctExpr) || 0;
    const radius = dotRadius(pctExpr, pctRange);
    const rgb = Number.isFinite(avgExprScaled)
      ? scaledColorForValue(avgExprScaled, colorRange)
      : colorForValue(avgExpr, colorRange);

    dotCtx.beginPath();
    dotCtx.arc(x, centerY, radius, 0, Math.PI * 2);
    dotCtx.fillStyle = rgbCss(rgb);
    dotCtx.fill();
    dotCtx.strokeStyle = "#7a1c16";
    dotCtx.lineWidth = 0.8;
    dotCtx.stroke();
    state.dotplotPoints.push({
      x,
      y: centerY,
      radius: Math.max(radius, 8),
      cluster,
      avgExpr,
      pctExpr,
    });

    if (!compact) {
      dotCtx.fillStyle = "#415064";
      dotCtx.font = "13px Arial, \"Noto Sans SC\", sans-serif";
      dotCtx.textAlign = "center";
      dotCtx.textBaseline = "top";
      dotCtx.fillText(String(cluster.label || cluster.id), x, centerY + 22);
    }
  }

  if (compact) {
    for (const [index, cluster] of clusters.entries()) {
      const x = left + band * (index + 0.5);
      dotCtx.save();
      dotCtx.translate(x, centerY + 30);
      dotCtx.rotate(-Math.PI / 4);
      dotCtx.fillStyle = "#415064";
      dotCtx.font = "12px Arial, \"Noto Sans SC\", sans-serif";
      dotCtx.textAlign = "right";
      dotCtx.textBaseline = "middle";
      dotCtx.fillText(String(cluster.label || cluster.id), 0, 0);
      dotCtx.restore();
    }
  }

  if (!compact) {
    drawDotplotLegend(colorRange, pctRange, rect.width - 26, centerY);
  }
}

function hideDotplotTooltip() {
  if (!els.dotplotTooltip) return;
  els.dotplotTooltip.style.display = "none";
}

function showDotplotTooltip(point, clientX, clientY) {
  if (!els.dotplotTooltip) return;
  const stageRect = dotplotCanvas.parentElement.getBoundingClientRect();
  const clusterId = String(point.cluster.label || point.cluster.id);
  els.dotplotTooltip.innerHTML = (
    `<strong>Cluster ${escapeHtml(clusterId)}</strong>` +
    `${escapeHtml(clusterDisplayName(point.cluster))}<br>` +
    `Avg expr: ${escapeHtml(formatNumber(point.avgExpr, 3))}<br>` +
    `% cells: ${escapeHtml(formatNumber(point.pctExpr, 1))}`
  );
  els.dotplotTooltip.style.display = "block";
  const tooltipRect = els.dotplotTooltip.getBoundingClientRect();
  const rawLeft = clientX - stageRect.left + 12;
  const rawTop = clientY - stageRect.top - tooltipRect.height - 10;
  const left = Math.max(8, Math.min(stageRect.width - tooltipRect.width - 8, rawLeft));
  const top = rawTop >= 8 ? rawTop : clientY - stageRect.top + 12;
  els.dotplotTooltip.style.left = `${left}px`;
  els.dotplotTooltip.style.top = `${Math.min(stageRect.height - tooltipRect.height - 8, top)}px`;
}

function handleDotplotPointerMove(event) {
  if (state.displayMode !== "gene" || !state.dotplotPoints.length) {
    hideDotplotTooltip();
    return;
  }
  const rect = dotplotCanvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const hit = state.dotplotPoints.find((point) => {
    const dx = x - point.x;
    const dy = y - point.y;
    return dx * dx + dy * dy <= point.radius * point.radius;
  });
  if (hit) {
    dotplotCanvas.style.cursor = "default";
    showDotplotTooltip(hit, event.clientX, event.clientY);
  } else {
    dotplotCanvas.style.cursor = "";
    hideDotplotTooltip();
  }
}

async function loadSpatial() {
  if (typeof Path2D === "undefined") {
    throw new Error("This browser does not support Path2D, so cell contours cannot be rendered");
  }
  const dataset = state.currentDataset;
  if (!dataset) throw new Error("No dataset selected");

  const sampleLabels = (dataset.samples || []).map(sampleDisplayLabel).join("/");
  setStatus(`Loading ${sampleLabels} contours, replicate indexes, cluster data, and tissue data...`);
  const tissueRequest = fetch(apiUrl("/api/tissues")).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      return { error: payload.error || "Tissue data unavailable" };
    }
    return payload;
  });
  const colorsRequest = fetch(apiUrl("/api/colors")).then(async (response) => {
    if (!response.ok) return { clusters: {}, tissues: {} };
    return response.json();
  }).catch(() => ({ clusters: {}, tissues: {} }));
  const clusterNamesRequest = fetch(apiUrl("/api/cluster-names")).then(async (response) => {
    if (!response.ok) return { names: {} };
    return response.json();
  }).catch(() => ({ names: {} }));
  const manifestRequests = (dataset.samples || []).map((sample) => (
    fetch(`${(sample.contoursPath || `${dataset.dataPath}/contours/${sample.id}`).replace(/\/$/, "")}/manifest.json`).then(async (response) => {
      if (!response.ok) throw new Error(`Missing ${sampleDisplayLabel(sample)} contour data; run web_viewer/export_contours.py first`);
      return normalizeManifestUrls(await response.json(), dataset.id);
    })
  ));
  const [manifests, replicates, clusters, tissues, colors, clusterNames] = await Promise.all([
    Promise.all(manifestRequests),
    fetch(apiUrl("/api/replicates")).then((response) => {
      if (!response.ok) throw new Error("Missing replicate data; run web_viewer/export_replicates.py first");
      return response.json();
    }),
    fetch(`${dataset.dataPath}/clusters.json`).then((response) => {
      if (!response.ok) throw new Error("Missing cluster data; run web_viewer/export_clusters.py first");
      return response.json();
    }),
    tissueRequest,
    colorsRequest,
    clusterNamesRequest,
  ]);

  state.samples = {};
  state.expressions = {};
  for (const manifest of manifests) {
    const sampleId = manifest.sample;
    const replicatePayload = replicates.samples ? replicates.samples[sampleId] : null;
    if (!replicatePayload) {
      throw new Error(`Missing replicate metadata for ${sampleId}`);
    }
    state.samples[sampleId] = prepareSpatial(manifest, replicatePayload);
  }
  loadCategoryColors(colors);
  loadClusterMeta(clusters, clusterNames);
  loadTissueMeta(tissues);
  fitView();
  updateStats();
  requestDraw();
}

function loadCategoryColors(payload) {
  const clusters = new Map();
  const tissues = new Map();
  for (const [id, color] of Object.entries(payload.clusters || {})) {
    const rgb = hexToRgb(color);
    if (rgb) clusters.set(String(id), rgb);
  }
  for (const [id, color] of Object.entries(payload.tissues || {})) {
    const rgb = hexToRgb(color);
    if (rgb) tissues.set(String(id), rgb);
  }
  state.categoryColors = { clusters, tissues };
}

function tissueMenuOrder() {
  const dataset = state.currentDataset;
  const datasetId = String(dataset ? dataset.id : "").toLowerCase();
  const datasetLabel = String(dataset ? dataset.label : "").toLowerCase();
  if (datasetId === "s1_stem" || datasetLabel === "s1 + stem") {
    return STEM_TISSUE_MENU_ORDER;
  }
  return DEFAULT_TISSUE_MENU_ORDER;
}

function tissueMenuRank(tissue) {
  const order = tissueMenuOrder();
  const id = String(tissue.id || "").toLowerCase();
  const label = String(tissue.label || "").toLowerCase();
  const idIndex = order.indexOf(id);
  if (idIndex >= 0) return idIndex;
  const labelIndex = order.indexOf(label);
  if (labelIndex >= 0) return labelIndex;
  return order.length;
}

function loadClusterMeta(payload, namesPayload = {}) {
  const maps = {};
  for (const [sample, samplePayload] of Object.entries(payload.samples || {})) {
    const map = new Map();
    for (const [cellId, clusterId] of samplePayload.cells || []) {
      map.set(Number(cellId), String(clusterId));
    }
    maps[sample] = map;
  }

  const names = new Map();
  for (const [clusterId, name] of Object.entries(namesPayload.names || {})) {
    names.set(String(clusterId), String(name));
  }

  state.clusterMeta = {
    clusters: payload.clusters || [],
    maps,
    names,
  };

  els.clusterSelect.innerHTML = "";
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All clusters";
  els.clusterSelect.appendChild(allOption);
  for (const cluster of state.clusterMeta.clusters) {
    const option = document.createElement("option");
    option.value = String(cluster.id);
    option.textContent = clusterOptionLabel(cluster);
    els.clusterSelect.appendChild(option);
  }

  const hasSelectedCluster = state.clusterMeta.clusters.some(
    (cluster) => String(cluster.id) === state.selectedCluster
  );
  state.selectedCluster = hasSelectedCluster ? state.selectedCluster : "";
  els.clusterSelect.value = state.selectedCluster;
  els.clusterSelect.disabled = !state.clusterMeta.clusters.length;
}

function loadTissueMeta(payload) {
  els.tissueSelect.innerHTML = "";

  if (payload && payload.error) {
    state.tissueMeta = { tissues: [], maps: {}, error: payload.error };
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Tissue unavailable";
    els.tissueSelect.appendChild(option);
    state.selectedTissue = "";
    els.tissueSelect.disabled = true;
    return;
  }

  const maps = {};
  for (const [sample, samplePayload] of Object.entries(payload.samples || {})) {
    const map = new Map();
    for (const [cellId, tissueId] of samplePayload.cells || []) {
      map.set(Number(cellId), String(tissueId));
    }
    maps[sample] = map;
  }

  state.tissueMeta = {
    tissues: payload.tissues || [],
    maps,
    error: "",
  };

  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All tissues";
  els.tissueSelect.appendChild(allOption);
  const orderedTissues = state.tissueMeta.tissues
    .map((tissue, index) => ({ tissue, index }))
    .sort((a, b) => tissueMenuRank(a.tissue) - tissueMenuRank(b.tissue) || a.index - b.index)
    .map((item) => item.tissue);
  for (const tissue of orderedTissues) {
    const option = document.createElement("option");
    option.value = String(tissue.id);
    option.textContent = tissue.label || tissue.id;
    els.tissueSelect.appendChild(option);
  }

  const hasSelectedTissue = state.tissueMeta.tissues.some(
    (tissue) => String(tissue.id) === state.selectedTissue
  );
  state.selectedTissue = hasSelectedTissue ? state.selectedTissue : "";
  els.tissueSelect.value = state.selectedTissue;
  els.tissueSelect.disabled = !state.tissueMeta.tissues.length;
}

async function loadDatasetCatalog() {
  const response = await fetch(`${API_BASE}/datasets`);
  if (!response.ok) throw new Error("Dataset configuration unavailable");
  const payload = await response.json();
  state.datasetCatalog = payload.datasets || [];
  if (!state.datasetCatalog.length) throw new Error("No datasets configured");

  els.datasetSelect.innerHTML = "";
  for (const dataset of state.datasetCatalog) {
    const option = document.createElement("option");
    option.value = dataset.id;
    option.textContent = datasetDisplayLabel(dataset);
    els.datasetSelect.appendChild(option);
  }

  const params = new URLSearchParams(window.location.search);
  const requestedId = params.get("dataset");
  const selected = state.datasetCatalog.find((dataset) => dataset.id === requestedId)
    || state.datasetCatalog.find((dataset) => dataset.id === payload.defaultDataset)
    || state.datasetCatalog[0];
  setDataset(selected.id, { updateUrl: false, resetGene: true });
}

function setDataset(datasetId, options = {}) {
  const dataset = state.datasetCatalog.find((item) => item.id === datasetId);
  if (!dataset) return;

  state.currentDataset = dataset;
  state.samples = {};
  state.expressions = {};
  state.clusterMeta = { clusters: [], maps: {}, names: new Map() };
  state.tissueMeta = { tissues: [], maps: {}, error: "" };
  state.selectedCluster = "";
  state.selectedTissue = "";
  state.currentGene = "";
  state.expressionRange = { vmin: 0, vmax: 0 };
  state.dotplot = { payload: null, error: "", loading: false };
  state.currentSample = dataset.defaultSample || ((dataset.samples || [])[0] || {}).id || "";
  els.datasetSelect.value = dataset.id;

  if (options.resetGene !== false && dataset.defaultGene) {
    els.input.value = dataset.defaultGene;
  }

  renderSampleButtons();
  if (options.updateUrl !== false) {
    const url = new URL(window.location.href);
    url.searchParams.set("dataset", dataset.id);
    window.history.replaceState({}, "", url);
  }
}

function renderSampleButtons() {
  els.sampleToggle.innerHTML = "";
  const samples = state.currentDataset ? state.currentDataset.samples || [] : [];
  for (const sample of samples) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.sample = sample.id;
    button.textContent = sampleDisplayLabel(sample);
    button.classList.toggle("active", sample.id === state.currentSample);
    button.addEventListener("click", () => setSample(sample.id));
    els.sampleToggle.appendChild(button);
  }
}

async function reloadCurrentDataset() {
  resizeCanvas();
  await loadSpatial();
  await loadGenes();
  await queryGene(els.input.value);
}

async function loadGenes() {
  const response = await fetch(apiUrl("/api/genes"));
  if (!response.ok) return;
  const payload = await response.json();
  els.geneList.innerHTML = "";
  for (const gene of payload.genes.slice(0, 2000)) {
    const option = document.createElement("option");
    option.value = gene;
    els.geneList.appendChild(option);
  }
}

function unpackExpression(samplePayload) {
  const values = samplePayload.values || [];
  const map = new Map();
  let min = Infinity;
  let max = 0;
  for (const [cellId, value] of values) {
    const oldValue = map.get(cellId);
    map.set(cellId, oldValue === undefined ? value : Math.max(oldValue, value));
    if (value > 0) {
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
  }
  return {
    map,
    min: Number.isFinite(min) ? min : 0,
    max,
    nonzero: samplePayload.nonzero || values.length,
  };
}

async function queryGene(gene) {
  gene = gene.trim();
  if (!gene) return;

  state.dotplot = { payload: null, error: "", loading: true };
  setDisplayMode("gene");
  setStatus(`Querying ${gene} expression and cluster dotplot...`);

  const [geneResponse, dotplotResponse] = await Promise.all([
    fetch(apiUrl("/api/gene", { gene })),
    fetch(apiUrl("/api/dotplot", { gene })),
  ]);
  const payload = await geneResponse.json();
  const dotplotPayload = await dotplotResponse.json();

  state.dotplot.loading = false;
  if (!geneResponse.ok) {
    state.dotplot = { payload: null, error: "Waiting for a valid gene", loading: false };
    requestDotplotDraw();
    setStatus(payload.error || "Query failed");
    return;
  }

  state.currentGene = gene;
  state.expressionRange = payload.range || { vmin: 0, vmax: 0 };
  state.expressions = {};
  for (const sample of state.currentDataset.samples || []) {
    const samplePayload = payload.samples ? payload.samples[sample.id] : null;
    state.expressions[sample.id] = samplePayload
      ? unpackExpression(samplePayload)
      : { map: new Map(), min: 0, max: 0, nonzero: 0 };
  }
  if (dotplotResponse.ok) {
    state.dotplot = { payload: dotplotPayload, error: "", loading: false };
  } else {
    state.dotplot = {
      payload: null,
      error: dotplotPayload.error || "Dotplot data unavailable",
      loading: false,
    };
  }
  updateStats();
  requestDraw();
  requestDotplotDraw();
}

function updateStats() {
  const { vmin, vmax } = state.expressionRange;
  const highlightMode = activeHighlightMode();
  els.legend.classList.toggle("is-hidden", highlightMode !== "expression");
  els.legendMax.textContent = vmax ? formatNumber(vmax) : "max";
  els.legendMin.textContent = Number.isFinite(vmin) ? formatNumber(vmin) : "0";
  updateScaleText();
}

function updateModeControls() {
  els.modeSelect.value = state.displayMode;
  els.modePanels.forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.modePanel !== state.displayMode);
  });
  els.viewer.classList.toggle("dotplot-hidden", state.displayMode !== "gene");
}

function setSample(sample) {
  state.currentSample = sample;
  els.sampleToggle.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.sample === sample);
  });
  fitView();
  updateStats();
  requestDraw();
}

function setDisplayMode(mode) {
  state.displayMode = ["gene", "cluster", "tissue"].includes(mode) ? mode : "gene";
  hideDotplotTooltip();
  updateModeControls();
  resizeCanvas();
  fitView();
  updateStats();
  requestDraw();
  requestDotplotDraw();
}

function setSelectedCluster(clusterId) {
  state.selectedCluster = clusterId || "";
  els.clusterSelect.value = state.selectedCluster;
  setDisplayMode("cluster");
}

function setSelectedTissue(tissueId) {
  state.selectedTissue = tissueId || "";
  els.tissueSelect.value = state.selectedTissue;
  setDisplayMode("tissue");
}

function zoomAt(factor, centerX = canvas.clientWidth / 2, centerY = canvas.clientHeight / 2) {
  const beforeX = (centerX - state.view.x) / state.view.scale;
  const beforeY = (centerY - state.view.y) / state.view.scale;
  state.view.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, state.view.scale * factor));
  state.view.x = centerX - beforeX * state.view.scale;
  state.view.y = centerY - beforeY * state.view.scale;
  updateScaleText();
  requestDraw();
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  queryGene(els.input.value);
});

els.datasetSelect.addEventListener("change", async () => {
  setDataset(els.datasetSelect.value, { updateUrl: true, resetGene: true });
  try {
    await reloadCurrentDataset();
  } catch (error) {
    setStatus(error.message);
    console.error(error);
  }
});

els.modeSelect.addEventListener("change", () => {
  setDisplayMode(els.modeSelect.value);
});

els.clusterSelect.addEventListener("change", () => {
  setSelectedCluster(els.clusterSelect.value);
});

els.tissueSelect.addEventListener("change", () => {
  setSelectedTissue(els.tissueSelect.value);
});

document.getElementById("zoomIn").addEventListener("click", () => zoomAt(1.25));
document.getElementById("zoomOut").addEventListener("click", () => zoomAt(0.8));
document.getElementById("resetView").addEventListener("click", () => {
  fitView();
  requestDraw();
});

canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const factor = event.deltaY < 0 ? 1.15 : 0.87;
  zoomAt(factor, event.clientX - rect.left, event.clientY - rect.top);
}, { passive: false });

canvas.addEventListener("pointerdown", (event) => {
  state.dragging = true;
  state.lastPointer = { x: event.clientX, y: event.clientY };
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", (event) => {
  if (!state.dragging || !state.lastPointer) return;
  state.view.x += event.clientX - state.lastPointer.x;
  state.view.y += event.clientY - state.lastPointer.y;
  state.lastPointer = { x: event.clientX, y: event.clientY };
  requestDraw();
});

canvas.addEventListener("pointerup", (event) => {
  state.dragging = false;
  state.lastPointer = null;
  canvas.releasePointerCapture(event.pointerId);
});

dotplotCanvas.addEventListener("pointermove", handleDotplotPointerMove);
dotplotCanvas.addEventListener("pointerleave", () => {
  dotplotCanvas.style.cursor = "";
  hideDotplotTooltip();
});

window.addEventListener("resize", () => {
  hideDotplotTooltip();
  resizeCanvas();
  fitView();
  requestDraw();
  requestDotplotDraw();
});

(async function init() {
  try {
    await loadDatasetCatalog();
    await reloadCurrentDataset();
  } catch (error) {
    setStatus(error.message);
    console.error(error);
  }
})();

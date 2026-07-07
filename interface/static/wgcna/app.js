const API_BASE = '/api/wgcna';
const NETWORKS = ['leaf', 'stem', 'root', 'reproductive', 'tuberization'];
const NETWORK_LABELS = {
  leaf: 'Leaf',
  stem: 'Stem',
  root: 'Root',
  reproductive: 'Reproductive',
  tuberization: 'Tuberization',
};
const NETWORK_COLORS = {
  leaf: '#15803d',
  stem: '#0f766e',
  root: '#7c3aed',
  reproductive: '#c2410c',
  tuberization: '#0369a1',
};
const NETWORK_NODE_FILLS = {
  leaf: '#22c55e',
  stem: '#14b8a6',
  root: '#8b5cf6',
  reproductive: '#f97316',
  tuberization: '#0ea5e9',
};
const NETWORK_NODE_SOFT_FILLS = {
  leaf: '#bbf7d0',
  stem: '#99f6e4',
  root: '#ddd6fe',
  reproductive: '#fed7aa',
  tuberization: '#bae6fd',
};
const NETWORK_EDGE_COLORS = {
  leaf: '#15803d',
  stem: '#0f766e',
  root: '#7c3aed',
  reproductive: '#c2410c',
  tuberization: '#0369a1',
};
const DETAIL_TOOLTIPS = {
  Type: 'Selected element type.',
  Gene: 'Original gene identifier.',
  Network: 'WGCNA network containing this item.',
  Variance: 'Expression variance used in WGCNA filtering.',
  Source: 'Source node ID in the displayed graph.',
  Target: 'Target node ID in the displayed graph.',
  'Gene A': 'First gene in this co-expression edge.',
  'Gene B': 'Second gene in this co-expression edge.',
  TOM: 'Topological overlap similarity for this edge.',
  Rank: 'Neighbor rank by TOM for the query gene.',
};

const state = {
  cy: null,
  lastPayload: null,
  loading: false,
};

const dom = {
  queryForm: document.getElementById('query-form'),
  genesInput: document.getElementById('genes-input'),
  topNSelect: document.getElementById('top-n-select'),
  tomMinInput: document.getElementById('tom-min-input'),
  sameModuleOnly: document.getElementById('same-module-only'),
  neighborEdges: document.getElementById('neighbor-edges'),
  crossNetwork: document.getElementById('cross-network'),
  sharedEdges: document.getElementById('shared-edges'),
  submitButton: document.getElementById('submit-button'),
  resetViewButton: document.getElementById('reset-view-button'),
  graphTitle: document.getElementById('graph-title'),
  graphSummary: document.getElementById('graph-summary'),
  graphEmpty: document.getElementById('graph-empty'),
  selectionDetail: document.getElementById('selection-detail'),
  downloadPng: document.getElementById('download-png'),
  downloadSvg: document.getElementById('download-svg'),
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

const selectedNetworks = () => Array.from(
  document.querySelectorAll('input[name="network"]:checked'),
).map((input) => input.value);

const buildQueryUrl = () => {
  const params = new URLSearchParams();
  params.set('genes', dom.genesInput.value.trim());
  const networks = selectedNetworks();
  params.set('networks', networks.length === NETWORKS.length ? 'all' : networks.join(','));
  params.set('top_n', dom.topNSelect.value || '50');
  const tomMin = String(dom.tomMinInput.value || '').trim();
  if (tomMin) params.set('tom_min', tomMin);
  params.set('same_module_only', String(dom.sameModuleOnly.checked));
  params.set('include_neighbor_edges', String(dom.neighborEdges.checked));
  params.set('include_cross_network', String(dom.crossNetwork.checked));
  params.set('include_module_overlaps', 'false');
  params.set('include_shared_edges', String(dom.sharedEdges.checked));
  params.set('max_total_edges', '3000');
  return `${API_BASE}/coexpression?${params.toString()}`;
};

const shortGeneLabel = (geneId) => {
  const text = String(geneId || '');
  const match = text.match(/chr([0-9A-Za-z]+)G([0-9]+)$/);
  if (match) return `chr${match[1]}G${match[2]}`;
  return text.length > 18 ? `${text.slice(0, 9)}…${text.slice(-6)}` : text;
};

const nodeColor = (node) => {
  const networkId = node.data('network_id');
  if (node.data('is_query_gene')) {
    return NETWORK_NODE_FILLS[networkId] || '#475569';
  }
  return NETWORK_NODE_SOFT_FILLS[networkId] || '#e2e8f0';
};

const nodeBorderColor = (node) => (
  NETWORK_COLORS[node.data('network_id')] || '#334155'
);

const edgeColor = (edge) => {
  if (edge.data('edge_type') === 'same_gene') return '#64748b';
  return NETWORK_EDGE_COLORS[edge.data('network_id')] || '#64748b';
};

const edgeWidth = (edge) => {
  if (edge.data('edge_type') === 'same_gene') return 0.65;
  const rank = Math.max(1, Number(edge.data('rank') || 100));
  const base = 0.35 + (1.35 / Math.sqrt(rank));
  return Math.min(1.7, base + (edge.data('shared_coexpression') ? 0.15 : 0));
};

const setupCytoscape = () => {
  state.cy = cytoscape({
    container: document.getElementById('cy'),
    elements: [],
    wheelSensitivity: 0.18,
    minZoom: 0.18,
    maxZoom: 3.2,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': (node) => nodeColor(node),
          'border-color': (node) => (node.data('is_query_gene') ? '#0f172a' : nodeBorderColor(node)),
          'border-width': (node) => (node.data('is_query_gene') ? 3 : 2),
          'height': (node) => (node.data('is_query_gene') ? 40 : 22),
          'width': (node) => (node.data('is_query_gene') ? 40 : 22),
          'shape': (node) => (node.data('is_query_gene') ? 'diamond' : 'ellipse'),
          'label': (node) => shortGeneLabel(node.data('gene_id')),
          'font-size': (node) => (node.data('is_query_gene') ? 11 : 8),
          'font-weight': (node) => (node.data('is_query_gene') ? 800 : 650),
          'color': '#14213d',
          'text-background-color': '#ffffff',
          'text-background-opacity': 0.72,
          'text-background-padding': 2,
          'text-margin-y': -8,
          'text-wrap': 'wrap',
          'text-max-width': 86,
          'overlay-padding': 6,
        },
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'line-color': (edge) => edgeColor(edge),
          'target-arrow-color': (edge) => edgeColor(edge),
          'opacity': 0.42,
          'width': (edge) => edgeWidth(edge),
        },
      },
      {
        selector: 'edge[edge_type = "tom_edge"]',
        style: {
          'line-color': (edge) => edgeColor(edge),
          'opacity': 0.38,
        },
      },
      {
        selector: 'edge[shared_coexpression = true]',
        style: {
          'line-color': (edge) => edgeColor(edge),
          'opacity': 0.6,
        },
      },
      {
        selector: 'edge[edge_type = "same_gene"]',
        style: {
          'line-style': 'dashed',
          'line-color': '#64748b',
          'width': 0.65,
          'opacity': 0.32,
        },
      },
      {
        selector: ':selected',
        style: {
          'border-color': '#f97316',
          'line-color': '#f97316',
          'target-arrow-color': '#f97316',
          'opacity': 1,
        },
      },
      {
        selector: 'edge:selected',
        style: {
          'line-color': '#dc2626',
          'target-arrow-color': '#dc2626',
          'width': 5,
          'opacity': 1,
          'z-index': 999,
        },
      },
    ],
  });

  state.cy.on('tap', 'node', (event) => renderNodeDetail(event.target));
  state.cy.on('tap', 'edge', (event) => renderEdgeDetail(event.target));
  state.cy.on('tap', (event) => {
    if (event.target === state.cy) {
      renderSelectionEmpty();
    }
  });
};

const computePositions = (nodes) => {
  const cy = state.cy;
  const width = Math.max(900, cy.width() || 900);
  const height = Math.max(560, cy.height() || 560);
  const columns = width > 1000 ? 3 : 2;
  const rows = Math.ceil(NETWORKS.length / columns);
  const cellWidth = width / columns;
  const cellHeight = height / rows;
  const grouped = new Map();

  for (const node of nodes) {
    const network = node.data.network_id || 'unknown';
    if (!grouped.has(network)) grouped.set(network, []);
    grouped.get(network).push(node);
  }

  const positions = {};
  NETWORKS.forEach((network, index) => {
    const group = grouped.get(network) || [];
    const col = index % columns;
    const row = Math.floor(index / columns);
    const cx = col * cellWidth + cellWidth / 2;
    const cyCenter = row * cellHeight + cellHeight / 2;
    const queryNodes = group.filter((node) => node.data.is_query_gene);
    const otherNodes = group.filter((node) => !node.data.is_query_gene);
    const centerCount = Math.max(queryNodes.length, 1);
    const centerRadius = Math.min(42, Math.max(0, centerCount * 8));
    queryNodes.forEach((node, queryIndex) => {
      const angle = (Math.PI * 2 * queryIndex) / centerCount;
      positions[node.data.id] = {
        x: cx + Math.cos(angle) * centerRadius,
        y: cyCenter + Math.sin(angle) * centerRadius,
      };
    });

    const ringCount = Math.max(otherNodes.length, 1);
    const radius = Math.max(78, Math.min(cellWidth, cellHeight) * 0.34);
    otherNodes.forEach((node, nodeIndex) => {
      const angle = (Math.PI * 2 * nodeIndex) / ringCount;
      const ringOffset = (nodeIndex % 7) * 4;
      positions[node.data.id] = {
        x: cx + Math.cos(angle) * (radius + ringOffset),
        y: cyCenter + Math.sin(angle) * (radius + ringOffset),
      };
    });
  });

  return positions;
};

const applyFacetLayout = () => {
  if (!state.cy || !state.lastPayload) return;
  const positions = computePositions(state.lastPayload.elements.nodes || []);
  state.cy.layout({
    name: 'preset',
    positions: (node) => positions[node.id()] || { x: 0, y: 0 },
    fit: true,
    padding: 34,
    animate: false,
  }).run();
};

const renderSelectionEmpty = () => {
  dom.selectionDetail.className = 'detail-body muted';
  dom.selectionDetail.textContent = 'Select a node or edge.';
};

const detailList = (items) => `
  <dl class="kv">
    ${items.map(([key, value]) => `
      <dt>
        <span class="detail-key" tabindex="0" data-tooltip="${escapeHtml(DETAIL_TOOLTIPS[key] || 'Selection field.')}">${escapeHtml(key)}</span>
      </dt>
      <dd>${escapeHtml(value === null || value === undefined || value === '' ? '-' : value)}</dd>
    `).join('')}
  </dl>
`;

const renderNodeDetail = (node) => {
  const data = node.data();
  dom.selectionDetail.className = 'detail-body';
  dom.selectionDetail.innerHTML = detailList([
    ['Gene', data.gene_id],
    ['Network', NETWORK_LABELS[data.network_id] || data.network_id],
    ['Variance', formatNumber(data.variance_log2tpm, 3)],
  ]);
};

const renderEdgeDetail = (edge) => {
  const data = edge.data();
  dom.selectionDetail.className = 'detail-body';
  if (data.edge_type === 'same_gene') {
    dom.selectionDetail.innerHTML = detailList([
      ['Type', 'same_gene'],
      ['Gene', data.gene_id],
      ['Source', data.source],
      ['Target', data.target],
    ]);
    return;
  }
  dom.selectionDetail.innerHTML = detailList([
    ['Type', data.edge_type],
    ['Network', NETWORK_LABELS[data.network_id] || data.network_id],
    ['Gene A', data.gene_a],
    ['Gene B', data.gene_b],
    ['TOM', formatNumber(data.tom, 5)],
    ['Rank', data.rank],
  ]);
};

const renderGraph = (payload) => {
  state.lastPayload = payload;
  const nodes = payload?.elements?.nodes || [];
  const edges = payload?.elements?.edges || [];
  state.cy.elements().remove();
  state.cy.add([...nodes, ...edges]);
  dom.graphEmpty.hidden = nodes.length > 0;
  const title = payload.query_genes?.length
    ? payload.query_genes.join(', ')
    : 'No query loaded';
  dom.graphTitle.textContent = title;
  dom.graphSummary.textContent = `${payload.summary.node_count} nodes · ${payload.summary.edge_count} edges · ${payload.summary.tom_edge_count} TOM edges`;
  renderSelectionEmpty();
  applyFacetLayout();
};

const runQuery = async () => {
  const genes = dom.genesInput.value.trim();
  if (!genes) {
    dom.genesInput.focus();
    return;
  }
  state.loading = true;
  dom.submitButton.disabled = true;
  dom.submitButton.textContent = 'Searching';
  try {
    const payload = await api(buildQueryUrl());
    renderGraph(payload);
  } catch (error) {
    dom.graphTitle.textContent = 'Query failed';
    dom.graphSummary.textContent = error.message || 'Request failed';
  } finally {
    state.loading = false;
    dom.submitButton.disabled = false;
    dom.submitButton.textContent = 'Search';
  }
};

const downloadText = (text, filename, type) => {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const downloadDataUrl = (dataUrl, filename) => {
  const link = document.createElement('a');
  link.href = dataUrl;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
};

const tsvCell = (value) => {
  const text = String(value ?? '');
  if (/[\t\r\n"]/.test(text)) {
    return `"${text.replaceAll('"', '""')}"`;
  }
  return text;
};

const rowsToTsv = (columns, rows) => {
  const lines = [columns.join('\t')];
  for (const row of rows) {
    lines.push(columns.map((column) => tsvCell(row[column])).join('\t'));
  }
  return `${lines.join('\n')}\n`;
};

const currentExportBase = () => {
  const genes = state.lastPayload?.query_genes || ['wgcna'];
  return `wgcna_${genes.join('_').replace(/[^A-Za-z0-9._-]+/g, '_').slice(0, 96) || 'network'}`;
};

const exportPng = () => {
  if (!state.cy || !state.lastPayload) return;
  downloadDataUrl(state.cy.png({ full: true, scale: 2, bg: '#ffffff' }), `${currentExportBase()}.png`);
};

const cySvg = () => {
  const cy = state.cy;
  const extent = cy.elements().boundingBox();
  const padding = 40;
  const width = Math.max(600, extent.w + padding * 2);
  const height = Math.max(400, extent.h + padding * 2);
  const offsetX = padding - extent.x1;
  const offsetY = padding - extent.y1;
  const lines = [];
  lines.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`);
  lines.push('<rect width="100%" height="100%" fill="#ffffff"/>');
  cy.edges().forEach((edge) => {
    const source = edge.source().position();
    const target = edge.target().position();
    const data = edge.data();
    const color = edgeColor(edge);
    const dash = data.edge_type === 'same_gene' ? ' stroke-dasharray="6 5"' : '';
    const widthValue = edgeWidth(edge);
    lines.push(`<line x1="${source.x + offsetX}" y1="${source.y + offsetY}" x2="${target.x + offsetX}" y2="${target.y + offsetY}" stroke="${color}" stroke-width="${widthValue}" opacity="0.65"${dash}/>`);
  });
  cy.nodes().forEach((node) => {
    const position = node.position();
    const data = node.data();
    const radius = data.is_query_gene ? 20 : 11;
    const fill = nodeColor(node);
    const stroke = data.is_query_gene ? '#0f172a' : (NETWORK_COLORS[data.network_id] || '#334155');
    if (data.is_query_gene) {
      const cx = position.x + offsetX;
      const cy = position.y + offsetY;
      lines.push(`<polygon points="${cx},${cy - radius} ${cx + radius},${cy} ${cx},${cy + radius} ${cx - radius},${cy}" fill="${fill}" stroke="${stroke}" stroke-width="3"/>`);
    } else {
      lines.push(`<circle cx="${position.x + offsetX}" cy="${position.y + offsetY}" r="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`);
    }
    lines.push(`<text x="${position.x + offsetX}" y="${position.y + offsetY - radius - 5}" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="10" font-weight="${data.is_query_gene ? 800 : 650}" fill="#14213d">${escapeHtml(shortGeneLabel(data.gene_id))}</text>`);
  });
  lines.push('</svg>');
  return lines.join('');
};

const exportSvg = () => {
  if (!state.cy || !state.lastPayload) return;
  downloadText(cySvg(), `${currentExportBase()}.svg`, 'image/svg+xml;charset=utf-8');
};

const exportTsv = () => {
  if (!state.lastPayload) return;
  const elements = state.lastPayload.elements || {};
  const nodeRows = (elements.nodes || []).map(({ data }) => ({
    id: data.id,
    gene_id: data.gene_id,
    network_id: data.network_id,
    role: data.is_query_gene ? 'query' : 'neighbor',
    module: data.module,
    variance_log2tpm: data.variance_log2tpm,
  }));
  const edgeRows = (elements.edges || []).map(({ data }) => ({
    source: data.source,
    target: data.target,
    interaction: data.edge_type,
    edge_type: data.edge_type,
    network_id: data.network_id || '',
    gene_a: data.gene_a || '',
    gene_b: data.gene_b || '',
    tom: data.tom ?? '',
    rank: data.rank ?? '',
  }));
  const base = currentExportBase();
  downloadText(
    rowsToTsv(
      ['source', 'target', 'interaction', 'edge_type', 'network_id', 'gene_a', 'gene_b', 'tom', 'rank'],
      edgeRows,
    ),
    `${base}_edges.tsv`,
    'text/tab-separated-values;charset=utf-8',
  );
  window.setTimeout(() => {
    downloadText(
      rowsToTsv(
        ['id', 'gene_id', 'network_id', 'role', 'module', 'variance_log2tpm'],
        nodeRows,
      ),
      `${base}_nodes.tsv`,
      'text/tab-separated-values;charset=utf-8',
    );
  }, 150);
};

const init = () => {
  setupCytoscape();
  dom.queryForm.addEventListener('submit', (event) => {
    event.preventDefault();
    runQuery();
  });
  dom.resetViewButton.addEventListener('click', () => {
    applyFacetLayout();
    state.cy.fit(undefined, 34);
  });
  dom.downloadPng.addEventListener('click', exportPng);
  dom.downloadSvg.addEventListener('click', exportSvg);
  dom.downloadTsv.addEventListener('click', exportTsv);
  window.addEventListener('resize', () => {
    window.clearTimeout(window.wgcnaResizeTimer);
    window.wgcnaResizeTimer = window.setTimeout(applyFacetLayout, 150);
  });
};

init();

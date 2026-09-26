import {mountPotatoEFP, PALETTES, mixColour} from './potato-efp.mjs?v=20260923-flower';
import {adaptExpression, formatNumber, formatValue} from './expression.mjs?v=20260923-flower';
import {downloadPdf} from './export.mjs?v=20260926-api1';
import {createViewport} from './viewport.mjs?v=20260923-layout2';

const $ = id => document.getElementById(id);
const state = {viewer: null, viewport: null, viewerPromise: null, result: null, data: null, sequence: 0, controller: null, exporting: false};

async function get(url, signal, text = false) {
  const response = await fetch(url, {signal, headers: {Accept: text ? 'image/svg+xml' : 'application/json'}});
  if (!response.ok) {
    if (response.status === 404 && !text) throw new Error('Gene not found. Check the gene ID and try again.');
    throw new Error(text ? 'The tissue diagram could not be loaded. Please retry.' : 'Expression data is unavailable. Please retry.');
  }
  return text ? response.text() : response.json();
}

function ensureViewer() {
  if (!state.viewerPromise) {
    state.viewerPromise = get('/static/efp/potato-template.svg?v=8', undefined, true).then(svg => {
      state.viewer = mountPotatoEFP($('plant'), svg);
      state.viewport = createViewport(state.viewer.svg, $('map-viewport'), ({zoom, canZoomIn, canZoomOut, enabled}) => {
        $('zoom-level').textContent = `${Math.round(zoom * 100)}%`;
        $('zoom-in').disabled = !canZoomIn;
        $('zoom-out').disabled = !canZoomOut;
        $('zoom-reset').disabled = !enabled;
        $('tissue-tooltip').hidden = true;
      });
      return state.viewer;
    }).catch(error => {
      state.viewerPromise = null;
      throw error;
    });
  }
  return state.viewerPromise;
}

function clearResult(message) {
  state.result = null;
  state.data = null;
  $('plant').hidden = true;
  $('map-empty').hidden = false;
  $('map-empty').textContent = message;
  $('graph-title').textContent = 'Tissue Expression Map';
  $('graph-summary').textContent = message;
  $('efp-legend').hidden = true;
  $('tissue-tooltip').hidden = true;
  $('download-pdf').disabled = true;
  $('query-error').hidden = true;
  $('tissue-table-wrap').hidden = true;
  $('tissue-rows').replaceChildren();
  $('tissue-empty').hidden = false;
  $('tissue-empty').textContent = message;
  $('tissue-note').hidden = true;
  state.viewport?.setEnabled(false);
}

function tooltipText(row) {
  const display = state.result.unit === 'TPM' ? '' : `\n${state.result.unit}: ${formatValue(row.value)}`;
  return `${row.label}\nTPM: ${formatValue(state.data.rawTpm[row.id])}${display}${row.missing ? '\nNo matching tissue or no data.' : ''}`;
}

function showResult(data) {
  const adapted = adaptExpression(data);
  const result = state.viewer.render(adapted.payload);
  state.data = adapted;
  state.result = result;
  $('graph-title').textContent = result.geneId;
  $('graph-summary').textContent = `Tissue mean | ${result.unit}`;
  $('legend-unit').textContent = result.unit;
  $('legend-bar').style.background = `linear-gradient(90deg, ${PALETTES[result.scale.palette].join(', ')})`;
  $('legend-ticks').replaceChildren(...result.ticks.map(value => {
    const span = document.createElement('span');
    span.textContent = formatNumber(value);
    return span;
  }));
  $('value-heading').textContent = result.unit;
  $('value-heading').hidden = adapted.transform === 'tpm';
  $('tissue-rows').replaceChildren(...adapted.tissueValues.map(row => {
    const tr = document.createElement('tr');
    tr.dataset.tissue = row.tissue;
    tr.dataset.mapped = String(row.diagramIds.length > 0);
    const label = document.createElement('td');
    const name = document.createElement('span');
    name.textContent = row.tissue + (row.diagramIds.length ? '' : ' *');
    const wrap = document.createElement('span');
    wrap.className = 'efp-tissue-label';
    const swatch = document.createElement('span');
    swatch.className = 'efp-value-swatch';
    swatch.style.background = mixColour(row.value, result.scale);
    swatch.setAttribute('aria-hidden', 'true');
    wrap.append(swatch, name);
    label.append(wrap);
    tr.append(label);
    for (const value of adapted.transform === 'tpm' ? [row.rawTpm] : [row.rawTpm, row.value]) {
      const td = document.createElement('td');
      td.textContent = formatValue(value);
      tr.append(td);
    }
    return tr;
  }));
  for (const row of result.rows) {
    const group = state.viewer.svg.querySelector(`[data-tissue="${row.id}"]`);
    group.setAttribute('aria-label', tooltipText(row));
  }
  $('tissue-table-wrap').hidden = false;
  $('tissue-table-wrap').scrollTop = 0;
  $('tissue-empty').hidden = true;
  $('tissue-note').hidden = !adapted.unmappedTissues.length;
  $('plant').hidden = false;
  $('map-empty').hidden = true;
  $('efp-legend').hidden = false;
  $('download-pdf').disabled = state.exporting;
  state.viewport.setEnabled(true);
}

async function query() {
  const sequence = ++state.sequence;
  state.controller?.abort();
  state.controller = new AbortController();
  clearResult('Loading expression.');
  $('viewer-panel').setAttribute('aria-busy', 'true');
  $('submit-button').textContent = 'Loading';
  try {
    const gene = $('gene-input').value.trim();
    if (!gene || /[\s,;]/.test(gene)) throw new Error('Enter one gene ID at a time.');
    const params = new URLSearchParams({genes: gene, scope: 'tissue', transform: $('transform-select').value});
    const [, data] = await Promise.all([
      ensureViewer(), get(`/api/bulk-rnaseq/expression?${params}`, state.controller.signal),
    ]);
    if (sequence !== state.sequence) return;
    if (data.genes?.[0]?.geneId !== gene || data.transform !== params.get('transform')) {
      throw new Error('The expression response does not match the query. Please retry.');
    }
    showResult(data);
    const url = new URL(location.href);
    url.searchParams.set('gene', gene);
    url.searchParams.set('transform', data.transform);
    history.replaceState(null, '', url);
  } catch (error) {
    if (sequence !== state.sequence || error.name === 'AbortError') return;
    clearResult('No expression loaded.');
    $('query-error').textContent = error.message;
    $('query-error').hidden = false;
  } finally {
    if (sequence === state.sequence) {
      $('viewer-panel').setAttribute('aria-busy', 'false');
      $('submit-button').textContent = 'Search';
    }
  }
}

function showTooltip(event) {
  const group = event.target.closest?.('[data-tissue]');
  if (!group || !state.result || state.viewport?.isDragging()) {
    $('tissue-tooltip').hidden = true;
    return;
  }
  const row = state.result.rows.find(row => row.id === group.dataset.tissue);
  if (!row) return;
  const tooltip = $('tissue-tooltip');
  tooltip.textContent = tooltipText(row);
  tooltip.hidden = false;
  const rect = group.getBoundingClientRect();
  const x = event.type === 'focusin' ? rect.right : event.clientX;
  const y = event.type === 'focusin' ? rect.top : event.clientY;
  tooltip.style.left = `${Math.max(12, Math.min(x + 14, innerWidth - tooltip.offsetWidth - 12))}px`;
  tooltip.style.top = `${Math.max(12, Math.min(y + 14, innerHeight - tooltip.offsetHeight - 12))}px`;
}

$('query-form').addEventListener('submit', event => { event.preventDefault(); query(); });
$('transform-select').addEventListener('change', query);
$('zoom-in').addEventListener('click', () => state.viewport?.zoomIn());
$('zoom-out').addEventListener('click', () => state.viewport?.zoomOut());
$('zoom-reset').addEventListener('click', () => state.viewport?.reset());
$('download-pdf').addEventListener('click', async () => {
  if (!state.result || state.exporting) return;
  const result = state.result;
  state.exporting = true;
  $('download-pdf').disabled = true;
  $('download-pdf').textContent = 'Exporting…';
  $('query-error').hidden = true;
  try { await downloadPdf(state.viewer.svg, result, state.data); }
  catch (error) {
    if (state.result === result) {
      $('query-error').textContent = `PDF export failed. ${error.message || 'Please retry.'}`;
      $('query-error').hidden = false;
    }
  } finally {
    state.exporting = false;
    $('download-pdf').textContent = 'PDF';
    $('download-pdf').disabled = !state.result;
  }
});
for (const name of ['pointermove', 'focusin']) $('plant').addEventListener(name, showTooltip);
for (const name of ['pointerleave', 'focusout']) $('plant').addEventListener(name, () => { $('tissue-tooltip').hidden = true; });
document.addEventListener('keydown', event => { if (event.key === 'Escape') $('tissue-tooltip').hidden = true; });
window.addEventListener('scroll', () => { $('tissue-tooltip').hidden = true; }, true);

const params = new URLSearchParams(location.search);
if (params.get('gene')) $('gene-input').value = params.get('gene').slice(0, 200);
if ([...$('transform-select').options].some(option => option.value === params.get('transform'))) {
  $('transform-select').value = params.get('transform');
}
query();

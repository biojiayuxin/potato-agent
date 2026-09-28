(() => {
  'use strict';
  const API = '/api/genome-annotations';
  const PAGE_SIZE = 20;
  const $ = id => document.getElementById(id);
  const split = value => [...new Set(String(value || '').split(/[\s,;]+/).filter(Boolean))];
  const format = value => Number(value || 0).toLocaleString('en-US');
  const list = value => Array.isArray(value) ? value : value ? [value] : [];
  const show = value => value == null || value === '' ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const state = {metadata: null, query: null, result: null, selected: new Map(), pageItems: [], sequence: 0,
    controller: null, exportScope: 'all'};
  const arrayFields = ['assemblyIds', 'ids'];
  const scalarControls = {view: 'result-view'};
  const urlFields = [...Object.keys(scalarControls), 'tfStatus', 'q'];
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function message(node, text, error = false) { node.textContent = text; node.classList.toggle('is-error', error); }
  function hideDomainTooltip() { $('domain-tooltip').hidden = true; }
  function showDomainTooltip(event) {
    const hit = event.target.closest?.('.domain-hit');
    if (!hit) { hideDomainTooltip(); return; }
    const tooltip = $('domain-tooltip');
    tooltip.textContent = hit.getAttribute('aria-label').replaceAll('; ', '\n');
    tooltip.hidden = false;
    const rect = hit.getBoundingClientRect();
    const x = event.type === 'focusin' ? rect.right : event.clientX;
    const y = event.type === 'focusin' ? rect.top : event.clientY;
    tooltip.style.left = `${Math.max(12,Math.min(x + 14,innerWidth - tooltip.offsetWidth - 12))}px`;
    tooltip.style.top = `${Math.max(12,Math.min(y + 14,innerHeight - tooltip.offsetHeight - 12))}px`;
  }
  async function request(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', ...options});
    if (!response.ok) {
      let detail;
      try { detail = (await response.json()).detail; } catch { /* Non-JSON service error. */ }
      const error = new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return response;
  }
  const json = async (path, options) => (await request(path, options)).json();
  function assemblyLabel(id) {
    if (id === 'phased_tetraploid/Des') return 'Désirée';
    return state.metadata?.assemblies.find(a => a.assemblyId === id)?.label || id;
  }
  function recordId(row) { return state.query?.view === 'transcripts' ? row.transcriptId : row.geneId; }
  function selectionFor(row) { return {assemblyId: row.assemblyId, [state.query.view === 'transcripts' ? 'transcriptId' : 'geneId']: recordId(row)}; }
  const keyFor = row => JSON.stringify([row.assemblyId, recordId(row)]);
  function badge(text, type = '') { return el('span', text, `badge ${type}`); }
  function tfNode(row) {
    const node = el('div',null,'tf-families');
    list(row.tfFamilies).forEach(family => node.append(badge(family, 'tf')));
    if (!node.childElementCount) node.textContent = '—';
    return node;
  }
  function readQuery() {
    const query = {assemblyIds: [...$('assembly-options').querySelectorAll('input:checked')].map(n => n.value),
      ids: split($('batch-ids').value), q: '', tfStatus: $('gene-scope').querySelector('input:checked').value,
      limit: PAGE_SIZE, offset: 0};
    Object.entries(scalarControls).forEach(([key, id]) => { query[key] = $(id).value; });
    if ($('search-kind').value === 'ids') query.ids = [...new Set([...query.ids, ...split($('search-input').value)])];
    else query.q = $('search-input').value.trim();
    if (!query.assemblyIds.length) throw new Error('Select at least one genome.');
    if (query.ids.length > 5000) throw new Error('Use no more than 5,000 IDs in one query.');
    return query;
  }
  function setQuery(query) {
    const assemblies = query.assemblyIds?.length ? query.assemblyIds : state.metadata.assemblies.map(a => a.assemblyId);
    $('assembly-options').querySelectorAll('input').forEach(n => { n.checked = assemblies.includes(n.value); });
    $('gene-scope').querySelectorAll('input').forEach(n => { n.checked = n.value === (query.tfStatus === 'selected' ? 'selected' : 'all'); });
    Object.entries(scalarControls).forEach(([key, id]) => {
      const value = query[key] || (key === 'view' ? 'genes' : 'all');
      if ([...$(id).options].some(o => o.value === value)) $(id).value = value;
    });
    $('search-kind').value = query.q ? 'q' : 'ids';
    $('search-input').value = query.q || (query.ids?.length === 1 ? query.ids[0] : '');
    $('batch-ids').value = query.ids?.length > 1 ? query.ids.join('\n') : '';
    $('batch-section').open = query.ids?.length > 1;
    updateSearchLabel();
  }
  function defaultQuery() {
    const assembly = state.metadata.assemblies.find(a => a.label === 'DMv8.2' || a.assemblyId.endsWith('/DMv8.2')) || state.metadata.assemblies[0];
    return {assemblyIds:[assembly.assemblyId], view:'genes', tfStatus:'all', ids:[], limit:PAGE_SIZE, offset:0};
  }
  function queryFromURL() {
    const params = new URLSearchParams(location.search), query = defaultQuery();
    arrayFields.forEach(key => { if (params.has(key)) query[key] = params.getAll(key); });
    urlFields.forEach(key => { if (params.has(key)) query[key] = params.get(key); });
    query.tfStatus = query.tfStatus === 'selected' ? 'selected' : 'all';
    // Large batches must be deliberately reimported rather than silently represented by a partial URL.
    query.ids = list(query.ids).slice(0, 1);
    return query;
  }
  function updateURL() {
    if (!state.query) return;
    const params = new URLSearchParams();
    arrayFields.forEach(key => {
      if (key === 'ids' && state.query.ids?.length > 1) return;
      list(state.query[key]).forEach(value => params.append(key, value));
    });
    urlFields.forEach(key => {
      if (state.query[key] && state.query[key] !== 'all') params.set(key, state.query[key]);
    });
    if (state.query.ids?.length > 1) params.set('batch', 'reimport');
    history.replaceState(null, '', `${location.pathname}?${params}`);
  }
  function updateSearchLabel() {
    const byId = $('search-kind').value === 'ids';
    $('search-label').textContent = byId ? 'Exact identifier' : 'Annotation description';
    $('search-input').placeholder = byId ? 'e.g. DM8.2_chr05G25210' : 'e.g. DNA-binding or kinase';
  }
  function updateSelection() {
    const selected = state.pageItems.filter(row => state.selected.has(keyFor(row))).length;
    $('select-page').checked = Boolean(selected && selected === state.pageItems.length);
    $('select-page').indeterminate = selected > 0 && selected < state.pageItems.length;
    $('export-selected').textContent = `Export selected (${format(state.selected.size)})`;
    $('export-selected').disabled = !state.result || !state.selected.size;
    $('ask-potato-agent').disabled = !state.result;
  }
  function renderReport(report = {}) {
    const node = $('id-report'); node.replaceChildren();
    [['unmatchedIds','IDs not found'],['ambiguousIds','IDs found in multiple genomes'],['filteredIds','IDs not matching this search']].forEach(([key, label]) => {
      const values = list(report[key]);
      if (values.length) node.append(el('p', `${label} (${format(values.length)}): ${values.slice(0, 25).map(show).join(', ')}${values.length > 25 ? '… (full report in export)' : ''}`));
    });
    node.hidden = !node.childElementCount;
  }
  async function runQuery(query, reset = true) {
    hideDomainTooltip();
    state.controller?.abort();
    state.controller = new AbortController();
    const sequence = ++state.sequence;
    state.query = {...query};
    state.result = null;
    if (reset) state.selected.clear();
    $('search-button').disabled = true;
    $('export-all').disabled = $('export-selected').disabled = $('previous-page').disabled = $('next-page').disabled = true;
    $('select-page').disabled = true;
    message($('query-status'), 'Searching annotations…');
    updateURL(); updateSelection();
    try {
      const result = await json(`${API}/query`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(query), signal:state.controller.signal});
      if (sequence !== state.sequence) return;
      state.result = result;
      state.query = {...query, datasetVersion:result.datasetVersion};
      state.pageItems = result.items;
      message($('query-status'), `${format(result.total)} matching ${query.view === 'genes' ? 'genes' : 'transcripts'}`);
      if (query.ids?.length > 1) $('query-status').append(el('span', ' · Reimport batch IDs when reopening this URL.', 'secondary-line'));
      renderReport(result.idReport);
      renderResults();
      $('export-all').disabled = result.total === 0;
      $('previous-page').disabled = !query.offset;
      $('next-page').disabled = !result.hasMore;
      $('select-page').disabled = !result.items.length;
      $('page-status').textContent = result.total ? `${format(result.offset + 1)}–${format(result.offset + result.items.length)} of ${format(result.total)}` : 'No matches';
    } catch (error) {
      if (error.name === 'AbortError' || sequence !== state.sequence) return;
      state.pageItems = []; state.selected.clear();
      message($('query-status'), error.status === 409 ? 'This annotation release changed. Refresh the page before querying again.' : error.message, true);
      $('results-body').replaceChildren();
      const row = el('tr'), cell = el('td', 'No results available. Adjust the query or retry.', 'empty-message'); cell.colSpan = 6; row.append(cell); $('results-body').append(row);
      $('id-report').hidden = true; $('page-status').textContent = '';
    } finally {
      if (sequence === state.sequence) { $('search-button').disabled = false; updateSelection(); }
    }
  }
  function renderResults() {
    hideDomainTooltip();
    $('results-body').replaceChildren();
    $('identifier-heading').textContent = state.query.view === 'genes' ? 'Gene' : 'Transcript';
    $('protein-heading').textContent = state.query.view === 'genes' ? 'Transcripts' : 'Protein length';
    if (!state.pageItems.length) {
      const row = el('tr'), cell = el('td','No records match this search.','empty-message'); cell.colSpan = 6; row.append(cell); $('results-body').append(row); return;
    }
    state.pageItems.forEach(row => {
      const tr = el('tr',null,'record-row'), selectCell = el('td'), checkbox = el('input');
      checkbox.type = 'checkbox'; checkbox.checked = state.selected.has(keyFor(row)); checkbox.setAttribute('aria-label',`Select ${recordId(row)} in ${assemblyLabel(row.assemblyId)}`);
      checkbox.addEventListener('change', () => { if (checkbox.checked) state.selected.set(keyFor(row),selectionFor(row)); else state.selected.delete(keyFor(row)); updateSelection(); }); selectCell.append(checkbox);
      const idCell = el('td'), open = el('button',recordId(row),'record-link'); open.type = 'button'; open.setAttribute('aria-expanded','false'); idCell.append(open);
      if (state.query.view === 'transcripts') idCell.append(el('span',row.geneId,'secondary-line'));
      const signatures = el('td',list(row.signatures).slice(0,5).map(show).join(', ') || '—','signature-summary');
      if (row.signatureCount > 5) signatures.append(el('span',`+${format(row.signatureCount - 5)} signatures`,'secondary-line'));
      const tf = el('td'); tf.append(tfNode(row));
      tr.append(selectCell,idCell,el('td',assemblyLabel(row.assemblyId)),el('td',state.query.view === 'genes' ? format(row.transcriptCount) : row.proteinLength == null ? '—' : `${format(row.proteinLength)} aa`),signatures,tf);
      open.addEventListener('click', () => toggleDetail(row,tr,open));
      $('results-body').append(tr);
    });
    updateSelection();
  }
  async function browserLink(row, container) {
    const assembly = state.metadata.assemblies.find(a => a.assemblyId === row.assemblyId);
    const button = el('button','Genome Browser'); button.type = 'button'; container.append(button);
    button.addEventListener('click', async () => {
      button.disabled = true;
      const status = el('span','Resolving genomic location…','muted'); container.append(status);
      try {
        const browserAssembly = assembly?.browserAssemblyId || row.assemblyId;
        const payload = await json(`/api/genome-browser/features/resolve?${new URLSearchParams({assembly:browserAssembly,id:row.transcriptId || row.geneId})}`);
        const feature = payload.gene || payload.transcript || payload;
        if (!feature.refName || !Number.isFinite(Number(feature.start)) || !Number.isFinite(Number(feature.end))) throw new Error('No genomic location is available for this record.');
        const link = el('a','Open Genome Browser'); link.href = `/genome-browser?${new URLSearchParams({assembly:browserAssembly,loc:`${feature.refName}:${feature.start}..${feature.end}`})}`;
        link.target = '_blank'; link.rel = 'noopener noreferrer'; button.replaceWith(link); status.remove(); link.click();
      } catch (error) { message(status,`Genome Browser: ${error.message}`,true); button.disabled = false; }
    });
  }
  async function toggleDetail(row,tr,button) {
    hideDomainTooltip();
    if (tr.nextElementSibling?.classList.contains('detail-row')) { tr.nextElementSibling.remove(); tr.classList.remove('expanded'); button.setAttribute('aria-expanded','false'); return; }
    tr.classList.add('expanded'); button.setAttribute('aria-expanded','true');
    const detail = el('tr',null,'detail-row'), cell = el('td'), content = el('div','Loading domains…','detail-content'); cell.colSpan = 6; cell.append(content); detail.append(cell); tr.after(detail);
    try {
      const endpoint = state.query.view === 'genes' ? 'genes' : 'transcripts';
      const payload = await json(`${API}/${endpoint}/${encodeURIComponent(recordId(row))}?${new URLSearchParams({assembly:row.assemblyId})}`);
      if (!content.isConnected) return;
      if (payload.datasetVersion !== state.result?.datasetVersion) throw new Error('The release changed. Refresh the results to view the current annotations.');
      content.replaceChildren();
      const heading = el('div',null,'detail-heading'); heading.append(el('p','Protein domains')); browserLink(row,heading); content.append(heading);
      if (endpoint === 'transcripts') { renderTranscript(payload,content); return; }
      await renderGeneTranscripts(payload,content,row.assemblyId);
    } catch (error) { message(content,error.message,true); }
  }
  async function renderGeneTranscripts(payload,container,assemblyId) {
    const loaders = payload.transcripts.map(transcript => {
      const card = el('section',null,'transcript-card');
      card.setAttribute('aria-label',transcript.transcriptId);
      card.append(el('h4',transcript.transcriptId,'transcript-title'));
      const evidence = el('div','Loading protein domains…','transcript-evidence');
      evidence.setAttribute('aria-busy','true');
      card.append(evidence); container.append(card);
      async function load() {
        message(evidence,'Loading protein domains…');
        evidence.setAttribute('aria-busy','true');
        try {
          const data = await json(`${API}/transcripts/${encodeURIComponent(transcript.transcriptId)}?${new URLSearchParams({assembly:assemblyId})}`);
          if (!card.isConnected) return;
          if (data.datasetVersion !== payload.datasetVersion) throw new Error('The release changed. Refresh the page.');
          evidence.replaceChildren(); renderTranscript(data,evidence,false);
        } catch (error) {
          if (!card.isConnected) return;
          message(evidence,error.message,true);
          const retry = el('button','Retry','transcript-retry'); retry.type = 'button';
          retry.addEventListener('click',load); evidence.append(retry);
        } finally { evidence.setAttribute('aria-busy','false'); }
      }
      return load;
    });
    // Keep transcript order stable and render each response as it arrives.
    let next = 0;
    await Promise.all(Array.from({length:Math.min(4,loaders.length)},async () => {
      while (container.isConnected && next < loaders.length) await loaders[next++]();
    }));
  }
  function appendEvidenceTable(container, headers, rows, className = 'evidence-table') {
    const shell = el('div',null,'table-shell'), table = el('table',null,className), head = el('thead'), hrow = el('tr'), body = el('tbody');
    headers.forEach(h => hrow.append(el('th',h))); head.append(hrow);
    rows.forEach(values => {
      const row = el('tr');
      values.forEach((value,index) => {
        const cell = el('td'), text = show(value), content = el('div',text,'evidence-cell');
        if (text.length > 180) { content.tabIndex = 0; content.setAttribute('role','region'); content.setAttribute('aria-label',`${headers[index]} (scroll for full evidence)`); }
        cell.append(content); row.append(cell);
      });
      body.append(row);
    });
    table.append(head,body); shell.append(table); container.append(shell);
  }
  function renderTranscript(payload,container,showIdentifier = true) {
    const transcript = payload.transcript, hits = payload.matches || [];
    const metadata = [showIdentifier ? transcript.transcriptId : null,transcript.proteinLength == null ? null : `${format(transcript.proteinLength)} aa`].filter(Boolean);
    if (metadata.length) container.append(el('p',metadata.join(' · '),'muted'));
    if (list(transcript.tfFamilies).length) {
      const families = tfNode(transcript); families.prepend(el('span','TF families: ')); container.append(families);
    }
    if (hits.length && transcript.proteinLength) container.append(domainChart(hits,transcript.proteinLength));
    if (hits.length) {
      appendEvidenceTable(container,['Database','Signature / description','Coordinates','Raw score','InterPro','GO terms'],hits.map(h => [h.analysis,`${h.signatureAccession} · ${h.signatureDescription || ''}`,`${h.start}–${h.end}`,h.score,[h.interproAccession,h.interproDescription].filter(Boolean).join(' · '),h.goTerms]));
    } else container.append(el('p','No domain annotations to display.','muted'));
  }
  function domainChart(hits,length) {
    const ns = 'http://www.w3.org/2000/svg', create = (tag,attrs = {},text) => {
      const node = document.createElementNS(ns,tag); Object.entries(attrs).forEach(([key,value]) => node.setAttribute(key,String(value))); if (text != null) node.textContent = text; return node;
    };
    const svg = create('svg',{viewBox:'0 0 860 100',class:'domain-chart',role:'img','aria-label':`Protein domain locations, ${length} amino acids`});
    const colors = {CDD:'#467dab',PANTHER:'#a06ab7',Pfam:'#348b69',SMART:'#c69134'};
    const left = 88, width = 750, x = position => left + (Number(position) - 1) / length * width;
    let y = 30;
    svg.append(create('text',{x:left,y:15},'1'),create('text',{x:left + width,y:15,'text-anchor':'end'},`${format(length)} aa`));
    for (const analysis of ['CDD','PANTHER','Pfam','SMART',...new Set(hits.map(h => h.analysis).filter(a => !colors[a]))]) {
      const group = hits.filter(h => h.analysis === analysis).sort((a,b) => Number(a.start)-Number(b.start) || Number(a.end)-Number(b.end));
      if (!group.length) continue;
      const lanes = [];
      svg.append(create('text',{x:5,y:y + 14},analysis));
      svg.append(create('line',{x1:left,x2:left+width,y1:y+10,y2:y+10,stroke:'#d8e2de'}));
      for (const hit of group) {
        let lane = lanes.findIndex(end => end < Number(hit.start)); if (lane < 0) lane = lanes.length; lanes[lane] = Number(hit.end);
        const label = `${hit.analysis} ${hit.signatureAccession}: ${hit.signatureDescription || ''}; amino acids ${hit.start}–${hit.end}; raw score ${hit.score}; InterPro ${hit.interproAccession || '—'} ${hit.interproDescription || ''}; GO ${show(hit.goTerms)}`;
        const rect = create('rect',{x:x(hit.start),y:y + lane * 20,width:Math.max(2,(Number(hit.end)-Number(hit.start)+1)/length*width),height:17,rx:3,fill:colors[analysis] || '#65738b',class:'domain-hit',tabindex:0,'aria-label':label});
        svg.append(rect);
      }
      y += Math.max(1,lanes.length) * 20 + 16;
    }
    svg.setAttribute('viewBox',`0 0 860 ${y}`); const shell = el('div',null,'chart-shell'); shell.append(svg); return shell;
  }
  function openExport(scope) {
    hideDomainTooltip();
    state.exportScope = scope;
    $('export-description').textContent = scope === 'selected' ? `${format(state.selected.size)} selected records across pages.` : `All ${format(state.result.total)} matching ${state.query.view}, across every result page.`;
    message($('export-status'),''); $('export-dialog').showModal();
  }
  function downloadExport(event) {
    event.preventDefault();
    const tables = [...$('export-form').querySelectorAll('input[name="table"]:checked')].map(n => n.value);
    if (!tables.length) { message($('export-status'),'Choose at least one table.',true); return; }
    const payload = {query:state.query,tables,format:$('export-format').value,selection:state.exportScope === 'selected' ? [...state.selected.values()] : [],datasetVersion:state.result.datasetVersion};
    // Let the browser stream the attachment to disk without buffering a complete ZIP in JavaScript.
    const frame = el('iframe'), form = el('form'), input = el('input');
    const exportId = crypto.randomUUID?.()
      || Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2,'0')).join('');
    frame.name = `annotation-export-${exportId}`; frame.hidden = true; frame.title = 'Annotation export response';
    form.method = 'POST'; form.action = `${API}/export-download`; form.target = frame.name; form.hidden = true;
    input.type = 'hidden'; input.name = 'payload'; input.value = JSON.stringify(payload); form.append(input);
    frame.addEventListener('load',() => {
      const body = frame.contentDocument?.body?.textContent?.trim();
      if (!body) return;
      let detail;
      try { detail = JSON.parse(body).detail; } catch { /* Display a generic message for a non-JSON error response. */ }
      const text = typeof detail === 'string' ? detail : detail?.message;
      message($('export-status'),text ? `${text} Refresh the query if the annotation release changed.` : 'Export could not start. Please retry the query and download.',true);
      frame.remove();
    });
    document.body.append(frame,form); form.submit(); form.remove();
    message($('export-status'),'Download requested. Your browser will show its progress; use browser downloads to cancel.');
  }
  function bind() {
    for (const event of ['pointerover','pointermove','focusin']) $('results-body').addEventListener(event,showDomainTooltip);
    for (const event of ['pointerout','pointerleave','focusout']) $('results-body').addEventListener(event,hideDomainTooltip);
    document.addEventListener('keydown',event => { if (event.key === 'Escape') hideDomainTooltip(); });
    window.addEventListener('scroll',hideDomainTooltip,true);
    window.addEventListener('resize',hideDomainTooltip);
    const applyQuery = () => { try { runQuery(readQuery()); } catch (error) { message($('query-status'),error.message,true); } };
    $('query-form').addEventListener('submit',event => { event.preventDefault(); applyQuery(); });
    $('gene-scope').addEventListener('change',applyQuery);
    $('search-kind').addEventListener('change',updateSearchLabel);
    $('all-assemblies').addEventListener('click',() => { $('assembly-options').querySelectorAll('input').forEach(input => { input.checked = true; }); });
    $('reset-search').addEventListener('click',() => { setQuery(defaultQuery()); $('batch-file').value = ''; runQuery(readQuery()); });
    $('batch-file').addEventListener('change',async () => {
      const file = $('batch-file').files[0]; if (!file) return;
      if (file.size > 2 * 1024 * 1024) { message($('query-status'),'Choose an ID file smaller than 2 MB.',true); return; }
      try { const ids = split(await file.text()); if (ids.length > 5000) throw new Error('Use no more than 5,000 IDs in one query.'); $('batch-ids').value = ids.join('\n'); message($('query-status'),`${format(ids.length)} IDs loaded. Press Search to apply them.`); } catch (error) { message($('query-status'),error.message,true); }
    });
    $('previous-page').addEventListener('click',() => runQuery({...state.query,offset:Math.max(0,state.query.offset - state.query.limit)},false));
    $('next-page').addEventListener('click',() => runQuery({...state.query,offset:state.query.offset + state.query.limit},false));
    $('select-page').addEventListener('change',() => { state.pageItems.forEach(row => { if ($('select-page').checked) state.selected.set(keyFor(row),selectionFor(row)); else state.selected.delete(keyFor(row)); }); renderResults(); });
    $('export-all').addEventListener('click',() => openExport('all')); $('export-selected').addEventListener('click',() => openExport('selected'));
    $('close-export').addEventListener('click',() => $('export-dialog').close()); $('export-form').addEventListener('submit',downloadExport);
    window.PotatoAgentExamples?.bind('functional_annotation');
  }
  async function init() {
    bind(); $('ask-potato-agent').disabled = true;
    try {
      state.metadata = await json(`${API}/metadata`);
      if (!state.metadata.assemblies?.length) throw new Error('No annotation genomes are available in this release.');
      state.metadata.assemblies.forEach(assembly => {
        const label = el('label',null,'assembly-option'), checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = assembly.assemblyId; label.append(checkbox,el('span',assemblyLabel(assembly.assemblyId))); $('assembly-options').append(label);
      });
      const params = new URLSearchParams(location.search); setQuery(queryFromURL()); $('query-controls').disabled = false;
      if (params.get('batch') === 'reimport') {
        message($('query-status'),'This link originally used a batch of IDs. Reimport the batch and press Search to reproduce the results.');
        $('results-body').replaceChildren(); $('batch-section').open = true; updateSelection();
      } else await runQuery(readQuery());
    } catch (error) {
      $('dataset-status').hidden = false;
      message($('dataset-status'),`Annotation data is unavailable. ${error.message}`,true);
      message($('query-status'),'The annotation release could not be opened. Please try again later.',true);
      $('results-body').replaceChildren();
    }
  }
  init();
})();

(() => {
  'use strict';

  const PAGE_SIZE = 20;
  const ANNOTATION_PAGE_SIZE = 30;
  const PAPER_PAGE_SIZE = 10;
  const SIMILARITY_PAGE_SIZE = 10;
  const GENE_PATH_PREFIX = '/genes/';
  const GENOME_BROWSER_ASSEMBLIES = {
    'DMv8.2': 'monoploid/DMv8.2',
  };
  const ICON_COPY_PATH = '/static/lite/icons/copy_button.png';
  const ICON_COPIED_PATH = '/static/lite/icons/copied.png';

  const EXPRESSION_TISSUE_GROUPS = [
    /flower|floral|anther|stamen|carpel|perianth|petal|sepal|pistil|ovary|inflorescence/i,
    /fruit|berry/i,
    /stem/i,
    /leaf/i,
    /root/i,
    /stolon/i,
    /tuber/i,
  ];

  const EXPRESSION_TISSUE_LABELS = new Map([
    ['immature small tuber (transection diameter < 1 cm)', 'Small tuber'],
    ['immature big tuber (1 cm <transection diameter < 5 cm)', 'Large tuber'],
  ]);

  const SEQUENCE_TYPES = [
    { key: 'cds', label: 'CDS', headerLabel: 'cds', unit: 'bp' },
    { key: 'protein', label: 'Protein', headerLabel: 'protein', unit: 'aa' },
    { key: 'genomic', label: 'Genomic span', headerLabel: 'genomic_span', unit: 'bp' },
    {
      key: 'promoter',
      label: 'Promoter',
      headerLabel: 'atg_upstream_2000bp',
      unit: 'bp',
    },
  ];

  const searchPanel = document.getElementById('gene-search-panel');
  const searchForm = document.getElementById('gene-search-form');
  const searchInput = document.getElementById('gene-search-input');
  const searchButton = document.getElementById('gene-search-button');
  const searchStatus = document.getElementById('gene-search-status');
  const resultsSection = document.getElementById('gene-results-section');
  const resultsSummary = document.getElementById('gene-results-summary');
  const results = document.getElementById('gene-results');
  const pagination = document.getElementById('gene-pagination');
  const previousButton = document.getElementById('gene-previous-page');
  const nextButton = document.getElementById('gene-next-page');
  const pageSummary = document.getElementById('gene-page-summary');

  const detailSection = document.getElementById('gene-detail-section');
  const detailBack = document.getElementById('gene-detail-back');
  const detailLoading = document.getElementById('gene-detail-loading');
  const detailContent = document.getElementById('gene-detail-content');
  const detailHeading = document.getElementById('gene-detail-heading');
  const detailSymbols = document.getElementById('gene-detail-symbols');
  const genomeBrowserLink = document.getElementById('gene-genome-browser-link');
  const reliabilityGrade = document.getElementById('gene-reliability-grade');
  const predictedFunction = document.getElementById('gene-predicted-function');
  const reportedIdList = document.getElementById('gene-reported-id-list');
  const transcriptList = document.getElementById('gene-transcript-list');
  const annotationToolbar = document.getElementById('gene-annotation-toolbar');
  const annotationFilter = document.getElementById('gene-annotation-filter');
  const annotationCount = document.getElementById('gene-annotation-count');
  const annotationList = document.getElementById('gene-annotation-list');
  const annotationPagination = document.getElementById('gene-annotation-pagination');
  const annotationPrevious = document.getElementById('gene-annotation-previous');
  const annotationNext = document.getElementById('gene-annotation-next');
  const annotationPageSummary = document.getElementById('gene-annotation-page');
  const expressionChart = document.getElementById('gene-expression-chart');
  const expressionUnit = document.getElementById('gene-expression-unit');
  const paperList = document.getElementById('gene-paper-list');
  const paperMore = document.getElementById('gene-paper-more');
  const similarityTableWrap = document.getElementById('gene-similarity-table-wrap');
  const similarityBody = document.getElementById('gene-similarity-body');
  const similarityEmpty = document.getElementById('gene-similarity-empty');
  const similarityMore = document.getElementById('gene-similarity-more');
  const sequenceTranscript = document.getElementById('gene-sequence-transcript');
  const sequenceTypeSelect = document.getElementById('gene-sequence-type');
  const sequenceState = document.getElementById('gene-sequence-state');
  const sequenceContent = document.getElementById('gene-sequence-content');
  const sequenceMetadata = document.getElementById('gene-sequence-metadata');
  const sequenceValue = document.getElementById('gene-sequence-value');
  const sequenceCopy = document.getElementById('gene-sequence-copy');
  const sequenceCopyIcon = document.getElementById('gene-sequence-copy-icon');

  let activeSearchController = null;
  let activeDetailController = null;
  let activeSequenceController = null;
  let currentQuery = '';
  let currentOffset = 0;
  let currentHasMore = false;
  let detailPayload = null;
  let currentAnnotationPage = 0;
  let currentPaperLimit = PAPER_PAGE_SIZE;
  let currentSimilarityLimit = SIMILARITY_PAGE_SIZE;
  let currentSequenceType = '';
  let currentSequenceRequest = 0;
  let copyResetTimer = null;
  const sequenceCache = new Map();

  function formatCount(value) {
    const number = Number(value);
    return new Intl.NumberFormat('en-US').format(Number.isFinite(number) ? number : 0);
  }

  function formatDecimal(value, maximumFractionDigits = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return '0';
    }
    return new Intl.NumberFormat('en-US', {
      maximumFractionDigits,
      minimumFractionDigits: 0,
    }).format(number);
  }

  function routeFromLocation() {
    if (window.location.pathname.startsWith(GENE_PATH_PREFIX)) {
      const encoded = window.location.pathname.slice(GENE_PATH_PREFIX.length);
      if (encoded && !encoded.includes('/')) {
        try {
          return { mode: 'detail', geneId: decodeURIComponent(encoded) };
        } catch (_error) {
          return { mode: 'detail', geneId: encoded };
        }
      }
    }

    const params = new URLSearchParams(window.location.search);
    const rawOffset = Number.parseInt(params.get('offset') || '0', 10);
    return {
      mode: 'search',
      query: (params.get('q') || '').trim(),
      offset: Number.isFinite(rawOffset) && rawOffset > 0 ? rawOffset : 0,
    };
  }

  function setSearchLocation(query, offset) {
    const params = new URLSearchParams({ q: query });
    if (offset > 0) {
      params.set('offset', String(offset));
    }
    window.history.pushState({}, '', `/genes?${params.toString()}`);
  }

  function setStatus(message, isError = false) {
    searchStatus.textContent = message;
    searchStatus.classList.toggle('is-error', isError);
    searchStatus.hidden = !message;
  }

  function setSearching(searching) {
    searchButton.disabled = searching;
    searchButton.textContent = searching ? 'Searching' : 'Search';
    resultsSection.setAttribute('aria-busy', searching ? 'true' : 'false');
  }

  function showSearchView() {
    searchPanel.hidden = false;
    detailSection.hidden = true;
    detailLoading.hidden = true;
    detailContent.hidden = true;
    if (activeDetailController) {
      activeDetailController.abort();
      activeDetailController = null;
    }
    if (activeSequenceController) {
      activeSequenceController.abort();
      activeSequenceController = null;
    }
  }

  function showDetailLoading(geneId) {
    if (activeSearchController) {
      activeSearchController.abort();
      activeSearchController = null;
    }
    setSearching(false);
    setStatus('');
    searchPanel.hidden = true;
    resultsSection.hidden = true;
    detailSection.hidden = false;
    detailContent.hidden = true;
    detailLoading.hidden = false;
    detailHeading.textContent = geneId || 'Gene details';
    document.title = `${geneId || 'Gene details'} | Genes | Potato Research`;
  }

  function renderLoading() {
    results.replaceChildren();
    resultsSection.hidden = false;
    pagination.hidden = true;
    resultsSummary.textContent = `Searching for "${currentQuery}"`;
    for (let index = 0; index < 3; index += 1) {
      const skeleton = document.createElement('article');
      skeleton.className = 'gene-result is-loading';
      skeleton.setAttribute('aria-hidden', 'true');
      for (let lineIndex = 0; lineIndex < 3; lineIndex += 1) {
        const line = document.createElement('div');
        line.className = 'gene-skeleton-line';
        skeleton.append(line);
      }
      results.append(skeleton);
    }
  }

  function reportedIdLabel(item) {
    const identifier = String(item?.identifier || '');
    const qualifier = item?.qualifier;
    if (qualifier?.type === 'blast_identity_pct' && Number.isFinite(Number(qualifier.value))) {
      return `${identifier} (${Number(qualifier.value).toFixed(2)}%)`;
    }
    return identifier;
  }

  function appendValueList(
    target,
    values,
    limit = Number.POSITIVE_INFINITY,
    formatter = (value) => String(value),
  ) {
    const filtered = Array.isArray(values) ? values.filter((value) => formatter(value)) : [];
    if (!filtered.length) {
      target.textContent = 'Not assigned';
      return;
    }
    const visible = filtered.slice(0, limit);
    visible.forEach((value, index) => {
      if (index > 0) {
        target.append(document.createTextNode(', '));
      }
      target.append(document.createTextNode(formatter(value)));
    });
    if (filtered.length > visible.length) {
      const more = document.createElement('span');
      more.className = 'gene-more-count';
      more.textContent = `+${filtered.length - visible.length} more`;
      target.append(more);
    }
  }

  function createResultRow(label) {
    const wrapper = document.createElement('div');
    wrapper.className = 'gene-result-field';
    const term = document.createElement('dt');
    term.textContent = label;
    const value = document.createElement('dd');
    wrapper.append(term, value);
    return { wrapper, value };
  }

  function createResultField(label, values, formatter) {
    const row = createResultRow(label);
    appendValueList(row.value, values, Number.POSITIVE_INFINITY, formatter);
    return row.wrapper;
  }

  function createGeneResult(gene) {
    const article = document.createElement('article');
    article.className = 'gene-result';

    const title = document.createElement('a');
    title.className = 'gene-result-title';
    title.href = `/genes/${encodeURIComponent(String(gene.geneId || ''))}`;
    title.target = '_blank';
    title.rel = 'noopener noreferrer';
    title.textContent = String(gene.geneId || 'Unknown gene');
    title.setAttribute('aria-label', `Open ${title.textContent} in a new tab`);

    const geneIdRow = createResultRow('Gene ID:');
    geneIdRow.value.append(title);
    const descriptionRow = createResultRow('Summary:');
    descriptionRow.value.classList.add('gene-description');
    descriptionRow.value.textContent = String(
      gene.descriptionExcerpt || 'No predicted description available.',
    );

    const fields = document.createElement('dl');
    fields.className = 'gene-result-fields';
    fields.append(
      geneIdRow.wrapper,
      createResultField('Symbol:', gene.symbols),
      createResultField('Reported ID:', gene.reportedIds, reportedIdLabel),
      descriptionRow.wrapper,
    );

    article.append(fields);
    return article;
  }

  function renderPagination(geneCount) {
    previousButton.disabled = currentOffset === 0;
    nextButton.disabled = !currentHasMore;
    pagination.hidden = currentOffset === 0 && !currentHasMore;
    const start = geneCount ? currentOffset + 1 : 0;
    const end = currentOffset + geneCount;
    pageSummary.textContent = `${formatCount(start)}-${formatCount(end)}`;
  }

  function renderSearchResults(payload) {
    const genes = Array.isArray(payload.genes) ? payload.genes : [];
    currentHasMore = Boolean(payload.hasMore);
    results.replaceChildren();
    resultsSection.hidden = false;

    if (!genes.length) {
      const empty = document.createElement('div');
      empty.className = 'gene-empty-result';
      empty.textContent = `No genes matched "${currentQuery}".`;
      results.append(empty);
      resultsSummary.textContent = '0 results';
      pagination.hidden = true;
      return;
    }

    const start = currentOffset + 1;
    const end = currentOffset + genes.length;
    resultsSummary.textContent = `Showing ${formatCount(start)}-${formatCount(end)} for "${currentQuery}"`;
    genes.forEach((gene) => results.append(createGeneResult(gene)));
    renderPagination(genes.length);
  }

  async function responseJson(response, fallbackMessage) {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof payload.detail === 'string' ? payload.detail : fallbackMessage;
      throw new Error(detail);
    }
    return payload;
  }

  async function searchGenes(query, offset = 0) {
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }
    showSearchView();
    if (activeSearchController) {
      activeSearchController.abort();
    }
    const controller = new AbortController();
    activeSearchController = controller;
    currentQuery = trimmed;
    currentOffset = offset;
    searchInput.value = trimmed;
    document.title = `${trimmed} | Genes | Potato Research`;
    setStatus('');
    setSearching(true);
    renderLoading();

    const params = new URLSearchParams({
      q: trimmed,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    try {
      const response = await fetch(`/api/v1/genes/search?${params.toString()}`, {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      const payload = await responseJson(response, 'Gene search failed.');
      renderSearchResults(payload);
    } catch (error) {
      if (error?.name === 'AbortError') {
        return;
      }
      resultsSection.hidden = true;
      setStatus(error instanceof Error ? error.message : 'Gene search failed.', true);
    } finally {
      if (activeSearchController === controller) {
        activeSearchController = null;
        setSearching(false);
      }
    }
  }

  function movePage(offset) {
    const nextOffset = Math.max(0, offset);
    setSearchLocation(currentQuery, nextOffset);
    searchGenes(currentQuery, nextOffset);
    resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function locationText(location) {
    if (!location || !location.seqid) {
      return 'Not available';
    }
    const start = Number(location.start);
    const end = Number(location.end);
    const hasRange = Number.isFinite(start) && Number.isFinite(end);
    const range = hasRange
      ? `${location.seqid}:${formatCount(start)}-${formatCount(end)}`
      : String(location.seqid);
    return location.strand ? `${range} (${location.strand})` : range;
  }

  function appendDefinitionItem(target, label, value, className = '') {
    const wrapper = document.createElement('div');
    if (className) {
      wrapper.className = className;
    }
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    if (value instanceof Node) {
      description.append(value);
    } else {
      description.textContent = String(value || 'Not available');
    }
    wrapper.append(term, description);
    target.append(wrapper);
  }

  function renderDelimitedValues(target, values, formatter = (value) => String(value)) {
    const normalized = Array.isArray(values)
      ? values.map((value) => formatter(value)).filter(Boolean)
      : [];
    target.textContent = normalized.length ? normalized.join('; ') : 'Not assigned';
    target.classList.toggle('is-empty', normalized.length === 0);
  }

  function sequenceTypeConfig(sequenceType) {
    return SEQUENCE_TYPES.find((item) => item.key === sequenceType) || SEQUENCE_TYPES[0];
  }

  function renderDetailHeader(payload) {
    const gene = payload.gene || {};
    const geneId = String(gene.geneId || 'Unknown gene');
    detailHeading.textContent = geneId;

    const browserAssembly = GENOME_BROWSER_ASSEMBLIES[String(payload.assembly || '')];
    const location = gene.location;
    const start = Number(location?.start);
    const end = Number(location?.end);
    const hasBrowserLocation = browserAssembly
      && location?.seqid
      && Number.isFinite(start)
      && Number.isFinite(end);
    if (hasBrowserLocation) {
      const params = new URLSearchParams({
        assembly: browserAssembly,
        loc: `${location.seqid}:${start}..${end}`,
      });
      genomeBrowserLink.href = `/genomes/browser?${params.toString()}`;
      genomeBrowserLink.hidden = false;
    } else {
      genomeBrowserLink.hidden = true;
    }
  }

  function renderSymbols(payload) {
    const symbols = Array.isArray(payload.gene?.symbols) ? payload.gene.symbols : [];
    renderDelimitedValues(detailSymbols, symbols);
  }

  function renderReportedIds(payload) {
    const reportedIds = Array.isArray(payload.gene?.reportedIds)
      ? payload.gene.reportedIds
      : [];
    renderDelimitedValues(reportedIdList, reportedIds, reportedIdLabel);
  }

  function renderDescription(payload) {
    const description = payload.description || {};
    predictedFunction.textContent = String(
      description.predictedFunction || 'No predicted function is available for this gene.',
    );
    const grade = String(description.reliabilityGrade || '').trim();
    reliabilityGrade.hidden = !grade;
    reliabilityGrade.textContent = grade ? `Reliability ${grade}` : '';
    reliabilityGrade.title = grade ? 'Prediction reliability grade' : '';
    reliabilityGrade.dataset.grade = grade;
  }

  function renderTranscripts(transcripts) {
    transcriptList.replaceChildren();

    if (!transcripts.length) {
      const empty = document.createElement('div');
      empty.className = 'gene-detail-empty';
      empty.textContent = 'No transcript records available.';
      transcriptList.append(empty);
      return;
    }

    const list = document.createElement('ul');
    list.className = 'gene-transcripts';
    transcripts.forEach((transcript) => {
      const item = document.createElement('li');
      const transcriptId = document.createElement('span');
      transcriptId.className = 'gene-transcript-id';
      transcriptId.textContent = String(transcript.transcriptId || 'Unknown transcript');
      item.append(transcriptId);
      if (transcript.isRepresentative) {
        const representative = document.createElement('small');
        representative.className = 'gene-representative-badge';
        representative.textContent = 'Representative';
        item.append(representative);
      }
      list.append(item);
    });
    transcriptList.append(list);
  }

  function annotationTermText(term) {
    if (typeof term === 'string') {
      return term;
    }
    if (!term || typeof term !== 'object') {
      return '';
    }
    return [term.id, term.name, term.description, term.label].filter(Boolean).join(' ');
  }

  function interproHref(identifier) {
    const id = String(identifier || '').trim();
    if (/^IPR\d+$/i.test(id)) {
      return `https://www.ebi.ac.uk/interpro/entry/InterPro/${encodeURIComponent(id)}/`;
    }
    return '';
  }

  function createInterproItem(term) {
    const item = document.createElement('li');
    const identifier = typeof term === 'string' ? term : String(term?.id || '');
    const description = typeof term === 'object'
      ? String(term?.description || term?.name || term?.label || '')
      : '';
    const href = interproHref(identifier);
    let identifierElement;
    if (href) {
      identifierElement = document.createElement('a');
      identifierElement.href = href;
      identifierElement.target = '_blank';
      identifierElement.rel = 'noopener noreferrer';
    } else {
      identifierElement = document.createElement('span');
    }
    identifierElement.className = 'gene-annotation-id';
    identifierElement.textContent = identifier || 'Unlabeled annotation';
    item.append(identifierElement);
    if (description && description !== identifier) {
      const detail = document.createElement('span');
      detail.className = 'gene-annotation-description';
      detail.textContent = description;
      item.append(detail);
    }
    return item;
  }

  function renderAnnotations() {
    const annotations = detailPayload?.annotations || {};
    const terms = Array.isArray(annotations.interpro) ? annotations.interpro : [];
    const query = annotationFilter.value.trim().toLocaleLowerCase();
    const filtered = query
      ? terms.filter((term) => annotationTermText(term).toLocaleLowerCase().includes(query))
      : terms;
    const pageCount = Math.max(1, Math.ceil(filtered.length / ANNOTATION_PAGE_SIZE));
    currentAnnotationPage = Math.min(currentAnnotationPage, pageCount - 1);
    const start = currentAnnotationPage * ANNOTATION_PAGE_SIZE;
    const visible = filtered.slice(start, start + ANNOTATION_PAGE_SIZE);

    annotationList.replaceChildren();
    annotationList.setAttribute('aria-labelledby', 'gene-annotations-heading');
    annotationToolbar.hidden = terms.length <= ANNOTATION_PAGE_SIZE;
    annotationFilter.hidden = terms.length <= ANNOTATION_PAGE_SIZE;
    annotationFilter.placeholder = 'Filter InterPro domains';

    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'gene-detail-empty';
      empty.textContent = query
        ? 'No InterPro domains match this filter.'
        : 'No InterPro domains available.';
      annotationList.append(empty);
      annotationCount.textContent = query ? '0 matches' : '0 domains';
      annotationPagination.hidden = true;
      return;
    }

    const list = document.createElement('ul');
    list.className = 'gene-annotation-terms';
    visible.forEach((term) => list.append(createInterproItem(term)));
    annotationList.append(list);

    const end = start + visible.length;
    annotationCount.textContent = `Showing ${formatCount(start + 1)}-${formatCount(end)} of ${formatCount(filtered.length)}`;
    annotationPagination.hidden = filtered.length <= ANNOTATION_PAGE_SIZE;
    annotationPrevious.disabled = currentAnnotationPage === 0;
    annotationNext.disabled = end >= filtered.length;
    annotationPageSummary.textContent = `Page ${formatCount(currentAnnotationPage + 1)} of ${formatCount(pageCount)}`;
  }

  function expressionTissueGroup(tissueName) {
    const name = String(tissueName || '');
    const index = EXPRESSION_TISSUE_GROUPS.findIndex((pattern) => pattern.test(name));
    return index < 0 ? EXPRESSION_TISSUE_GROUPS.length : index;
  }

  function orderedExpressionTissues(values) {
    return values
      .map((tissue, index) => ({ tissue, index }))
      .sort((left, right) => {
        const groupDifference = expressionTissueGroup(left.tissue.tissue)
          - expressionTissueGroup(right.tissue.tissue);
        return groupDifference || left.index - right.index;
      })
      .map((item) => item.tissue);
  }

  function expressionTissueLabel(tissueName) {
    const fullName = String(tissueName || 'Unknown tissue');
    return EXPRESSION_TISSUE_LABELS.get(fullName.toLocaleLowerCase()) || fullName;
  }

  function createExpressionLegend() {
    const legend = document.createElement('div');
    legend.className = 'gene-expression-legend';

    const meanItem = document.createElement('span');
    const meanSwatch = document.createElement('span');
    meanSwatch.className = 'gene-expression-legend-mean';
    meanSwatch.setAttribute('aria-hidden', 'true');
    meanItem.append(meanSwatch, document.createTextNode('Mean TPM'));

    const sdItem = document.createElement('span');
    const sdSwatch = document.createElement('span');
    sdSwatch.className = 'gene-expression-legend-sd';
    sdSwatch.setAttribute('aria-hidden', 'true');
    sdItem.append(sdSwatch, document.createTextNode('SD'));

    legend.append(meanItem, sdItem);
    return legend;
  }

  function renderExpression(payload) {
    const expression = payload.expression || {};
    const tissues = Array.isArray(expression.tissues)
      ? orderedExpressionTissues(expression.tissues)
      : [];
    expressionChart.replaceChildren();
    expressionUnit.hidden = !tissues.length;

    if (!tissues.length) {
      const empty = document.createElement('div');
      empty.className = 'gene-detail-empty';
      empty.textContent = 'No Expression Atlas tissue summary available.';
      expressionChart.append(empty);
      return;
    }

    const maximum = Math.max(
      ...tissues.map((item) => (Number(item.meanTpm) || 0) + (Number(item.sdTpm) || 0)),
      1,
    );
    const scaleMaximum = maximum * 1.08;
    const scroll = document.createElement('div');
    scroll.className = 'gene-expression-scroll';
    const plot = document.createElement('div');
    plot.className = 'gene-expression-plot';
    plot.style.gridTemplateColumns = `repeat(${tissues.length}, 60px)`;
    plot.style.minWidth = `${Math.max(720, tissues.length * 52)}px`;

    tissues.forEach((tissue) => {
      const fullName = String(tissue.tissue || 'Unknown tissue');
      const shortName = expressionTissueLabel(fullName);
      const mean = Number(tissue.meanTpm) || 0;
      const sd = Number(tissue.sdTpm) || 0;
      const tooltipText = `${fullName}: mean ${formatDecimal(mean, 3)} TPM; SD ${formatDecimal(sd, 3)} TPM`;
      const column = document.createElement('div');
      column.className = 'gene-expression-column';
      column.tabIndex = 0;
      column.title = tooltipText;
      column.setAttribute('aria-label', tooltipText);

      const tooltip = document.createElement('div');
      tooltip.className = 'gene-expression-tooltip';
      tooltip.textContent = tooltipText;

      const barArea = document.createElement('div');
      barArea.className = 'gene-expression-bar-area';
      const meanPercentage = (mean / scaleMaximum) * 100;
      const lowerPercentage = (Math.max(0, mean - sd) / scaleMaximum) * 100;
      const upperPercentage = (Math.min(scaleMaximum, mean + sd) / scaleMaximum) * 100;

      const meanValue = document.createElement('span');
      meanValue.className = 'gene-expression-value';
      meanValue.textContent = formatDecimal(mean, 2);
      meanValue.style.bottom = `calc(${meanPercentage}% + 7px)`;

      const bar = document.createElement('div');
      bar.className = 'gene-expression-bar';
      bar.style.height = `${meanPercentage}%`;
      if (mean > 0) {
        bar.classList.add('has-value');
      }
      bar.setAttribute('aria-hidden', 'true');

      if (sd > 0) {
        const errorBar = document.createElement('div');
        errorBar.className = 'gene-expression-error-bar';
        errorBar.style.bottom = `${lowerPercentage}%`;
        errorBar.style.height = `${upperPercentage - lowerPercentage}%`;
        errorBar.setAttribute('aria-hidden', 'true');
        barArea.append(errorBar);
      }
      barArea.append(bar, meanValue);

      const label = document.createElement('div');
      label.className = 'gene-expression-label';
      const labelText = document.createElement('span');
      labelText.textContent = shortName;
      label.append(labelText);

      column.append(tooltip, barArea, label);
      plot.append(column);
    });
    scroll.append(plot);
    expressionChart.append(createExpressionLegend(), scroll);
  }

  function normalizedDoi(value) {
    return String(value || '')
      .trim()
      .replace(/^doi:\s*/i, '')
      .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '');
  }

  function doiHref(doi) {
    return `https://doi.org/${doi.split('/').map((part) => encodeURIComponent(part)).join('/')}`;
  }

  function renderPapers() {
    const papers = Array.isArray(detailPayload?.papers) ? detailPayload.papers : [];
    paperList.replaceChildren();

    if (!papers.length) {
      paperList.classList.add('is-empty');
      const empty = document.createElement('li');
      empty.className = 'gene-detail-empty';
      empty.textContent = 'No literature references are linked to this gene.';
      paperList.append(empty);
      paperMore.hidden = true;
      return;
    }

    paperList.classList.remove('is-empty');
    papers.slice(0, currentPaperLimit).forEach((paper) => {
      const item = document.createElement('li');
      const title = document.createElement('div');
      title.className = 'gene-paper-title';
      title.textContent = String(paper.title || paper.doi || 'Untitled reference');
      item.append(title);
      const doi = normalizedDoi(paper.doi);
      if (doi) {
        const link = document.createElement('a');
        link.className = 'gene-paper-doi';
        link.href = doiHref(doi);
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = doi;
        item.append(link);
      }
      paperList.append(item);
    });

    paperMore.hidden = papers.length <= PAPER_PAGE_SIZE;
    if (!paperMore.hidden) {
      const remaining = papers.length - currentPaperLimit;
      paperMore.textContent = remaining > 0
        ? `Show ${formatCount(Math.min(PAPER_PAGE_SIZE, remaining))} more`
        : 'Show fewer';
    }
  }

  function formatEValue(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return 'Not available';
    }
    if (number === 0) {
      return '0';
    }
    if (Math.abs(number) < 0.001 || Math.abs(number) >= 1000) {
      return number.toExponential(2);
    }
    return formatDecimal(number, 5);
  }

  function renderSimilarity() {
    const hits = Array.isArray(detailPayload?.proteinSimilarityHits)
      ? detailPayload.proteinSimilarityHits
      : [];
    similarityBody.replaceChildren();

    if (!hits.length) {
      similarityTableWrap.hidden = true;
      similarityEmpty.hidden = false;
      similarityEmpty.textContent = 'No UniRef100 similarity hits available.';
      similarityMore.hidden = true;
      return;
    }

    similarityTableWrap.hidden = false;
    similarityEmpty.hidden = true;
    hits.slice(0, currentSimilarityLimit).forEach((hit) => {
      const row = document.createElement('tr');
      const subjectCell = document.createElement('td');
      const subject = String(hit.subjectId || 'Unknown subject');
      const subjectLink = document.createElement('a');
      subjectLink.href = `https://www.uniprot.org/uniref/${encodeURIComponent(subject)}`;
      subjectLink.target = '_blank';
      subjectLink.rel = 'noopener noreferrer';
      subjectLink.textContent = subject;
      subjectCell.append(subjectLink);

      const identityCell = document.createElement('td');
      identityCell.textContent = `${formatDecimal(hit.identityPct, 3)}%`;
      const eValueCell = document.createElement('td');
      eValueCell.textContent = formatEValue(hit.eValue);
      const descriptionCell = document.createElement('td');
      descriptionCell.textContent = String(hit.description || 'No description');
      row.append(subjectCell, identityCell, eValueCell, descriptionCell);
      similarityBody.append(row);
    });

    similarityMore.hidden = hits.length <= SIMILARITY_PAGE_SIZE;
    if (!similarityMore.hidden) {
      const remaining = hits.length - currentSimilarityLimit;
      similarityMore.textContent = remaining > 0
        ? `Show ${formatCount(Math.min(SIMILARITY_PAGE_SIZE, remaining))} more`
        : 'Show fewer';
    }
  }

  function selectedTranscript() {
    const transcripts = Array.isArray(detailPayload?.transcripts) ? detailPayload.transcripts : [];
    return transcripts.find((item) => item.transcriptId === sequenceTranscript.value) || null;
  }

  function sequenceAvailability(transcriptId, sequenceType) {
    const availability = Array.isArray(detailPayload?.sequenceAvailability)
      ? detailPayload.sequenceAvailability
      : [];
    return availability.find(
      (item) => item.transcriptId === transcriptId && item.sequenceType === sequenceType,
    ) || null;
  }

  function sequenceCacheKey(transcriptId, sequenceType) {
    return `${String(detailPayload?.gene?.geneId || '')}\u0000${transcriptId}\u0000${sequenceType}`;
  }

  function setSequenceState(message, isError = false) {
    sequenceContent.hidden = true;
    sequenceState.replaceChildren();
    sequenceState.hidden = false;
    sequenceState.classList.toggle('is-error', isError);
    const text = document.createElement('span');
    text.textContent = message;
    sequenceState.append(text);
  }

  function wrapSequence(sequence, width = 80) {
    const lines = [];
    for (let offset = 0; offset < sequence.length; offset += width) {
      lines.push(sequence.slice(offset, offset + width));
    }
    return lines.join('\n');
  }

  function sequenceFasta(payload) {
    const config = sequenceTypeConfig(payload.sequenceType);
    const header = `>${String(payload.geneId || '')}|${String(payload.transcriptId || '')}|${config.headerLabel}`;
    return `${header}\n${wrapSequence(String(payload.sequence || ''))}`;
  }

  function renderSequencePayload(payload) {
    const config = sequenceTypeConfig(payload.sequenceType);
    sequenceState.hidden = true;
    sequenceState.classList.remove('is-error');
    sequenceContent.hidden = false;
    sequenceMetadata.replaceChildren();
    appendDefinitionItem(sequenceMetadata, 'Type', config.label);
    appendDefinitionItem(
      sequenceMetadata,
      'Length',
      `${formatCount(payload.length)} ${config.unit}`,
    );
    if (payload.location) {
      appendDefinitionItem(sequenceMetadata, 'Location', locationText(payload.location));
    }
    if (payload.sequenceType === 'genomic' || payload.sequenceType === 'promoter') {
      appendDefinitionItem(sequenceMetadata, 'Orientation', "5' to 3' transcript orientation");
    }
    if (payload.anchor) {
      const anchor = payload.anchor;
      const requested = Number(anchor.requestedLength);
      const parts = [`${String(anchor.type || 'ATG')} at ${formatCount(anchor.position)}`];
      if (Number.isFinite(requested)) {
        parts.push(`${formatCount(requested)} bp requested`);
      }
      if (anchor.wasTruncated) {
        parts.push('truncated at chromosome boundary');
      }
      appendDefinitionItem(sequenceMetadata, 'Anchor', parts.join(' | '));
    }
    sequenceValue.textContent = sequenceFasta(payload);
    if (copyResetTimer) {
      window.clearTimeout(copyResetTimer);
      copyResetTimer = null;
    }
    resetSequenceCopyFeedback();
  }

  function renderSequenceIdle() {
    setSequenceState('No sequence is available for this transcript.');
  }

  function renderSequenceTypeOptions(preferredType = '') {
    sequenceTypeSelect.replaceChildren();
    const transcript = selectedTranscript();
    const availableTypes = Array.isArray(transcript?.sequenceTypes) ? transcript.sequenceTypes : [];
    const availableConfigs = SEQUENCE_TYPES.filter((config) => availableTypes.includes(config.key));
    const selectedType = availableTypes.includes(preferredType)
      ? preferredType
      : availableTypes.includes('cds')
        ? 'cds'
        : availableConfigs[0]?.key || '';

    availableConfigs.forEach((config) => {
      const availability = sequenceAvailability(transcript.transcriptId, config.key);
      const option = document.createElement('option');
      option.value = config.key;
      const length = availability
        ? ` (${formatCount(availability.length)} ${config.unit})`
        : '';
      option.textContent = `${config.label}${length}`;
      sequenceTypeSelect.append(option);
    });
    currentSequenceType = selectedType;
    sequenceTypeSelect.value = selectedType;
    sequenceTypeSelect.disabled = availableConfigs.length === 0;
  }

  function renderSequences(payload) {
    const transcripts = Array.isArray(payload.transcripts) ? payload.transcripts : [];
    sequenceTranscript.replaceChildren();
    transcripts.forEach((transcript) => {
      const option = document.createElement('option');
      option.value = String(transcript.transcriptId || '');
      option.textContent = transcript.isRepresentative
        ? `${option.value} (representative)`
        : option.value;
      sequenceTranscript.append(option);
    });
    const representative = transcripts.find((item) => item.isRepresentative) || transcripts[0];
    sequenceTranscript.value = representative?.transcriptId || '';
    sequenceTranscript.disabled = transcripts.length === 0;
    renderSequenceTypeOptions('cds');
    loadSequence();
  }

  async function loadSequence() {
    const transcript = selectedTranscript();
    const sequenceType = currentSequenceType;
    if (!transcript || !sequenceType) {
      renderSequenceIdle();
      return;
    }

    const key = sequenceCacheKey(transcript.transcriptId, sequenceType);
    const cached = sequenceCache.get(key);
    if (cached) {
      renderSequencePayload(cached);
      return;
    }

    if (activeSequenceController) {
      activeSequenceController.abort();
    }
    const controller = new AbortController();
    activeSequenceController = controller;
    currentSequenceRequest += 1;
    const requestNumber = currentSequenceRequest;
    const config = sequenceTypeConfig(sequenceType);
    setSequenceState(`Loading ${config.label}`);

    const geneId = String(detailPayload?.gene?.geneId || '');
    const params = new URLSearchParams({ transcript_id: transcript.transcriptId });
    try {
      const response = await fetch(
        `/api/v1/genes/${encodeURIComponent(geneId)}/sequences/${encodeURIComponent(sequenceType)}?${params.toString()}`,
        { headers: { Accept: 'application/json' }, signal: controller.signal },
      );
      const payload = await responseJson(response, 'Sequence request failed.');
      sequenceCache.set(key, payload);
      const selectionIsCurrent = requestNumber === currentSequenceRequest
        && sequenceTranscript.value === transcript.transcriptId
        && sequenceTypeSelect.value === sequenceType;
      if (selectionIsCurrent) {
        renderSequencePayload(payload);
      }
    } catch (error) {
      if (error?.name === 'AbortError') {
        return;
      }
      if (requestNumber === currentSequenceRequest) {
        setSequenceState(
          error instanceof Error ? error.message : 'Sequence request failed.',
          true,
        );
      }
    } finally {
      if (activeSequenceController === controller) {
        activeSequenceController = null;
      }
    }
  }

  function resetDetailState() {
    detailPayload = null;
    currentAnnotationPage = 0;
    currentPaperLimit = PAPER_PAGE_SIZE;
    currentSimilarityLimit = SIMILARITY_PAGE_SIZE;
    currentSequenceType = '';
    currentSequenceRequest += 1;
    sequenceCache.clear();
    annotationFilter.value = '';
    if (activeSequenceController) {
      activeSequenceController.abort();
      activeSequenceController = null;
    }
  }

  function renderGeneDetail(payload) {
    detailPayload = payload;
    renderDetailHeader(payload);
    renderSymbols(payload);
    renderReportedIds(payload);
    renderTranscripts(Array.isArray(payload.transcripts) ? payload.transcripts : []);
    renderDescription(payload);
    renderAnnotations();
    renderExpression(payload);
    renderPapers();
    renderSimilarity();
    renderSequences(payload);
    detailLoading.hidden = true;
    detailContent.hidden = false;
    const geneId = String(payload.gene?.geneId || 'Gene details');
    document.title = `${geneId} | Genes | Potato Research`;
  }

  async function loadGeneDetail(geneId) {
    resetDetailState();
    showDetailLoading(geneId);
    if (activeDetailController) {
      activeDetailController.abort();
    }
    const controller = new AbortController();
    activeDetailController = controller;
    try {
      const response = await fetch(`/api/v1/genes/${encodeURIComponent(geneId)}`, {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      const payload = await responseJson(response, 'Gene details could not be loaded.');
      renderGeneDetail(payload);
    } catch (error) {
      if (error?.name === 'AbortError') {
        return;
      }
      detailLoading.hidden = true;
      detailContent.hidden = true;
      setStatus(error instanceof Error ? error.message : 'Gene details could not be loaded.', true);
    } finally {
      if (activeDetailController === controller) {
        activeDetailController = null;
      }
    }
  }

  async function copySequenceToClipboard() {
    const text = sequenceValue.textContent || '';
    if (!text) {
      return;
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const field = document.createElement('textarea');
        field.value = text;
        field.setAttribute('readonly', '');
        field.className = 'gene-copy-fallback';
        document.body.append(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        if (!copied) {
          throw new Error('Copy failed');
        }
      }
      sequenceCopyIcon.src = ICON_COPIED_PATH;
      sequenceCopy.setAttribute('aria-label', 'Sequence copied');
      sequenceCopy.title = 'Sequence copied';
    } catch (_error) {
      sequenceCopy.setAttribute('aria-label', 'Copy failed');
      sequenceCopy.title = 'Copy failed';
    }
    if (copyResetTimer) {
      window.clearTimeout(copyResetTimer);
    }
    copyResetTimer = window.setTimeout(() => {
      resetSequenceCopyFeedback();
      copyResetTimer = null;
    }, 3000);
  }

  function resetSequenceCopyFeedback() {
    sequenceCopyIcon.src = ICON_COPY_PATH;
    sequenceCopy.setAttribute('aria-label', 'Copy sequence');
    sequenceCopy.title = 'Copy sequence';
  }

  function handleRoute() {
    const route = routeFromLocation();
    if (route.mode === 'detail') {
      loadGeneDetail(route.geneId);
      return;
    }

    showSearchView();
    setStatus('');
    if (route.query) {
      searchGenes(route.query, route.offset);
      return;
    }

    if (activeSearchController) {
      activeSearchController.abort();
      activeSearchController = null;
    }
    currentQuery = '';
    currentOffset = 0;
    searchInput.value = '';
    resultsSection.hidden = true;
    document.title = 'Genes | Potato Research';
  }

  searchForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    if (!query) {
      searchInput.focus();
      return;
    }
    setSearchLocation(query, 0);
    searchGenes(query, 0);
  });

  document.querySelectorAll('[data-gene-example]').forEach((button) => {
    button.addEventListener('click', () => {
      searchInput.value = button.dataset.geneExample || '';
      searchInput.focus();
    });
  });

  previousButton.addEventListener('click', () => movePage(currentOffset - PAGE_SIZE));
  nextButton.addEventListener('click', () => movePage(currentOffset + PAGE_SIZE));
  annotationFilter.addEventListener('input', () => {
    currentAnnotationPage = 0;
    renderAnnotations();
  });
  annotationPrevious.addEventListener('click', () => {
    currentAnnotationPage = Math.max(0, currentAnnotationPage - 1);
    renderAnnotations();
  });
  annotationNext.addEventListener('click', () => {
    currentAnnotationPage += 1;
    renderAnnotations();
  });
  paperMore.addEventListener('click', () => {
    const papers = Array.isArray(detailPayload?.papers) ? detailPayload.papers : [];
    currentPaperLimit = currentPaperLimit >= papers.length
      ? PAPER_PAGE_SIZE
      : Math.min(papers.length, currentPaperLimit + PAPER_PAGE_SIZE);
    renderPapers();
  });
  similarityMore.addEventListener('click', () => {
    const hits = Array.isArray(detailPayload?.proteinSimilarityHits)
      ? detailPayload.proteinSimilarityHits
      : [];
    currentSimilarityLimit = currentSimilarityLimit >= hits.length
      ? SIMILARITY_PAGE_SIZE
      : Math.min(hits.length, currentSimilarityLimit + SIMILARITY_PAGE_SIZE);
    renderSimilarity();
  });
  sequenceTranscript.addEventListener('change', () => {
    if (activeSequenceController) {
      activeSequenceController.abort();
      activeSequenceController = null;
    }
    currentSequenceRequest += 1;
    renderSequenceTypeOptions('cds');
    loadSequence();
  });
  sequenceTypeSelect.addEventListener('change', () => {
    currentSequenceType = sequenceTypeSelect.value;
    loadSequence();
  });
  sequenceCopy.addEventListener('click', copySequenceToClipboard);
  detailBack.addEventListener('click', () => {
    if (activeSequenceController) {
      activeSequenceController.abort();
    }
  });
  window.addEventListener('popstate', handleRoute);

  handleRoute();
})();

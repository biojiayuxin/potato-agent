const PLOIDY_LABELS = {
  monoploid: 'Monoploid',
  phased_diploid: 'Phased diploid',
  phased_tetraploid: 'Phased tetraploid',
};

const PLOIDY_ORDER = ['monoploid', 'phased_diploid', 'phased_tetraploid'];

const state = {
  assemblies: [],
};

const els = {
  search: document.getElementById('accession-search'),
  ploidy: document.getElementById('ploidy-filter'),
  status: document.getElementById('catalog-status'),
  body: document.getElementById('accession-table-body'),
};

function ploidyLabel(value) {
  return PLOIDY_LABELS[value] || value || 'Not available';
}

function ploidyClass(value) {
  if (value === 'monoploid') return 'ploidy-monoploid';
  if (value === 'phased_diploid') return 'ploidy-phased-diploid';
  if (value === 'phased_tetraploid') return 'ploidy-phased-tetraploid';
  return '';
}

function assemblySortValue(assembly) {
  const ploidyIndex = PLOIDY_ORDER.indexOf(assembly.ploidy);
  const order = ploidyIndex === -1 ? PLOIDY_ORDER.length : ploidyIndex;
  return { order, accession: String(assembly.sample || '') };
}

function sortAssemblies(left, right) {
  const leftValue = assemblySortValue(left);
  const rightValue = assemblySortValue(right);
  if (leftValue.order !== rightValue.order) return leftValue.order - rightValue.order;
  return leftValue.accession.localeCompare(rightValue.accession, 'en', { numeric: true, sensitivity: 'base' });
}

function browserUrl(assemblyId) {
  const params = new URLSearchParams({ assembly: assemblyId });
  return `/genomes/browser?${params.toString()}`;
}

function doiUrl(doi) {
  const encoded = doi.split('/').map((part) => encodeURIComponent(part)).join('/');
  return `https://doi.org/${encoded}`;
}

function setMessage(message, isError = false) {
  const row = document.createElement('tr');
  const cell = document.createElement('td');
  cell.colSpan = 3;
  cell.className = 'table-message';
  cell.classList.toggle('is-error', isError);
  cell.textContent = message;
  row.append(cell);
  els.body.replaceChildren(row);
}

function createAccessionCell(assembly) {
  const cell = document.createElement('td');
  const accession = String(assembly.sample || '').trim();
  if (!accession) {
    cell.className = 'muted-value';
    cell.textContent = 'Not available';
    return cell;
  }

  const link = document.createElement('a');
  link.className = 'accession-link';
  link.href = browserUrl(assembly.id);
  link.textContent = accession;
  cell.append(link);
  return cell;
}

function createPloidyCell(assembly) {
  const cell = document.createElement('td');
  const badge = document.createElement('span');
  badge.className = `ploidy-badge ${ploidyClass(assembly.ploidy)}`.trim();
  badge.textContent = ploidyLabel(assembly.ploidy);
  cell.append(badge);
  return cell;
}

function createDoiCell(assembly) {
  const cell = document.createElement('td');
  const doi = String(assembly.doi || '').trim();
  if (!doi) {
    cell.className = 'muted-value';
    cell.textContent = 'Not available';
    return cell;
  }

  const link = document.createElement('a');
  link.className = 'doi-link';
  link.href = doiUrl(doi);
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = doi;
  cell.append(link);
  return cell;
}

function filteredAssemblies() {
  const query = els.search.value.trim().toLocaleLowerCase('en');
  const ploidy = els.ploidy.value;
  return state.assemblies.filter((assembly) => {
    if (ploidy !== 'all' && assembly.ploidy !== ploidy) return false;
    if (!query) return true;
    const searchText = [assembly.sample, assembly.doi, ploidyLabel(assembly.ploidy)]
      .filter(Boolean)
      .join(' ')
      .toLocaleLowerCase('en');
    return searchText.includes(query);
  });
}

function renderTable() {
  const assemblies = filteredAssemblies();
  els.status.classList.remove('is-error');
  els.status.textContent = `Showing ${assemblies.length} of ${state.assemblies.length} accessions`;

  if (!assemblies.length) {
    setMessage('No matching genome accessions');
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const assembly of assemblies) {
    const row = document.createElement('tr');
    row.append(createAccessionCell(assembly), createPloidyCell(assembly), createDoiCell(assembly));
    fragment.append(row);
  }
  els.body.replaceChildren(fragment);
}

async function init() {
  try {
    const response = await fetch('/api/genome-browser/assemblies', {
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`Genome catalog API returned ${response.status}`);

    const payload = await response.json();
    state.assemblies = (Array.isArray(payload.assemblies) ? payload.assemblies : [])
      .filter((assembly) => assembly && typeof assembly === 'object')
      .sort(sortAssemblies);
    if (!state.assemblies.length) throw new Error('No genome accessions are available');

    els.search.disabled = false;
    els.ploidy.disabled = false;
    renderTable();
  } catch (error) {
    els.status.classList.add('is-error');
    els.status.textContent = 'Genome accessions unavailable';
    setMessage(error instanceof Error ? error.message : 'Genome accessions failed to load', true);
  }
}

els.search.addEventListener('input', renderTable);
els.ploidy.addEventListener('change', renderTable);

init();
window.PotatoAgentExamples.bind('genomes');

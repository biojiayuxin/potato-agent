const CATEGORY_LABELS = {
  all: 'All genomes',
  monoploid: 'Monoploid',
  phased_diploid: 'Phased diploid',
  phased_tetraploid: 'Phased tetraploid',
};

const PREFERRED_ASSEMBLIES = ['monoploid/DMv8.2', 'monoploid/DMv8', 'monoploid/DM'];
const STORAGE_KEY = 'potatoGenomeBrowserAssembly';

const state = {
  manifest: null,
  assemblies: [],
  filteredAssemblies: [],
  selectedAssembly: null,
  root: null,
};

const els = {
  form: document.getElementById('browser-form'),
  category: document.getElementById('category-select'),
  assembly: document.getElementById('assembly-select'),
  location: document.getElementById('location-input'),
  load: document.getElementById('load-button'),
  reset: document.getElementById('reset-location-button'),
  detail: document.getElementById('assembly-detail'),
  title: document.getElementById('browser-title'),
  summary: document.getElementById('browser-summary'),
  empty: document.getElementById('browser-empty'),
  view: document.getElementById('jbrowse-linear-genome-view'),
};

function formatInteger(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '';
  return new Intl.NumberFormat('en-US').format(number);
}

function categoryLabel(category) {
  return CATEGORY_LABELS[category] || category || 'Unknown';
}

function safeAssemblyName(id) {
  return String(id || 'assembly').replace(/[^A-Za-z0-9_.-]+/g, '_');
}

function dataUrl(relativePath) {
  return `/api/genome-browser/data/${String(relativePath || '')
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')}`;
}

function setEmpty(message, isError = false) {
  els.empty.textContent = message;
  els.empty.hidden = false;
  els.empty.classList.toggle('is-error', isError);
}

function hideEmpty() {
  els.empty.hidden = true;
  els.empty.classList.remove('is-error');
}

function setControlsEnabled(enabled) {
  els.category.disabled = !enabled;
  els.assembly.disabled = !enabled;
  els.location.disabled = !enabled;
  els.load.disabled = !enabled;
  els.reset.disabled = !enabled;
}

function assemblySortValue(assembly) {
  const category = assembly.category || '';
  const sample = assembly.sample || assembly.id || '';
  return `${category}\u0000${sample}`;
}

function optionLabel(assembly) {
  const name = assembly.displayName || assembly.sample || assembly.id;
  return `${name} · ${categoryLabel(assembly.category)}`;
}

function countByCategory(assemblies) {
  return assemblies.reduce((counts, assembly) => {
    const category = assembly.category || 'unknown';
    counts[category] = (counts[category] || 0) + 1;
    return counts;
  }, {});
}

function populateCategories() {
  const counts = countByCategory(state.assemblies);
  const ordered = ['all', 'monoploid', 'phased_diploid', 'phased_tetraploid'];
  els.category.replaceChildren();
  for (const category of ordered) {
    if (category !== 'all' && !counts[category]) continue;
    const option = document.createElement('option');
    option.value = category;
    const count = category === 'all' ? state.assemblies.length : counts[category];
    option.textContent = `${categoryLabel(category)} (${formatInteger(count)})`;
    els.category.append(option);
  }
}

function filterAssemblies() {
  const category = els.category.value || 'all';
  state.filteredAssemblies = state.assemblies
    .filter((assembly) => category === 'all' || assembly.category === category)
    .sort((left, right) => assemblySortValue(left).localeCompare(assemblySortValue(right)));

  els.assembly.replaceChildren();
  for (const assembly of state.filteredAssemblies) {
    const option = document.createElement('option');
    option.value = assembly.id;
    option.textContent = optionLabel(assembly);
    els.assembly.append(option);
  }
}

function findAssembly(id) {
  return state.assemblies.find((assembly) => assembly.id === id) || null;
}

function chooseInitialAssembly() {
  const params = new URLSearchParams(window.location.search);
  const requested = params.get('assembly') || params.get('id');
  if (requested && findAssembly(requested)) return requested;

  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored && findAssembly(stored)) return stored;

  for (const id of PREFERRED_ASSEMBLIES) {
    if (findAssembly(id)) return id;
  }

  const dmv8 = state.assemblies.find((assembly) => String(assembly.sample || '').startsWith('DMv8'));
  if (dmv8) return dmv8.id;

  return state.assemblies[0]?.id || '';
}

function categoryForAssembly(id) {
  const assembly = findAssembly(id);
  return assembly?.category || 'all';
}

function selectAssembly(id) {
  const assembly = findAssembly(id) || state.filteredAssemblies[0] || state.assemblies[0] || null;
  if (!assembly) return null;
  state.selectedAssembly = assembly;
  if (![...els.assembly.options].some((option) => option.value === assembly.id)) {
    els.category.value = categoryForAssembly(assembly.id);
    filterAssemblies();
  }
  els.assembly.value = assembly.id;
  return assembly;
}

function defaultLocationFor(assembly) {
  return assembly.defaultLocation || 'chr01:1..100000';
}

function renderAssemblyDetail(assembly) {
  const rows = [
    ['Genome Type', categoryLabel(assembly.category)],
    ['Sample', assembly.sample || assembly.id],
    ['Reference', `${formatInteger(assembly.refNameCount)} sequences`],
    ['Size', `${formatInteger(assembly.totalBp)} bp`],
    ['Genes', formatInteger(assembly.geneCount)],
    ['Transcripts', formatInteger(assembly.transcriptCount)],
  ];

  if (assembly.doi) rows.push(['DOI', assembly.doi]);
  els.detail.replaceChildren();
  for (const [label, value] of rows) {
    if (!value) continue;
    const row = document.createElement('div');
    const dt = document.createElement('dt');
    const dd = document.createElement('dd');
    dt.textContent = label;
    dd.textContent = value;
    row.append(dt, dd);
    els.detail.append(row);
  }
}

function renderSummary(assembly, location) {
  els.title.textContent = assembly.displayName || assembly.sample || assembly.id;
  els.summary.textContent = `${categoryLabel(assembly.category)} · ${location}`;
  renderAssemblyDetail(assembly);
}

function buildJBrowseConfig(assembly) {
  const assemblyName = safeAssemblyName(assembly.id);
  const geneTrackId = `${assemblyName}-gene_models`;

  return {
    assemblyName,
    assembly: {
      name: assemblyName,
      aliases: [assembly.id, assembly.sample, assembly.displayName].filter(Boolean),
      sequence: {
        type: 'ReferenceSequenceTrack',
        trackId: `${assemblyName}-reference`,
        adapter: {
          type: 'BgzipFastaAdapter',
          fastaLocation: { uri: dataUrl(assembly.reference) },
          faiLocation: { uri: dataUrl(assembly.fai) },
          gziLocation: { uri: dataUrl(assembly.gzi) },
        },
      },
    },
    tracks: [
      {
        type: 'FeatureTrack',
        trackId: geneTrackId,
        name: 'Gene Models',
        assemblyNames: [assemblyName],
        category: ['Annotation'],
        adapter: {
          type: 'Gff3TabixAdapter',
          gffGzLocation: { uri: dataUrl(assembly.annotation) },
          index: {
            location: { uri: dataUrl(assembly.annotationIndex) },
            indexType: 'TBI',
          },
        },
      },
    ],
    defaultSession: {
      name: `${assembly.sample || assembly.id} session`,
      view: {
        id: 'linearGenomeView',
        type: 'LinearGenomeView',
        tracks: [
          {
            type: 'FeatureTrack',
            configuration: geneTrackId,
            displays: [
              {
                type: 'LinearBasicDisplay',
                configuration: `${geneTrackId}-LinearBasicDisplay`,
              },
            ],
          },
        ],
      },
    },
  };
}

function updateUrl(assembly, location) {
  const params = new URLSearchParams(window.location.search);
  params.set('assembly', assembly.id);
  if (location) {
    params.set('loc', location);
  } else {
    params.delete('loc');
  }
  const nextUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState({}, '', nextUrl);
}

function renderBrowser(assembly, location) {
  if (!window.React || !window.ReactDOM || !window.JBrowseReactLinearGenomeView) {
    setEmpty('Genome browser libraries are unavailable.', true);
    return;
  }

  const { createViewState, JBrowseLinearGenomeView } = window.JBrowseReactLinearGenomeView;
  const config = buildJBrowseConfig(assembly);
  const viewState = createViewState({
    assembly: config.assembly,
    tracks: config.tracks,
    defaultSession: config.defaultSession,
    location,
  });

  if (!state.root) {
    state.root = window.ReactDOM.createRoot(els.view);
  }

  state.root.render(
    window.React.createElement(JBrowseLinearGenomeView, {
      key: `${assembly.id}:${location}`,
      viewState,
    }),
  );
  hideEmpty();
}

function loadSelectedAssembly({ useDefaultLocation = false } = {}) {
  const assembly = selectAssembly(els.assembly.value);
  if (!assembly) return;

  const params = new URLSearchParams(window.location.search);
  const requestedLocation = params.get('loc');
  const typedLocation = els.location.value.trim();
  const location = useDefaultLocation
    ? defaultLocationFor(assembly)
    : typedLocation || requestedLocation || defaultLocationFor(assembly);

  els.location.value = location;
  window.localStorage.setItem(STORAGE_KEY, assembly.id);
  renderSummary(assembly, location);
  updateUrl(assembly, location);
  setEmpty('Loading genome browser');
  window.setTimeout(() => renderBrowser(assembly, location), 0);
}

function onCategoryChange() {
  filterAssemblies();
  const assembly = selectAssembly(state.filteredAssemblies[0]?.id);
  if (assembly) {
    els.location.value = defaultLocationFor(assembly);
    renderSummary(assembly, els.location.value);
  }
}

async function init() {
  setControlsEnabled(false);
  try {
    const response = await fetch('/api/genome-browser/assemblies', { headers: { Accept: 'application/json' } });
    if (!response.ok) {
      throw new Error(`Genome browser API returned ${response.status}`);
    }
    state.manifest = await response.json();
    state.assemblies = Array.isArray(state.manifest.assemblies) ? state.manifest.assemblies : [];
    if (!state.assemblies.length) {
      throw new Error('No assemblies are available');
    }

    populateCategories();
    const initialId = chooseInitialAssembly();
    els.category.value = categoryForAssembly(initialId);
    filterAssemblies();
    selectAssembly(initialId);
    const params = new URLSearchParams(window.location.search);
    els.location.value = params.get('loc') || defaultLocationFor(state.selectedAssembly);
    setControlsEnabled(true);
    loadSelectedAssembly();
  } catch (error) {
    setControlsEnabled(false);
    els.title.textContent = 'Genome Browser';
    els.summary.textContent = 'Manifest unavailable';
    els.detail.innerHTML = '<div><dt>Status</dt><dd>Unavailable</dd></div>';
    setEmpty(error instanceof Error ? error.message : 'Genome browser failed to load', true);
  }
}

els.category.addEventListener('change', onCategoryChange);
els.assembly.addEventListener('change', () => {
  const assembly = selectAssembly(els.assembly.value);
  if (assembly) {
    els.location.value = defaultLocationFor(assembly);
    renderSummary(assembly, els.location.value);
  }
});
els.form.addEventListener('submit', (event) => {
  event.preventDefault();
  loadSelectedAssembly();
});
els.reset.addEventListener('click', () => {
  loadSelectedAssembly({ useDefaultLocation: true });
});

init();

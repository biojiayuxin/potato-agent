import {TISSUES} from './potato-efp.mjs?v=20260923-flower';

export const TRANSFORMS = Object.freeze({
  log2_tpm: 'log2(TPM + 1)',
  tpm: 'TPM',
  row_zscore: 'Z-score',
});

// Display order only; diagram matching still uses the explicit aliases below.
const TISSUE_DISPLAY_GROUPS = [
  /flower|floral|anther|carpel|perianth|petal|sepal|pistil|ovary|inflorescence/i,
  /fruit|berry/i,
  /leaf/i,
  /stem/i,
  /root/i,
  /stolon/i,
  /tuber/i,
];

function tissueDisplayGroup(tissue) {
  const index = TISSUE_DISPLAY_GROUPS.findIndex(group => group.test(tissue));
  return index < 0 ? TISSUE_DISPLAY_GROUPS.length : index;
}

// Explicit owner-approved tissue correspondences; no fuzzy matching.
// Flower remains distinct from flower bud.
export const TISSUE_SOURCES = Object.freeze({
  flower: ['flower'], perianth: ['perianth'], flower_bud: ['flower_bud', 'flower bud'],
  fruit: ['fruit'], anther: ['anther'], stem: ['stem'],
  young_leaf: ['young_leaf', 'young leaf'], leaf: ['leaf'],
  mature_leaf: ['mature_leaf', 'mature leaf'], root: ['root'], stolon: ['stolon'],
  stolon_tip_S1: ['stolon_tip_S1', 'stolon_tip', 'stolon tip', 'stolon tip (S1)'],
  swelled_stolon_S2: ['swelled_stolon_S2'],
  young_tuber_S3: ['young_tuber_S3', 'immature small tuber (transection diameter < 1 cm)'],
  young_tuber_S4: ['young_tuber_S4', 'immature big tuber (1 cm <transection diameter < 5 cm)'],
  mature_tuber: ['mature_tuber', 'mature tuber', 'tuber'],
});

export const formatNumber = value => {
  if (value === null || value === undefined) return 'NA';
  return Number(value.toPrecision(4)).toString();
};

export const formatValue = value => value === null || value === undefined
  ? 'NA' : String(Number(value.toFixed(6)));

export function adaptExpression(data) {
  if (data?.scope !== 'tissue' || !Object.hasOwn(TRANSFORMS, data.transform) ||
      data.genes?.length !== 1 || !data.genes[0].geneId || !Array.isArray(data.columns) ||
      data.values?.length !== 1 || data.rawValues?.length !== 1 ||
      !Array.isArray(data.values[0]) || !Array.isArray(data.rawValues[0]) ||
      data.values[0].length !== data.columns.length || data.rawValues[0].length !== data.columns.length) {
    throw new Error('Expected Tissue mean expression for one gene.');
  }
  const allValues = data.values[0];
  for (const [index, value] of allValues.entries()) {
    const raw = data.rawValues[0][index];
    if ((value !== null && (typeof value !== 'number' || !Number.isFinite(value))) ||
        (raw !== null && (typeof raw !== 'number' || !Number.isFinite(raw) || raw < 0)) ||
        (data.transform !== 'row_zscore' && value !== null && value < 0)) {
      throw new Error('The expression response contains an invalid value.');
    }
  }
  const expression = {};
  const rawTpm = {};
  const tissueLabels = {};
  // Omit stamen from the eFP data model itself, including exported metadata.
  // Keep the API's all-atlas transformations and color range unchanged.
  const sourceRows = data.columns.map((column, index) => ({
    tissue: column.tissue || column.label || column.id, index,
  })).filter(row => row.tissue.trim().toLowerCase() !== 'stamen');
  const usedColumns = new Set();
  const mappedIds = new Map();
  for (const tissue of TISSUES) {
    let indices = sourceRows.filter(row => TISSUE_SOURCES[tissue.id].includes(row.tissue)).map(row => row.index);
    if (tissue.id === 'stolon_tip_S1' && indices.length === 0) {
      // Reuse stolon only until a distinct stolon-tip column exists. An explicit
      // null tip value stays NA rather than silently borrowing stolon data.
      indices = sourceRows.filter(row => TISSUE_SOURCES.stolon.includes(row.tissue)).map(row => row.index);
      tissueLabels[tissue.id] = 'Stolon';
    }
    if (indices.length > 1) throw new Error(`Ambiguous tissue mapping: ${tissue.label}.`);
    const index = indices[0];
    if (index !== undefined) {
      usedColumns.add(index);
      if (!mappedIds.has(index)) mappedIds.set(index, []);
      mappedIds.get(index).push(tissue.id);
    }
    rawTpm[tissue.id] = index === undefined ? null : data.rawValues[0][index];
    expression[tissue.id] = index === undefined || rawTpm[tissue.id] === null ? null : allValues[index];
  }
  // Preserve the Gene Expression range across ALL atlas tissues, including
  // those absent from the drawing. Never recalculate z-scores on mapped tissues.
  const valid = allValues.filter(value => value !== null);
  const min = valid.length ? Math.min(...valid) : 0;
  const max = valid.length ? Math.max(...valid) : 0;
  const zscore = data.transform === 'row_zscore';
  const maxAbs = Math.max(1, Math.abs(min), Math.abs(max));
  return {
    payload: {
      gene_id: data.genes[0].geneId, unit: TRANSFORMS[data.transform], expression, tissue_labels: tissueLabels,
      scale: {type: 'linear', min: zscore ? -maxAbs : 0,
        max: zscore ? maxAbs : Math.max(1, max), palette: zscore ? 'diverging' : 'YlOrRd'},
    },
    rawTpm,
    tissueValues: sourceRows.map(({tissue, index}) => ({
      tissue,
      value: allValues[index], rawTpm: data.rawValues[0][index],
      diagramIds: mappedIds.get(index) || [],
    })).sort((left, right) => tissueDisplayGroup(left.tissue) - tissueDisplayGroup(right.tissue)),
    dataset: data.dataset || '',
    transform: data.transform,
    unmappedTissues: sourceRows.filter(row => !usedColumns.has(row.index)).map(row => row.tissue),
  };
}

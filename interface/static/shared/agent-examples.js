(() => {
  'use strict';

  const DEFAULT_GENE = 'DM8.2_chr05G25210';
  const DEFAULT_SPATIAL = {
    genes: ['Soltu.DM.03G024100'],
    dataset: 'Stolon and tuber (s1_s2)',
    sample: 'Stolon (S1)',
  };
  const pages = ['genes', 'bulk_rnaseq', 'wgcna', 'spatial', 'pan_genome', 'genome_browser', 'functional_annotation', 'synteny'];
  const normalizePage = page => page === 'genomes' ? 'pan_genome' : page;

  const build = (page, context = {}) => {
    page = normalizePage(page);
    const fallback = page === 'spatial' ? DEFAULT_SPATIAL.genes : [DEFAULT_GENE];
    const loaded = Array.isArray(context.genes)
      ? [...new Set(context.genes.filter((gene) => typeof gene === 'string' && gene.trim()).map((gene) => gene.trim()))]
      : [];
    const genes = loaded.length ? loaded : fallback;
    const names = genes.join(', ');
    const subject = genes.length > 1 ? `the genes ${names}` : names;
    switch (page) {
      case 'genes':
        return `Summarize the functional annotations, available protein domains, and relevant literature for ${subject}. Create an evidence table with sources, distinguishing reported findings from predictions.`;
      case 'bulk_rnaseq':
        return `Compare the expression of ${subject} across tissues. Generate a heatmap and export the underlying expression values as a TSV file.`;
      case 'wgcna':
        return `Find the top 25 co-expression neighbors of ${genes.length > 1 ? `each of the query genes ${names}` : names} in the tuberization network. Rank them by TOM, add available functional annotations, and generate ${genes.length > 1 ? 'a network plot and candidate table for each query gene' : 'a network plot and candidate table'}.`;
      case 'spatial':
        return `Compare the average expression and percentage of expressing cells for ${subject} across clusters. Generate a dot plot and export the summary table. Dataset: ${context.dataset || DEFAULT_SPATIAL.dataset}; sample: ${context.sample || DEFAULT_SPATIAL.sample}.`;
      case 'pan_genome':
        return 'List the potato genome assemblies available in PotatoOmics, including their accessions, ploidy, and available annotation resources. Export the results as a TSV file.';
      case 'genome_browser':
        return `Export the CDS and protein sequences of ${DEFAULT_GENE} from DMv8.2 as FASTA files. Use the database's default transcript and include a gene-to-transcript mapping table.`;
      case 'functional_annotation':
        return 'Count the genes annotated as ERF transcription factors in the C88 genome.';
      case 'synteny':
        return 'Use the genome-synteny skill (JCVI/MCScan) to perform gene-based chromosome-level synteny analysis for DH_W99 and E86-69 and generate a synteny ribbon plot. Arrange the haplotypes from top to bottom in this order: DH_W99_hap1, DH_W99_hap2, E86-69_hap1, E86-69_hap2, showing syntenic links between adjacent haplotypes.';
      default:
        throw new Error('Unknown research example');
    }
  };
  const takeFromLocation = () => {
    const hash = window.location.hash;
    if (!hash.startsWith('#example=')) return null;
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
    try {
      const value = JSON.parse(decodeURIComponent(hash.slice('#example='.length)));
      const page = normalizePage(value?.page);
      return value && pages.includes(page) && typeof value.text === 'string'
        && value.text.trim() ? { ...value, page } : null;
    } catch { return null; }
  };
  const bind = (page, getContext = () => ({})) => {
    page = normalizePage(page);
    const button = document.getElementById('ask-potato-agent');
    button?.addEventListener('click', () => {
      const id = crypto.randomUUID?.()
        || Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
      const intent = { id, page, text: build(page, getContext()) };
      window.location.assign(`/chat#example=${encodeURIComponent(JSON.stringify(intent))}`);
    });
  };

  window.PotatoAgentExamples = {
    build, bind, takeFromLocation,
  };
})();

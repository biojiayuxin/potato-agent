---
name: search-gene-function
description: Build an English/ASCII gene-function evidence table for potato candidate genes. The workflow fixes the potato gene ID and protein sequence, searches potato evidence using gene names before locus IDs, runs cross-species DIAMOND/RBH against Arabidopsis, rice, and maize, and outputs one integrated TSV/CSV.
version: 1.2.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, candidate-gene, gene-function, ortholog, literature, Arabidopsis, rice, maize]
    related_skills: [potato-gene-search, potato-knowledge-search, arabidopsis-gene-search, rice-gene-search, maize-gene-search, literature-review, ortholog-finder, slurm-for-long-running-tasks]
prerequisites:
  commands: [python3]
---

# Search Gene Function

## Purpose

Use this skill when a user provides potato candidate gene IDs (DMv8/DMv6/E4-63/PGSC/Soltu) and wants function, potato literature evidence, and Arabidopsis/rice/maize homolog evidence.

Final user-facing files must be **English/ASCII** to avoid encoding problems. Raw API/RAG/literature outputs may keep original Unicode as traceable intermediate files.

## Inputs and variables

Set these variables before running commands:

```bash
GENE=<potato_gene_id>
PUBLIC_DATA_DIR=${PUBLIC_DATA_DIR:-/mnt/data/public_data}
WORK_BASE=${WORK_BASE:-${HOME}/work}
WORK=${WORK_BASE}/search_gene_function_${GENE}
mkdir -p "$WORK"
```

Load related skills with `skill_view` and set:

```bash
POTATO_GENE_SKILL_DIR=<skill_dir from potato-gene-search>
POTATO_RAG_SKILL_DIR=<skill_dir from potato-knowledge-search>
LIT_SKILL_DIR=<skill_dir from literature-review>
AT_SKILL_DIR=<skill_dir from arabidopsis-gene-search>
DIAMOND=${DIAMOND:-$(command -v diamond || true)}
```

Abort if `DIAMOND` is empty and the task requires cross-species BLAST/RBH.

## Workflow

### 1. Record run context

```bash
{
  echo -e "key\tvalue"
  echo -e "gene\t${GENE}"
  echo -e "work\t${WORK}"
  echo -e "public_data_dir\t${PUBLIC_DATA_DIR}"
  echo -e "diamond\t${DIAMOND}"
  [ -n "$DIAMOND" ] && echo -e "diamond_version\t$($DIAMOND version 2>/dev/null | head -1)"
  date '+run_time\t%F %T %z'
} > "$WORK/00_run_context.tsv"
```

### 2. Query potato gene details and fix the peptide sequence

```bash
python3 "$POTATO_GENE_SKILL_DIR/scripts/query_potato_gene.py" details "$GENE" \
  --include-sequences --sequence-fields pep \
  > "$WORK/01_potato_gene_details.json"

python3 - <<'PY'
import json, os, sys
work = os.environ['WORK']
data = json.load(open(f'{work}/01_potato_gene_details.json'))
pep = data.get('pep')
if not pep:
    sys.exit('ERROR: no peptide sequence returned')
open(f'{work}/query.pep.fa', 'w').write(pep.strip() + '\n')
seq = ''.join(x.strip() for x in pep.splitlines() if not x.startswith('>'))
with open(f'{work}/01_query_sequence_summary.tsv', 'w') as out:
    out.write('field\tvalue\n')
    for k in ['gene_id','ID','transID_repre','symbols','symbol','gene_symbol','ID_reported','coordinates','domain']:
        out.write(f'{k}\t{data.get(k, "")}\n')
    out.write(f'pep_header\t{pep.splitlines()[0]}\n')
    out.write(f'pep_length\t{len(seq)}\n')
PY
```

Use `query.pep.fa` for all downstream similarity searches.

### 3. Check potato reference-version mapping

Use local DMv8.1/DMv8.2 peptide FASTA if available:

```bash
DMV81_PEP="$PUBLIC_DATA_DIR/Genomes/DMv8/DMv8.1.pep.fa"
DMV82_PEP="$PUBLIC_DATA_DIR/Genomes/DMv8/DMv8.2.pep.fa"

for name in dmv81 dmv82; do
  case "$name" in
    dmv81) fa="$DMV81_PEP";;
    dmv82) fa="$DMV82_PEP";;
  esac
  [ -s "$fa" ] || continue
  "$DIAMOND" makedb --quiet --in "$fa" -d "$WORK/$name"
  "$DIAMOND" blastp --quiet --ultra-sensitive \
    -q "$WORK/query.pep.fa" -d "$WORK/$name" \
    -o "$WORK/02_query_vs_${name}.tsv" \
    -f 6 qseqid sseqid pident length qlen slen evalue bitscore qcovhsp scovhsp \
    --max-target-seqs 20 --evalue 1e-5 --threads ${THREADS:-4}
done
```

Report the best local sequence match; do not infer a version-mapped ID by string replacement.

### 4. Build RAG/literature query terms

Search potato evidence with **gene names first, locus IDs second**. Extract terms from `symbols`, `symbol`, `gene_symbol`, UniProt/product names, then IDs.

```bash
python3 - <<'PY'
import json, os, re
from pathlib import Path
work = Path(os.environ['WORK'])
gene = os.environ['GENE']
data = json.load(open(work / '01_potato_gene_details.json'))
terms = []
seen = set()

def add(term, source, priority):
    term = re.sub(r'\s+', ' ', (term or '').strip())
    if not term or term.lower() in seen:
        return
    seen.add(term.lower())
    terms.append((term, source, priority))

for field in ['symbols', 'symbol', 'gene_symbol']:
    value = data.get(field)
    if isinstance(value, str):
        for t in re.split(r'[,;]', value):
            add(t, field, 1)
            add(f'{t} potato', field, 1)
            add(f'{t} Solanum tuberosum', field, 1)

for item in data.get('ls_uniprot') or []:
    product = str(item).split('\t')[-1]
    product = re.sub(r' n=\d+ Tax=.*$', '', product).strip()
    add(product, 'ls_uniprot', 2)
    add(f'{product} potato', 'ls_uniprot', 2)

add(gene, 'gene_id', 3)
for t in re.split(r'[,;\s]+', data.get('ID_reported') or ''):
    t = re.sub(r'\(.*?\)$', '', t.strip())
    add(t, 'ID_reported', 3)

terms.sort(key=lambda x: (x[2], len(x[0])))
with open(work / '03_query_terms.tsv', 'w') as out:
    out.write('query\tsource\tpriority\n')
    for row in terms:
        out.write('%s\t%s\t%s\n' % row)
PY
```

Run RAG for priority 1-2 terms first; use priority 3 IDs for mapping checks and supplement-table evidence.

```bash
mkdir -p "$WORK/03_rag" "$WORK/08_literature"
python3 - <<'PY'
import os, re, subprocess
from pathlib import Path
work = Path(os.environ['WORK'])
rag = Path(os.environ['POTATO_RAG_SKILL_DIR']) / 'scripts/query_potato_rag.py'
for line in open(work / '03_query_terms.tsv'):
    if line.startswith('query\t'):
        continue
    query, source, priority = line.rstrip('\n').split('\t')
    if int(priority) > 3:
        continue
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', query)[:120]
    out = work / '03_rag' / f'{safe}.json'
    subprocess.run(['python3', str(rag), query, '--top-k-retrieve', '80', '--top-k-rerank', '8', '--format', 'json'], stdout=open(out, 'w'), check=False)
PY
```

For broad literature search, query the best gene-name terms first, then the gene ID if needed:

```bash
python3 "$LIT_SKILL_DIR/scripts/lit_search.py" search "<best_gene_name> potato function" --limit 8 --source all \
  > "$WORK/08_literature/<best_gene_name>_potato_function.json"
python3 "$LIT_SKILL_DIR/scripts/lit_search.py" search "$GENE Solanum tuberosum" --limit 5 --source all \
  > "$WORK/08_literature/${GENE}_id_check.json"
```

Assign potato evidence level:

| Level | Meaning |
|---|---|
| A | Direct functional evidence in potato: mutant, overexpression, RNAi/VIGS/CRISPR, interaction, or biochemical assay |
| B | Trait/treatment association: QTL/GWAS, DE, coexpression, candidate gene, eQTL |
| C | Family analysis, annotation table, supplement list only |
| D | No useful potato evidence found |

### 5. Run cross-species DIAMOND forward search

```bash
AT_PEP="$PUBLIC_DATA_DIR/Genomes/Other_species/Arabidopsis/At.repre.pep.fa"
RICE_PEP="$PUBLIC_DATA_DIR/Genomes/Other_species/Rice_Nipponbare/RGAP_MSU7_Nipponbare.repre.pep.fa"
MAIZE_PEP="$PUBLIC_DATA_DIR/Genomes/Other_species/Maize/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.protein.fa"

for name in arabidopsis rice_rgap maize_nam5; do
  case "$name" in
    arabidopsis) fa="$AT_PEP";;
    rice_rgap) fa="$RICE_PEP";;
    maize_nam5) fa="$MAIZE_PEP";;
  esac
  [ -s "$fa" ] || continue
  "$DIAMOND" makedb --quiet --in "$fa" -d "$WORK/$name"
  "$DIAMOND" blastp --quiet --ultra-sensitive \
    -q "$WORK/query.pep.fa" -d "$WORK/$name" \
    -o "$WORK/05_forward_${name}.top20.tsv" \
    -f 6 qseqid sseqid pident length qlen slen evalue bitscore qcovhsp scovhsp \
    --max-target-seqs 20 --evalue 1e-5 --threads ${THREADS:-4}
done
```

### 6. Run reverse BLAST and summarize RBH

Extract top hits for each species, BLAST them back to the potato protein database, and record each hit's reverse best potato target.

```bash
POTATO_PEP=${POTATO_PEP:-$DMV81_PEP}
"$DIAMOND" makedb --quiet --in "$POTATO_PEP" -d "$WORK/potato_ref"
```

Use any portable FASTA parser to extract the top hit sequences from the subject FASTA, then run:

```bash
"$DIAMOND" blastp --quiet --ultra-sensitive \
  -q "$WORK/rbh_<species>_top20.pep.fa" -d "$WORK/potato_ref" \
  -o "$WORK/06_reverse_<species>.top20.tsv" \
  -f 6 qseqid sseqid pident length qlen slen evalue bitscore qcovhsp scovhsp \
  --max-target-seqs 5 --evalue 1e-5 --threads ${THREADS:-4}
```

Orthology confidence:

| Confidence | Rule |
|---|---|
| High | Strong forward hit, reverse best hit is target gene, good coverage/domain match, and preferably synteny/tree support |
| Medium | Forward hit and reverse best hit support the target, but coverage is limited or paralogs are close |
| Ambiguous | Similar family member; reverse best hit is another potato paralog or several paralogs are close |
| Low | Weak or partial similarity only |

### 7. Annotate top hits

- Arabidopsis: query top AGI IDs with `arabidopsis-gene-search`; record TAIR name/description and PlantConnectome/PMID evidence if needed.
- Rice: DIAMOND output may use RGAP/MSU `LOC_Os...`; use local RGAP GFF for description and RAP-DB only when a reliable mapping is available.
- Maize: use local MaizeGDB NAM5 files for locus symbol/name, GO, InterPro/Pfam, and UniProt annotation.

Keep raw annotation outputs in `07_annotations/`.

## Final outputs

Write one integrated table:

```text
${WORK}/${GENE}_single_table_top10_hits.tsv
${WORK}/${GENE}_single_table_top10_hits.csv
${WORK}/${GENE}_REPORT.md
```

Final table columns:

```text
potato_gene_id
potato_reported_name
potato_reported_ids
potato_domain
potato_function_description
potato_evidence_level
potato_reference_DOI
potato_protein_source_note
hit_species
hit_species_latin
blast_rank_unique_gene
hit_gene_id
hit_protein_or_transcript_id
hit_reported_name
hit_function_description
hit_annotation_source
blast_identity_percent
blast_align_length
blast_query_coverage_percent
blast_subject_coverage_percent
blast_evalue
blast_bitscore
rbh_target
orthology_confidence
analysis_source_note
```

Rules for final files:

- Use English/ASCII text only in final TSV/CSV/REPORT.
- Keep raw Unicode evidence only in intermediate files; cite final evidence by DOI/PMID/path.
- State whether BLAST/DIAMOND results are `newly_generated`, `reused_existing:<path>`, or `mixed`.
- Do not call BLAST top hits strict orthologs unless RBH and other evidence support that conclusion.

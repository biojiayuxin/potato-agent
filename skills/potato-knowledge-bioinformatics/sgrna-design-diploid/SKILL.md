---
name: sgrna-design-diploid
description: Use when designing CRISPR/Cas9 sgRNA targets for heterozygous diploid potato genomes. Confirm real allele/copy count with CDS-to-genome alignment, reuse public CRISPR databases when present, then design and filter allele-aware sgRNAs.
version: 1.1.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, diploid, heterozygous, CRISPR, sgRNA, GMAP, allele-aware, haplotype]
    related_skills: [sgrna-design, gmap-cds-genome-alignment]
---

# Diploid sgRNA Design for Heterozygous Potato Genomes

## Overview

Use this skill to design CRISPR/Cas9 sgRNAs for heterozygous diploid potato genomes with haplotype-resolved assemblies. The central rule is that sgRNA design must be based on the **actual genomic allele/copy situation**, not only on independent gene annotation records.

Haplotype annotations can miss an allele, split an allele into fragments, or annotate paralogs inconsistently. Therefore, before recommending sgRNAs, confirm how many genomic loci are present by aligning the target CDS/cDNA back to the combined haplotype genome.

Core rule:

```text
Do not assume allele count from annotation alone.
First confirm real genomic allele/copy count by CDS-to-genome alignment.
Then decide which sgRNAs are usable.
```

## When to Use

Use this skill when:

- The user asks for CRISPR/Cas9 sgRNA design in a heterozygous diploid potato genome.
- The genome has two haplotypes or haplotype-resolved assemblies.
- The editing goal is to knock out all functional alleles/copies of a gene by default.
- Existing annotation may miss or incorrectly split one allele.
- The design must distinguish intended allele hits from true off-targets.

Do not use this skill for simple haploid/reference-genome sgRNA design. For single-haplotype references, use `sgrna-design`.

When executing this workflow, load and follow these skills as needed:

1. `gmap-cds-genome-alignment` — required for CDS-to-genome alignment and real allele/copy confirmation.
2. `sgrna-design` — required for CRISPOR target design and basic sgRNA scoring/filtering.

## Required Inputs

Confirm or infer these before execution:

1. Target genome/material name, used as `<genome_name>`.
2. Target gene input:
   - gene ID in the target genome, or
   - gene name/symbol, or
   - external reference gene ID if a trusted mapping/search route exists.
3. Editing goal:
   - default: knock out all functional alleles/copies of the target gene;
   - optional: family/multi-copy knockout.
4. Check reusable databases:
   - default: public_data/CRISPR_DB/<genome_name>/

If the user only gives a gene name, first resolve it to a target-genome gene ID before sequence extraction.

## Reusable CRISPR Database Convention

Before creating any new combined genome, GMAP database, or CRISPOR genome database, first check the reusable CRISPR database root relative to the active workspace/home:

```text
public_data/CRISPR_DB/<genome_name>/
```

The generic runtime layout is:

```text
public_data/CRISPR_DB/<genome_name>/
├── gmap/
│   ├── <gmap_db_name>.version
│   ├── <gmap_db_name>.ref*positions
│   └── ... other GMAP index files ...
└── crispor/
    ├── genomeInfo.all.tab
    └── <crispor_genome_id>/
        ├── <crispor_genome_id>.2bit
        ├── <crispor_genome_id>.fa.bwt
        ├── <crispor_genome_id>.gp
        └── ... other CRISPOR/BWA files ...
```

Runtime variables:

```bash
CRISPR_DB_ROOT=${CRISPR_DB_ROOT:-public_data/CRISPR_DB}
CRISPR_DB="$CRISPR_DB_ROOT/<genome_name>"
GMAP_DB_DIR="$CRISPR_DB/gmap"
GMAP_DB_NAME="<gmap_db_name>"
CRISPOR_GENOME_DIR="$CRISPR_DB/crispor"
CRISPOR_GENOME_ID="<crispor_genome_id>"
```

If these DB files exist and smoke tests pass, reuse them directly. Routine sgRNA design does **not** require the original combined FASTA/GFF3 once both the GMAP and CRISPOR databases have been built.

## Execution Workflow

### Step 1 — Check reusable databases or prepare combined haplotype resources

First check whether reusable GMAP/CRISPOR databases already exist under:

```text
public_data/CRISPR_DB/<genome_name>/
```

Check database files, not merely the presence of a directory:

```bash
test -s "$GMAP_DB_DIR/${GMAP_DB_NAME}.version"
compgen -G "$GMAP_DB_DIR/${GMAP_DB_NAME}.ref*positions" >/dev/null
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.2bit"
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.fa.bwt"
test -s "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/genomeInfo.tab"
test -s "$CRISPOR_GENOME_DIR/genomeInfo.all.tab"
```

Only create a new combined FASTA/GFF3 if the reusable database is missing or verification fails and a new DB must be built. The combined FASTA must include both haplotypes, and the combined GFF3 must include annotations from both haplotypes.

When merging haplotype FASTA/GFF files, avoid duplicate sequence IDs.

Use the merged haplotype FASTA/GFF for any new GMAP database, CRISPOR genome database, or sequence extraction.

Before building GMAP and CRISPOR databases, warn the user that indexing may take around 30 minutes. Prefer running long indexing steps in the background or via Slurm, and resume from existing outputs if the session is interrupted.

### Step 2 — Resolve gene name to target-genome gene ID

If the user provides a gene name/symbol rather than a target-genome gene ID:

1. Search target-genome annotation attributes, local mapping tables, or trusted potato gene databases to identify candidate gene IDs.
2. If multiple candidates exist, report the ambiguity and ask the user to choose unless one candidate is clearly supported.
3. Mapping/annotation routes can identify starting candidate genes, but they are not final evidence for allele/copy count.

If the user provides a target-genome gene ID directly, still run the GMAP confirmation step to check whether a counterpart allele exists elsewhere in the combined genome.

### Step 3 — Extract CDS and confirm real allele/copy count with GMAP

Extract the CDS nucleotide sequence for the starting target gene. If representative CDS FASTA files already exist, use them. If CDS FASTA is not available, use `gffread-export-cds-pep` to generate CDS from genome FASTA + GFF3.

Then align the CDS against the combined haplotype genome. For allele discovery, do not restrict GMAP to one best path. Use enough paths to recover both haplotypes and possible additional copies.

Recommended GMAP reuse command:

```bash
gmap -D "$GMAP_DB_DIR" -d "$GMAP_DB_NAME" -t THREADS \
  -f gff3_gene --gff3-add-separators=0 \
  --npaths=10 --nofails \
  target.cds.fa \
  > target.cds_to_combined_genome.gff3 \
  2> target.gmap.log
```

Only build a new GMAP database if the reusable DB is absent or fails verification. When building a new DB, use the deduplicated combined FASTA and save the DB under a reusable or task-specific `gmap/` directory.

Parse mRNA-level GMAP hits and summarize at least:

- query ID;
- genomic sequence ID;
- start/end/strand;
- coverage;
- identity;
- matches/mismatches/indels if available;
- haplotype inferred from sequence ID or coordinate source;
- overlap with annotated genes if possible.

#### Allele/copy classification from GMAP

Use annotation as supportive evidence, not the sole evidence. Classify the target based on GMAP hits to the combined genome:

| Class | Practical definition | Default design consequence |
|---|---|---|
| `single_locus` | One high-confidence genomic locus only | Design single-locus sgRNA; all other hits are off-targets |
| `biallelic_pair` | One high-confidence locus on hap1 and one on hap2 | Prefer shared sgRNAs that edit both alleles |
| `annotation_missing_allele` | Annotation lists one gene, but GMAP reveals a high-confidence homologous locus on the other haplotype | Treat as biallelic for sgRNA design; mention annotation may be missing/incomplete |
| `fragmented_or_partial_allele` | One allele is partial, fragmented, low coverage, or interrupted | Report uncertainty; design may need allele-specific or paired guide strategy |
| `multi_locus` | More than two high-confidence genomic loci | Do not blindly recommend one guide; clarify whether user wants all copies or specific copies |
| `uncertain` | Low identity/coverage or conflicting hits | Stop and report that allele count is uncertain |

Suggested high-confidence thresholds:

```text
coverage >= 85% and identity >= 90%: candidate allele/copy
coverage >= 95% and identity >= 95%: high-confidence allele/copy
```

For strict functional knockout design, prefer high-confidence hits. For divergent alleles, inspect lower-coverage hits manually before deciding they are absent.

### Step 4 — Design sgRNA candidates

Design sgRNAs using the confirmed target loci and the combined diploid genome as the off-target search background. Use `sgrna-design` for CRISPOR details. Default recommendation: CRISPOR with SpCas9 NGG.

Recommended CRISPOR reuse command:

```bash
crispor --genomeDir "$CRISPOR_GENOME_DIR" "$CRISPOR_GENOME_ID" \
  targets.fa guides.tsv -o offtargets.tsv -p NGG --mm 4
```

If target-region FASTA is needed and the original combined FASTA has been removed, extract it from the CRISPOR `.2bit` file:

```bash
twoBitToFa \
  "$CRISPOR_GENOME_DIR/$CRISPOR_GENOME_ID/$CRISPOR_GENOME_ID.2bit:<seqid>:START-END" \
  target_region.fa
```

Only build a new CRISPOR genome if the reusable one is missing or fails verification. A new CRISPOR genome should be built from the deduplicated combined haplotype FASTA and GFF3:

```bash
crispor-add-genome --baseDir "$CRISPOR_GENOME_DIR" fasta <genome>.hap1_hap2.unique.fa \
  --desc "<crispor_genome_id>|<scientific_name>|<common_name>|<version>" \
  --gff <genome>.hap1_hap2.unique.repre.gff3
```

Candidate target sequences should be extracted from the confirmed target loci, not only from the initially annotated gene interval. If GMAP finds an unannotated allele, include that locus when evaluating whether sgRNAs cover all intended alleles.

Default candidate design modes:

1. **Shared-guide mode** for `biallelic_pair` or `annotation_missing_allele`:
   - Prefer sgRNAs that perfectly match both confirmed alleles, including PAM.
   - The second allele hit is an intended target, not a true off-target.
2. **Paired-guide mode** when no good shared guide exists:
   - Recommend one sgRNA for hap1 locus and one sgRNA for hap2 locus.
   - Evaluate the combined pair as one design set.
3. **Single-locus mode** for `single_locus`:
   - Require no high-risk hits elsewhere in the combined genome.
4. **Allele-specific mode** only if the user explicitly asks:
   - Prefer PAM-disrupting or seed-region differences in the non-target allele.
   - Do not default to allele-specific editing for normal knockout requests.
5. **Multi-locus mode** for `multi_locus`:
   - Ask whether to target all copies, only allelic copies, or specific paralogs.
   - Do not collapse paralogs into off-targets without user confirmation.

### Step 5 — Decide which sgRNAs are usable based on actual genomic alleles

After CRISPOR produces candidate sgRNAs and off-target tables, reinterpret hits using the GMAP-confirmed allele/copy set.

A usable guide or guide set must satisfy the editing goal:

1. It covers all confirmed functional target alleles/copies, or any uncovered allele is explicitly reported as partial/nonfunctional/uncertain.
2. Hits to confirmed intended alleles are counted as `intended_hits`, not as `true_offtargets`.
3. Non-intended genomic hits, especially exonic hits, are minimal.
4. The guide is in CDS/exon or a functionally relevant early coding region when possible.
5. The guide does not contain problematic polyT status such as CRISPOR `GrafEtAlStatus='tt'`.
6. Specificity and efficiency scores are acceptable compared with alternatives.

## Ranking Rules

Default ranking for all-allele knockout:

```text
covers all intended alleles/copies
→ no 0/1/2-mismatch true off-targets
→ fewer exonic true off-targets, preferably 0
→ fewer total true off-targets
→ higher CFD/MIT specificity
→ higher on-target efficiency score
→ better CDS/exon position for frameshift knockout
→ no polyT/GrafEtAlStatus risk
```

For a biallelic target, a guide with two exact intended hits and zero true off-targets is better than a guide with one exact target hit and no off-targets, because the latter fails to knock out both alleles.

### Selecting multiple sgRNAs for one knockout construct

When the user asks for multiple sgRNAs, do not simply take the top N ranked guides. Build the set iteratively:

1. Start with guides that are `GrafOK`, in exon/CDS, and cover all intended alleles/copies.
2. Exclude guides with any non-intended 0/1/2-mismatch true off-targets.
3. Prefer guides with `exonic_offtarget_count = 0`; if fewer than the requested number exist, allow only >=3-mismatch exonic off-targets and state this explicitly.
4. Check target-site spacing/overlap among selected guides. Avoid guides whose 20 bp guide or guide+PAM intervals overlap, or whose guide positions are very close.
5. Write a dedicated `selected_<N>_sgrnas...tsv` with mismatch summaries (`0mm`, `1mm`, `2mm`, `3mm`, `4mm`) and a separate off-target detail TSV for selected guides.

## Required Output

Return a concise, user-facing recommendation. Do not dump all candidates unless requested.

Include:

1. Allele/copy confirmation summary.
2. Recommended usable sgRNAs or sgRNA sets.
3. Short interpretation: whether the gene appears single-locus, biallelic, annotation-missing, multi-copy, or uncertain; why the guides are usable; and any limiting factors.

## Files to Save

For reproducibility, save these files in the task output directory:

```text
target.cds.fa
gmap/cds_to_combined_genome.gff3
gmap/alignment_summary.tsv
crispor/guides.tsv
crispor/offtargets.tsv
recommended_sgrnas.tsv
summary.txt
```

If a new database is built, also save a manifest describing the FASTA/GFF inputs, deduplication rule, GMAP DB name, CRISPOR genome ID, and database location.

Use English filenames and TSV column names to avoid encoding problems.

## Common Pitfalls

1. **Using annotation count as allele count.** Separate haplotype annotations may miss one allele. Always confirm with CDS-to-genome alignment.
2. **Running CRISPOR against only one haplotype.** Off-target search must use the combined haplotype genome.
3. **Counting the other allele as an off-target.** If alignment confirms it as an intended allele, count it as an intended hit.
4. **Assuming absence from GFF means absence from genome.** A locus can be unannotated but present and targetable.
5. **Ignoring fragmented/partial alleles.** A partial hit may indicate annotation error, pseudogenization, assembly issue, or real deletion; report uncertainty.
6. **Recommending a single guide that edits only one allele for a normal knockout request.** For diploid knockout, coverage of all functional alleles is usually required.
7. **Treating paralogs as ordinary off-targets without asking.** Multi-locus hits may be biologically relevant copies; clarify the editing goal.
8. **Merging haplotype FASTA files without deduplicating sequence IDs.** Duplicate seqids can cause `samtools faidx`, GMAP, and CRISPOR to skip or mis-index sequences.
9. **Assuming CRISPOR dependencies are on PATH.** Ensure CRISPOR, BWA, and kentUtils (`faToTwoBit`, `twoBitInfo`, `twoBitToFa`) are available before building or reusing a database.
10. **Rebuilding databases unnecessarily.** Always check `public_data/CRISPR_DB/<genome_name>/` before running long GMAP or CRISPOR indexing jobs.

## Verification Checklist

- [ ] Reusable `public_data/CRISPR_DB/<genome_name>/` database was checked first.
- [ ] GMAP database files exist or were built from a deduplicated combined FASTA.
- [ ] CRISPOR genome files exist or were built from a deduplicated combined FASTA/GFF.
- [ ] Target gene name was resolved to a target-genome gene ID when needed.
- [ ] CDS sequence used for alignment was recorded.
- [ ] Allele/copy class was assigned from alignment evidence, not annotation count alone.
- [ ] sgRNA design used the combined haplotype genome as off-target background.
- [ ] Intended allele hits were separated from true off-targets.
- [ ] Final answer gives a compact list of usable recommended guides or explains why none are safe.

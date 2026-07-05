---
name: promoter-analysis
description: Use when a user provides or asks to analyze a plant promoter sequence/FASTA, gene name, gene ID, transcript ID, or genomic coordinate and needs predicted transcription factor binding sites, TF family types, motif coordinates, PlantPAN 4.0 submission/parsing, redundancy-collapsed TFBS summaries, or reproducible promoter-analysis output files. For gene/coordinate inputs, first resolve and extract the promoter sequence with potato-gene-search and/or genome-sequence-extraction, then run this PlantPAN workflow.
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [plantpan, promoter, TFBS, motif, cis-elements, plant, potato, web-interface]
    related_skills: [genome-sequence-extraction, potato-gene-search, literature-review]
---

# PlantPAN Promoter Analysis

## Overview

Use PlantPAN 4.0 to predict transcription factor binding sites (TFBS), tandem repeats, CpNpG elements, and other promoter features for a user-supplied plant promoter sequence. The expected user-facing result is a concise answer to:

```text
Which TF families/types are predicted?
Where are their binding-site motif cores in the submitted promoter?
Which loci or promoter windows are most useful for biological interpretation?
```

This skill wraps the PlantPAN4 web form in a reproducible command-line workflow.

PlantPAN4 does not require an obvious public JSON API for promoter analysis. The promoter analysis page is a standard HTML form:

```text
GET  https://plantpan.itps.ncku.edu.tw/plantpan4/promoter_analysis.php
POST https://plantpan.itps.ncku.edu.tw/plantpan4/promoter_results.php
```

The POST response contains the results page, an iframe for visualization, and a download link to a text result file. Use this workflow to submit FASTA sequences programmatically, save the raw HTML/downloaded text, parse TFBS hits, and produce concise summaries suitable for biological interpretation.

## When to Use

Use this skill when:

- The user asks to analyze a promoter sequence with PlantPAN / PlantPAN4.
- The user provides a promoter FASTA path and wants predicted cis-elements or TFBS.
- The user gives a gene symbol, gene ID, transcript ID, or chromosome coordinate and asks for promoter TFBS / cis-element analysis.
- You need more comprehensive promoter motif prediction than a small local hand-written motif list.
- You need a reproducible result table from PlantPAN rather than only a browser screenshot.
- You need to compare PlantPAN TFBS hits with downstream editing, deletion, or guide-design intervals.

Do **not** use this skill for:

- Extracting promoter sequences by itself; use `potato-gene-search` and/or `genome-sequence-extraction` first, then return to this skill with the extracted FASTA.
- General gene function lookup; use `potato-gene-search`, `potato-knowledge-search`, or species-specific gene-search skills.
- Claiming regulatory function as proven biology. PlantPAN TFBS are motif predictions and require experimental validation.

## Inputs

Minimum required input:

```text
promoter FASTA file path, raw promoter sequence, gene name/ID, transcript ID, or genomic coordinate
```

Recommended optional inputs:

```text
output directory
query name
species/genome build and annotation source when the input is not already FASTA
promoter definition, e.g. ATG-upstream length or explicit coordinate interval
PlantPAN species mode: allspecies or selected species
selected TFBSspecies[] values if choose=others
whether to include Tandem Repeat / CpNpG outputs
minimum Similar Score for collapsed priority loci
coordinate convention for reporting positions
```

Default choices when the user does not specify:

```text
choose=allspecies
motif=database
mode[]=Tandem
mode[]=CpNpG
score cutoff for collapsed priority loci: 0.85
coordinate convention: 1-based sequence coordinates plus relative_to_ATG = position - (sequence_length + 1), if the FASTA is an ATG-upstream promoter in gene 5'→3' orientation
```

If the user pastes raw sequence text, first write a FASTA file with a short stable header. Remove whitespace and keep IUPAC DNA ambiguity codes; do not reverse-complement unless the user provides strand/orientation context that requires it.

## Resolve Non-FASTA Inputs

If the user gives a gene name, gene ID, transcript ID, or chromosome coordinate instead of a promoter FASTA, resolve the sequence before running PlantPAN:

1. **Potato gene name / symbol / reported ID / DMv8 gene ID**: use `potato-gene-search` to find the best DMv8 gene match, representative transcript, coordinates, strand, and available sequence fields. If multiple candidates are plausible, show the top candidates and ask only when the choice would materially change the promoter sequence.
2. **Transcript ID**: use `potato-gene-search` when it is a potato/DMv8 transcript or use `genome-sequence-extraction` with the relevant transcript/CDS/GFF FASTA resources. Do not silently choose among multiple transcripts for the same gene unless a representative-transcript rule is available.
3. **Gene ID with genome/annotation files**: use `genome-sequence-extraction` to extract the requested promoter or gene-relative window from genome FASTA + GFF/GTF. Follow that skill's ATG-upstream and strand-aware 5' -> 3' orientation rules.
4. **Chromosome coordinate**: use `genome-sequence-extraction` to extract the interval. Use the user-specified strand; if no strand is specified, default to `+` and report that choice. Convert BED-like intervals to 1-based closed coordinates before extraction.
5. **Missing genome build, annotation, promoter length, or strand**: make the smallest safe assumption only when it is conventional and report it. Otherwise ask for the missing genome/annotation or promoter definition before submitting to PlantPAN.

Record the extraction provenance in the final answer: original user input, resolved gene/transcript/coordinate, promoter length or interval, strand/orientation, extraction output FASTA, and any clipping/fallback rule. Then set `PROMOTER_FA` to the extracted FASTA and continue with the quick workflow below.

## PlantPAN4 Form Fields

The current PlantPAN4 promoter page uses these fields:

| Field | Value / Meaning |
|---|---|
| `sequence` | FASTA text, including header and sequence |
| `motif` | `database` for PlantPAN database; `custom` for user motif mode |
| `choose` | `allspecies` or `others` |
| `TFBSspecies[]` | Used only when `choose=others`; e.g. `Arabidopsis_thaliana`, `Oryza_sativa`, `Zea_mays` |
| `motif_seq` | Custom motif text when `motif=custom`; keep empty for database mode |
| `mode[]` | Optional promoter elements; typically `Tandem` and `CpNpG` |

Known species checkbox values on the page include:

```text
Arabidopsis_thaliana
Brachypodium_distachyon
Chlamydomonas_reinhardtii
Glycine_max
Malus_domestica
Oryza_sativa
Populus_trichocarpa
Sorghum_bicolor
Volvox_carteri
Zea_mays
Physcomitrella_patens
```

## Core File

This skill includes one reusable runtime helper:

```text
scripts/plantpan4_promoter_submit_parse.py    # stdlib-only submit/download/parse helper
```

Prefer this script for PlantPAN submission, result download, TFBS parsing, and TSV/manifest generation.

## Quick Workflow

### 1. Prepare paths

Normalize the input to a FASTA file and use an English output directory and filenames to avoid encoding problems:

```bash
PROMOTER_FA=/path/to/promoter.fa
OUTDIR=/path/to/results/plantpan4_promoter_analysis
mkdir -p "$OUTDIR"
```

### 2. Submit to PlantPAN4 and save raw files

Use the helper script from this skill when available:

```bash
python3 "$SKILL_DIR/scripts/plantpan4_promoter_submit_parse.py" \
  --fasta "$PROMOTER_FA" \
  --outdir "$OUTDIR" \
  --choose allspecies \
  --mode Tandem --mode CpNpG \
  --score-cutoff 0.85
```

Expected primary outputs:

```text
plantpan4_promoter_analysis_page.html
plantpan4_results.html
plantpan4_download.txt
plantpan4_tfbs_hits.tsv
plantpan4_tfbs_family_summary.tsv
plantpan4_priority_tfbs_hits.tsv
plantpan4_priority_tfbs_collapsed_loci.tsv
plantpan4_priority_300bp_windows.tsv
plantpan4_manifest.json
```

If the PlantPAN page changes, inspect the saved raw HTML and update the helper script rather than adding ad hoc one-off parsing.

### 3. Parse existing PlantPAN output without resubmitting

If a previous run already saved PlantPAN output, avoid resubmitting to the web server and use parse-only mode:

```bash
# Parse an existing downloaded PlantPAN TSV/text file
python3 "$SKILL_DIR/scripts/plantpan4_promoter_submit_parse.py" \
  --fasta "$PROMOTER_FA" \
  --outdir "$OUTDIR" \
  --parse-only-download /path/to/plantpan4_download.txt

# Or parse an existing result HTML and try to re-download the linked text file
python3 "$SKILL_DIR/scripts/plantpan4_promoter_submit_parse.py" \
  --fasta "$PROMOTER_FA" \
  --outdir "$OUTDIR" \
  --parse-only-html /path/to/plantpan4_results.html
```

### 4. Run selected-species mode

For less redundant output, use `choose=others` and one or more species values:

```bash
python3 "$SKILL_DIR/scripts/plantpan4_promoter_submit_parse.py" \
  --fasta "$PROMOTER_FA" \
  --outdir "$OUTDIR" \
  --choose others \
  --species Arabidopsis_thaliana \
  --species Oryza_sativa \
  --mode Tandem --mode CpNpG \
  --score-cutoff 0.85
```

## Parsing the Downloaded Result

PlantPAN4 downloaded text is a TSV-like file with columns similar to:

```text
Matrix ID	TF Family	TF ID or Motif Name	Position	Hit Sequence	Strand	Similar Score
```

Important parsing rules:

1. `Position` is a 1-based start position in the submitted promoter sequence.
2. `Hit Sequence` contains lowercase flanking bases and uppercase motif core bases, e.g. `cgGTCAAt`.
3. For biological interpretation, compute the motif core interval from uppercase letters:

```text
core_start = Position + index_of_first_uppercase_base
core_end   = Position + index_of_last_uppercase_base
```

4. If the promoter FASTA is exactly ATG-upstream and oriented gene 5'→3', report:

```text
relative_core_start_to_ATG = core_start - (sequence_length + 1)
relative_core_end_to_ATG   = core_end   - (sequence_length + 1)
```

For a 3000 bp promoter, sequence position 3000 corresponds to `-1`, and position 1 corresponds to `-3000`.

## Recommended Post-processing

PlantPAN all-species output is very redundant because many TF matrices from related TF families hit the same short sequence. Always provide both raw and reduced outputs.

### Raw hit table

Keep one row per PlantPAN hit:

```text
matrix_id
tf_family
tf_id_or_motif_name
position_1based
hit_sequence
strand
similar_score
full_start_1based
full_end_1based
relative_full_start_to_ATG
relative_full_end_to_ATG
core_start_1based
core_end_1based
relative_core_start_to_ATG
relative_core_end_to_ATG
core_sequence
```

### Family summary

Count hits by TF family term after splitting `TF Family` on semicolons:

```text
family_term
hit_count
unique_matrix_count
mean_score
max_score
```

### Collapsed loci table

For interpretation, collapse redundant matrices by:

```text
family_term + core_start_1based + core_end_1based + core_sequence
```

Retain:

```text
max_score
n_raw_hits
core_start_1based
core_end_1based
relative_core_start_to_ATG
relative_core_end_to_ATG
matrix_ids
tf_ids
```

The helper script filters this collapsed loci table with `--score-cutoff` using
PlantPAN `Similar Score`. The default is `0.85`. Increase it for stricter,
higher-confidence motif cores; decrease it for exploratory analysis. Raw hit,
family summary, and priority-hit tables are still written before this cutoff is
applied.

This table is usually the best basis for a human-readable summary because it keeps one row per family-specific motif locus instead of one row per redundant PlantPAN matrix hit.

### Priority families

For plant promoter interpretation, prioritize families such as:

```text
MADS, WRKY, bZIP, NAC/NAM, MYB/Myb/SANT, Dof, AP2/ERF, B3/ARF, bHLH, HD-ZIP, TCP, GATA, Trihelix, Homeodomain, SBP, GRAS, G2-like
```

Avoid over-interpreting very short AT-rich hits in AT-rich promoters. Summarize dense clusters rather than listing every hit.

### Sliding windows

For long promoters, compute 300 bp or 500 bp windows and report TFBS density:

```text
window_start_1based
window_end_1based
relative_window
priority_tfbs_hits
families
```

This helps identify candidate regulatory modules.

## Comparing PlantPAN Hits to User-defined Regions

If the user gives candidate regions, deletion intervals, sgRNA intervals, or windows, intersect PlantPAN core intervals with those regions.

Use overlap logic:

```python
not (tfbs_core_end < region_start or tfbs_core_start > region_end)
```

For guide or local editing intervals, also report nearby hits within ±20 bp:

```python
not (tfbs_core_end < guide_start - 20 or tfbs_core_start > guide_end + 20)
```

Recommended output columns:

```text
region_label
relative_interval
collapsed_priority_loci
unique_family_terms
top_family_loci_counts
representative_high_score_loci
```

## Interpretation Rules

1. **Direct answer first.** Give the most supported TFBS clusters and files produced.
2. **Separate prediction from proof.** Say “PlantPAN predicts / supports” rather than “this TF binds” unless there is experimental evidence.
3. **Report redundancy.** For all-species analysis, a single short motif can produce many matrix hits. Use collapsed loci for conclusions.
4. **Emphasize families and clusters.** Individual matrix IDs are useful for traceability but are rarely the final biological message.
5. **Treat short AT-rich motifs cautiously.** Dof, Homeodomain, HD-ZIP, AT-Hook, and similar short motifs can be frequent in AT-rich promoters.
6. **Use species-selected mode when appropriate.** If the user wants less redundant results, rerun with `choose=others` and selected species such as Arabidopsis, rice, or maize.
7. **Keep raw outputs.** Never discard raw HTML/downloaded TSV; PlantPAN temporary links may expire.

## Final Answer Requirements

When reporting results to the user, include:

```text
query name and sequence length
input provenance: direct FASTA/raw sequence or extracted from gene/transcript/coordinate
resolved gene/transcript/coordinate and extraction rule when applicable
PlantPAN mode, species selection, and score cutoff
top TF family terms from plantpan4_tfbs_family_summary.tsv
representative high-confidence loci from plantpan4_priority_tfbs_collapsed_loci.tsv
coordinates as submitted-sequence 1-based core_start..core_end
relative_to_ATG coordinates only when the FASTA orientation supports them
paths to raw hits, family summary, collapsed loci, windows, and manifest
```

Do not make the user infer the answer only from file paths. Provide at least a small table or bullet list of the dominant TF families and representative binding-site positions.

## Troubleshooting

### PlantPAN returns a large HTML page but no download file

Search the HTML for:

```text
download_promoter.php?file_promoter=...
file_basic_name=...
label_promoter.php?file_basic_name=...
```

Save the HTML anyway. Results can often still be parsed from checkbox values or hidden tables in the HTML.

### Timeout or transient network failure

Retry once with a normal browser-like `User-Agent` and `Referer`. Do not repeatedly hammer the server.

### The result is overwhelmingly large

Use collapsed loci and family/window summaries. For all-species mode, thousands of raw rows for a 3 kb promoter are normal.

### Coordinates look reversed

PlantPAN coordinates are based on the submitted FASTA orientation. Only use `relative_to_ATG` if the sequence was submitted in gene 5'→3' upstream orientation. If the promoter came directly from genomic coordinates on the minus strand and was not reverse-complemented, relative coordinates may be misleading.

## Common Pitfalls

1. **Only using the default Arabidopsis checkbox.** The PlantPAN form defaults to `choose=others` with Arabidopsis checked. For broad discovery, use `choose=allspecies`; for less redundancy, explicitly choose species.
2. **Treating all raw hits as independent evidence.** Many matrices represent related TFs and the same short core. Collapse by family and coordinate.
3. **Ignoring uppercase core bases.** The full `Hit Sequence` includes flanking lowercase bases; use uppercase letters for motif core coordinates.
4. **Assuming PlantPAN coordinates are relative to ATG.** They are 1-based positions in the submitted sequence; convert explicitly.
5. **Losing temporary results.** Download and save raw HTML and text immediately.
6. **Over-claiming function.** TFBS prediction suggests candidate regulation, not actual TF binding or expression effect.
7. **Not recording POST parameters.** Save a manifest with URL, form fields, sequence length, query name, and run time.

## Verification Checklist

- [ ] Input FASTA exists and contains exactly the intended promoter sequence.
- [ ] If the user provided a gene name/ID/transcript/coordinate, the promoter FASTA was first obtained with `potato-gene-search` and/or `genome-sequence-extraction`.
- [ ] Extraction provenance, promoter definition, strand/orientation, and any clipping/fallback rule are recorded.
- [ ] Submitted FASTA header and sequence were saved in the output directory.
- [ ] PlantPAN result HTML was saved.
- [ ] Downloaded PlantPAN text result was saved, or the missing download link was reported.
- [ ] Parsed TFBS table contains expected columns and nonzero rows.
- [ ] Family summary and collapsed loci table were generated.
- [ ] Coordinates are reported both as PlantPAN 1-based positions and, when appropriate, relative to ATG.
- [ ] Final interpretation is based on collapsed loci/clusters, not raw row counts alone.
- [ ] Raw files and manifest paths are included in the final reply.

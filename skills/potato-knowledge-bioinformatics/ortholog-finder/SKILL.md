---
name: ortholog-finder
description: Anchor-first ortholog discovery between two genomes with MCScan/jcvi and LAST. Use when an agent needs a reusable Snakemake workflow that converts GFF3 to BED, checks BED/FASTA ID consistency, runs jcvi.compara.catalog ortholog, resolves query genes to subject ortholog candidates from .anchors first, optionally falls back to .last.filtered, and handles gene IDs that differ from transcript/protein FASTA IDs.
---

# Ortholog Finder

Use this skill to find orthologous genes between two genomes from GFF3 annotation and protein or CDS FASTA files. The workflow uses MCScan/jcvi as the alignment and synteny engine, prefers `.anchors` evidence, and can fall back to `.last.filtered` hits when a query gene has no anchor-supported ortholog.

## Workflow

| Step | Output |
|---|---|
| Convert each GFF3 to BED | `work/<prefix>.bed` |
| Link each FASTA into jcvi naming format | `work/<prefix>.pep` or `work/<prefix>.cds` |
| Check BED column 4 IDs against FASTA headers | `results/id_check.tsv` |
| Run `python -m jcvi.compara.catalog ortholog` | `results/<A>.<B>.last`, `.last.filtered`, `.anchors`, `.lifted.anchors` |
| Extract query gene IDs and gene-to-transcript mappings | `results/gene_ids.txt`, `results/gene_to_transcript.tsv` |
| Map query genes to subject ortholog candidates | `results/final_mapped_gene_anchor.txt` |
| Optionally remove suffixes from the subject column | `results/final_mapped_gene_anchor.no_suffix.txt` |

## Template Setup

Copy the template workflow and scripts into a run directory:

```bash
SKILL_DIR="${SKILL_DIR:?set SKILL_DIR to the ortholog-finder skill directory}"
WORK="${WORK:-$PWD/ortholog_finder_run}"
mkdir -p "$WORK"
cp "$SKILL_DIR/templates/Snakefile" "$WORK/Snakefile"
cp "$SKILL_DIR/templates/config.yaml" "$WORK/config.yaml"
mkdir -p "$WORK/scripts"
cp "$SKILL_DIR"/scripts/*.py "$WORK/scripts/"
```

Edit `config.yaml` before running. Keep `scripts_dir: scripts` if the scripts were copied into the run directory.

## Configuration

Set both genomes under `species`. Use paths accessible to the user or shared paths under `public_data` when appropriate.

```yaml
env_prefix: ""          # optional; if empty, tools must be on PATH
scripts_dir: "scripts" # relative to the run directory
species:
  a:
    prefix: "genomeA"
    gff3: "data/genomeA.gff3"
    fasta: "data/genomeA.pep.fa"
    gff_type: "mRNA"
    gff_key: "ID"
  b:
    prefix: "genomeB"
    gff3: "data/genomeB.gff3"
    fasta: "data/genomeB.pep.fa"
    gff_type: "mRNA"
    gff_key: "ID"
mcscan:
  dbtype: "prot"        # "prot" creates .pep links; "nucl" creates .cds links
  align_soft: "last"
  cscore: 0.7
  cpus: 16
anchor_mapping:
  fallback_enabled: true
  fallback_min_identity: 90.0
```

Use `gene_id.transcript_types` if transcript features are named something other than `mRNA`. Use `no_suffix.enabled: false` when transcript suffix removal is not desired.

## Run

The environment must contain Snakemake, jcvi, Python, and the selected aligner. For the default configuration, install LAST as well.

```bash
cd "$WORK"
snakemake -n --cores 1
snakemake --cores 16 --printshellcmds --rerun-incomplete
```

If `env_prefix` is set in `config.yaml`, the workflow prepends `<env_prefix>/bin` to `PATH`.

## Mapping Logic

- `.anchors` is the primary evidence source. The mapper keeps the best scored subject per raw query, then the best scored query per raw subject.
- `.last.filtered` fallback is per raw query, first filters hits by `anchor_mapping.fallback_min_identity` (default `90.0` percent identity), then chooses the highest bitscore. If bitscore ties, keep the earlier `.last.filtered` row.
- Query gene output remains gene-level. Matching to MCScan raw query IDs uses `results/gene_to_transcript.tsv`, so GFF3s where gene IDs and mRNA/protein IDs differ are supported.
- The no-suffix output only changes the configured output column, by default the subject ortholog column.

## Scripts

- `scripts/check_bed_fasta_ids.py`: compare BED IDs with FASTA headers.
- `scripts/extract_gene_ids.py`: extract query gene IDs and gene-to-transcript rows from GFF3.
- `scripts/map_anchor_orthologs.py`: build the anchor-first ortholog table.
- `scripts/remove_mapping_suffix.py`: remove transcript-style suffixes from a selected output column.

Read or patch these scripts directly when custom parsing behavior is needed.

## Validate Results

Check:

```bash
head results/id_check.tsv
head results/final_mapped_gene_anchor.txt
cat logs/final_mapped_gene_anchor.log
```

Report the number of total query genes, anchor-supported orthologs, fallback orthologs, and unmapped genes. If the result is unexpectedly sparse, first inspect `results/id_check.tsv`, FASTA type versus `mcscan.dbtype`, and whether `mcscan.cscore` is too strict.
Also report `fallback_min_identity`, `fallback_rows_below_min_identity`, and `fallback_priority` from `logs/final_mapped_gene_anchor.log` when fallback contributes hits or expected hits are missing.

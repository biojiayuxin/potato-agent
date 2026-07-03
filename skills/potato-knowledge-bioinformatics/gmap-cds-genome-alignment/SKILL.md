---
name: gmap-cds-genome-alignment
description: Align CDS/cDNA nucleotide FASTA to a genome with GMAP using scripts/gmap_cds_to_genome.py; output GMAP GFF3 plus genomic locations with coverage and identity.
license: MIT
metadata:
  hermes:
    tags: [bioinformatics, gmap, cds, genome, gff3, alignment]
    related_skills: [gffread-export-cds-pep]
---

# GMAP CDS-to-Genome Alignment

Use `scripts/gmap_cds_to_genome.py` to align CDS/cDNA nucleotide FASTA to a genome FASTA with GMAP. The script builds or reuses a GMAP database, runs GMAP, parses `mRNA` rows from the GFF3 output, and summarizes each genomic location with coverage and identity.

Requires `gmap` and `gmap_build` on `PATH`.

## Usage

```bash
python3 scripts/gmap_cds_to_genome.py \
  --genome genome.fa \
  --cds cds.fa \
  --outdir gmap_cds_result \
  --db-name genome_gmap \
  --threads 8 \
  --npaths 1
```

Use `--npaths 1` for a best-hit location. Use `--npaths 10` for allele/copy discovery or hap1+hap2 diploid confirmation.

## Outputs

- `result/cds_to_genome.gff3`: GMAP GFF3 output.
- `result/alignment_summary.tsv`: one row per aligned `mRNA` location with `query_id`, `seqid`, `start`, `end`, `strand`, `coverage_percent`, and `identity_percent`.
- `result/alignment_summary.txt`: text summary of how many locations were found and each location's coverage/identity.
- `result/gmap_build.log` and `result/gmap.gff3.log`: logs.

Report the number of genomic locations and list each location with coverage and identity. If needed, inspect the GFF3 output file directly.

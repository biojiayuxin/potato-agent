---
name: genome-sequence-extraction
description: Use when a user needs to download the complete configured genome FASTA and GFF3 for a Potato Agent Genome Browser assembly, or deterministically extract strand-aware regions, genes, promoters, transcripts, CDS, or proteins from potato and other plant assemblies. Supports readable local resources and the public Interface API without requiring a Potato Agent account.
version: 1.0.0
author: Potato Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [genome, fasta, gff3, sequence-extraction, promoter, cds, protein, potato, bioinformatics]
    related_skills: [potato-gene-search, promoter-analysis, gffread-export-cds-pep]
prerequisites:
  commands: [python3]
---

# Genome Sequence Extraction

## Overview

Use the bundled scripts to perform two distinct tasks:

1. Download the complete genome FASTA and annotation GFF3 configured for one Potato Agent Genome Browser assembly.
2. Extract genomic intervals or annotation-defined gene, promoter, transcript, CDS, and protein sequences.

The scripts are deterministic and do not call an LLM. Hermes Agent chooses explicit parameters, runs the script, checks its structured output, and reports the generated files to the user.

## When to use

Use this skill when the user asks for any of the following:

- the complete configured genome and GFF3 for a Genome Browser assembly;
- an arbitrary genomic interval by 1-based coordinates;
- a gene span or strand-aware gene-relative window;
- a promoter immediately upstream of the CDS 5-prime/ATG coordinate;
- a spliced transcript, CDS, or translated protein by gene/transcript ID;
- resource discovery before selecting an assembly.

Do not use this skill for de novo genome annotation, similarity search, variant calling, sequence alignment, or extracting records from a standalone derived FASTA when no genome coordinates are involved. For records already present in `cds.fa`, `pep.fa`, or transcript FASTA, use `scripts/extract_fasta_records.py` as described below.

## Hermes execution rules

Hermes replaces `${HERMES_SKILL_DIR}` with the installed skill directory. Always use that absolute path; do not assume the current directory is the skill directory:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" --help
```

Write outputs into the current user's working directory or another directory requested by the user. Never write generated data into `${HERMES_SKILL_DIR}`, `/srv`, the shared Genome Browser database, another user's home, or a hard-coded account path.

Use `--source auto` unless the user explicitly requires local-only or API-only operation:

- On Potato Agent, `auto` uses a readable local configured assembly when it is complete.
- On another machine, or when local resources are unavailable, `auto` uses the public API at `https://potato-agent.ynnu.edu.cn`.
- `--source local` prohibits network fallback.
- `--source api` requires the public API and cannot be combined with local resource arguments.

The public API does not require a Potato Agent account. Use `--api-base-url` only to test another deployment or mirror; do not persist a private Interface IP or assume localhost. For API schema and validation behavior, read [references/interface-api.md](references/interface-api.md) when downloading, debugging API integration, or changing request code.

## Discover assemblies

When the assembly name is uncertain, list available resources before acting:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode list-resources \
  --source auto
```

Force the public catalog when checking what remote users can access:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode list-resources \
  --source api
```

Pass a unique assembly ID, sample, or display name to `--assembly`. Prefer the full assembly ID returned by the listing, such as `monoploid/DMv8.2`. Do not guess punctuation or silently choose among ambiguous matches.

## Download complete configured files

Use `--mode download` when the user asks for a complete genome, complete GFF/GFF3, reference files, or the assembly dataset. This mode downloads or locally copies both configured files:

- complete BGZF-compressed genome FASTA (`*.fa.bgz`);
- complete sorted BGZF-compressed annotation GFF3 (`*.gff3.bgz`).

These are the files actually configured in `Genome_browser_DB`, not the original upstream byte-for-byte source files. The GFF3 may have been sorted and normalized during database import. This mode does not download `.fai`, `.gzi`, `.tbi`, or chromosome-size indexes.

If the user does not specify a destination, choose a descriptive subdirectory in the current working directory and report its absolute path. Example:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode download \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --output-dir ./DMv8.2_genome_resources
```

The command prints a JSON summary containing assembly, backend, destination paths, byte counts, and SHA-256 hashes. Confirm that it reports exactly two files and return those paths and sizes to the user. Do not read a downloaded genome or GFF3 into the model context.

Downloads are staged and published atomically. Existing targets are rejected by default. Use `--overwrite` only when the user explicitly asks to replace those files or has confirmed replacement after seeing the conflict. Never work around a collision by deleting unrelated files.

Do not emulate a complete download by issuing many `--mode region` calls. The 1 Mb sequence API is for extraction, while download mode streams the configured files without that sequence limit.

## Extract sequences

All CLI and API coordinates are 1-based inclusive. Convert BED intervals before calling the script:

```text
start_1based = bed_start_0based + 1
end_1based = bed_end_exclusive
```

Choose one extraction mode:

| Mode | Input | Result |
|---|---|---|
| `region` | seqid/start/end/strand or regions TSV | Arbitrary genomic interval |
| `gene` | Gene or transcript ID | Complete parent gene span |
| `gene-window` | ID plus upstream/downstream | Strand-aware gene-relative window |
| `promoter` | ID plus length | Bases immediately upstream of the CDS 5-prime/ATG coordinate |
| `transcript` | Gene or transcript ID | Exons spliced in transcript order |
| `cds` | Gene or transcript ID | CDS segments spliced in transcript order |
| `protein` | Gene or transcript ID | Spliced CDS translated with the standard nuclear code |

Always set `--output` and `--report` for extraction modes. Put both in the user's working directory.

Extract an arbitrary interval:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode region \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --seqid chr01 \
  --start 10000 \
  --end 12000 \
  --strand + \
  --name chr01_10000_12000 \
  --output ./region.fa \
  --report ./region.report.tsv
```

For batch regions, provide a tab-delimited file with `name`, `seqid`, `start`, and `end`; optionally include `strand`:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode region \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --regions ./regions.tsv \
  --output ./regions.fa \
  --report ./regions.report.tsv
```

Extract the default representative-transcript promoter:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode promoter \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --id DM8.2_chr01G00010 \
  --length 2000 \
  --output ./promoter.2kb.fa \
  --report ./promoter.2kb.report.tsv
```

Extract or translate an explicitly selected transcript:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode protein \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --id DM8.2_chr01G00010.1 \
  --output ./transcript.protein.fa \
  --report ./transcript.protein.report.tsv
```

Use explicit local files when needed:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_sequences.py" \
  --mode cds \
  --source local \
  --genome /path/to/genome.fa \
  --annotation /path/to/genes.gff3 \
  --id GeneA \
  --isoform longest \
  --output ./GeneA.cds.fa \
  --report ./GeneA.cds.report.tsv
```

## Feature and strand semantics

- Promoter defaults to `--isoform representative`.
- Other feature modes default to `--isoform error`, preventing silent transcript selection.
- Use `--isoform longest` only when the user requests the longest model.
- Use `--isoform all` when the user requests all transcripts.
- An explicit transcript ID bypasses gene-level isoform selection.
- Promoters exclude the ATG base and require CDS coordinates; do not infer ATG from a gene or transcript boundary.
- Gene-derived output is oriented 5-prime to 3-prime. Region mode uses the requested strand and defaults to `+` only when no strand was provided.
- In `phased_tetraploid/*`, an ambiguous raw ID returns exact `id@seqid` candidates. Report them and obtain the user's haplotype choice; never merge loci or invent a suffix.

The centralized index selects representative transcripts using explicit provenance, an unambiguous GFF representative marker, longest CDS, longest exon span when no CDS exists, and transcript ID as a deterministic tie-breaker. For generic explicit files without that index, representative selection falls back to longest CDS or longest exon span. Preserve `representative_source` in the report.

## Limits and clipping

API sequence extraction allows at most 1,000,000 requested nucleotides per output plan and 256 segments per request. The script splits larger exon/CDS segment lists across requests while preserving order, but rejects any one output plan over 1 Mb.

Do not add `--clip` unless the user permits boundary truncation. Clipping applies only to `region`, `promoter`, and `gene-window`. Annotation-defined gene, exon, or CDS coordinates outside the reference are resource errors.

The script requests forward-strand API segments and performs reverse complementation during assembly. Do not manually reverse-complement the result again.

## Inspect extraction outputs

Each extraction run creates:

1. `--output`: extracted FASTA records.
2. `--report`: one audit row per query/transcript plan.
3. `OUTPUT.missing.txt`: unique failed query IDs unless `--missing` overrides it.
4. `OUTPUT.meta.json`: version, arguments, resolved resource, backend, paths, and counts unless `--metadata` overrides it.

Check `status`, `message`, `actual_length`, `clipped`, `resource_id`, `extraction_backend`, `resolved_id`, `transcript_id`, `segments`, `strand`, and `representative_source` before answering.

Exit codes:

- `0`: all requested work succeeded;
- `2`: extraction completed but at least one query failed;
- `1`: fatal input, resource, API, or file-transfer error.

When exit code is `2`, inspect the report and missing file even if the FASTA is non-empty. Report real failures instead of presenting partial output as complete.

## Standalone FASTA records

Use the compatibility script only when records already exist in a standalone derived FASTA:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/extract_fasta_records.py" \
  --fasta ./input.pep.fa \
  --ids ./ids.txt \
  --match-mode smart \
  --output ./selected.pep.fa \
  --report ./selected.pep.report.tsv \
  --missing ./selected.pep.missing.txt
```

Do not use this path for genome-backed coordinates, promoters, splicing, translation, complete genome downloads, or GFF3 downloads.

## Common pitfalls

1. Invoking a bundled script through a relative path from an arbitrary working directory. Use `${HERMES_SKILL_DIR}`.
2. Writing outputs into the installed skill directory. Use the current user's working directory.
3. Treating `reference` and `annotation` strings from `list-resources` as already downloaded files when using the API. Run download mode.
4. Using sequence extraction to reconstruct a complete genome. Run download mode.
5. Claiming the configured GFF3 is byte-identical to its upstream source. It is the complete database-configured, sorted/normalized artifact.
6. Enabling `--overwrite` without user authorization. Preserve existing user files by default.
7. Assuming Potato Agent server paths, localhost, or an internal IP exist for another user. Keep `--source auto` and the public HTTPS default.

## Verification checklist

- [ ] Commands use the Hermes-expanded `${HERMES_SKILL_DIR}` path.
- [ ] Assembly names come from `list-resources` or an exact user-provided value.
- [ ] Outputs are in the current user's workspace or an explicitly requested destination.
- [ ] Complete file requests use download mode and produce both configured files.
- [ ] Existing targets were not overwritten without explicit authorization.
- [ ] Extraction coordinates use 1-based inclusive semantics.
- [ ] Isoform and clipping choices match the user's request.
- [ ] Structured output/report was checked before reporting success.
- [ ] Large files were not loaded into model context.

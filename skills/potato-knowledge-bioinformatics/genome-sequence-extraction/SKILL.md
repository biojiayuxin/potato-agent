---
name: genome-sequence-extraction
description: Deterministically extract arbitrary genomic regions, genes, gene-relative windows, ATG-upstream promoters, spliced transcripts, CDS, or translated proteins from potato and other plant assemblies. Use when Codex needs strand-aware sequence extraction by feature ID or 1-based coordinates from local genome/GFF resources or the Potato Agent Genome Browser API, including regions up to 1 Mb.
---

# Genome Sequence Extraction

Use `scripts/extract_sequences.py` for genome-backed extraction. Convert the user's request into explicit parameters; let the script resolve resources and features, calculate coordinates, select isoforms, fetch sequence, orient strands, splice segments, translate proteins, and write audit files.

Use `scripts/extract_fasta_records.py` only when the user provides a standalone derived FASTA such as `cds.fa`, `pep.fa`, or transcript FASTA and wants records already present in that file.

## Choose a source

Use `--source auto` unless the user requires a specific backend:

- Preserve local operation when `--genome`, `--annotation`, or `--resource-dir` is present.
- For canonical Potato assemblies, use the local `Genome_browser_DB` when its reference and centralized `feature_index.sqlite` are readable. Resolve feature coordinates from the local index and extract sequence from the local FASTA.
- If the requested canonical assembly is not available through the local database, fall back to `https://potato-agent.ynnu.edu.cn`.
- Use matching local Other species resources when they are available.
- Use `--source local` to prohibit network fallback.
- Use `--source api` to require the API. Do not combine it with local resource arguments.

The API default is always `https://potato-agent.ynnu.edu.cn`; the script does not discover an address from systemd or environment variables. Use an explicit `--api-base-url` only for deployment testing, such as direct testing against a local Interface IP. Never put that test IP into routine skill commands. In `auto` mode, treat a stale, unreadable, incomplete, or provenance-mismatched local index/reference set as unavailable and select the API before extraction; do not retry through the API for a missing feature ID or an invalid user coordinate.

When the assembly name is uncertain, list resources first:

```bash
python3 scripts/extract_sequences.py --mode list-resources --source auto
python3 scripts/extract_sequences.py --mode list-resources --source api
```

Pass a unique assembly ID, sample, or display name to `--assembly`. Prefer the full assembly ID from the listing, such as `monoploid/DMv8.2`.

In `phased_tetraploid/*` assemblies, the same raw gene or transcript ID may occur on multiple haplotypes. An ambiguous raw ID fails with exact candidates such as `gene1@chr01_hap1`; rerun with the candidate matching the user's requested haplotype or report the choices when no haplotype was specified. Never invent an `@seqid` suffix or merge candidate loci.

For the API schema, limits, and response-validation contract, read [references/interface-api.md](references/interface-api.md) before changing or debugging API integration.

## Set extraction semantics

All CLI and API coordinates are 1-based inclusive. Convert BED coordinates before calling the script:

```text
start_1based = bed_start_0based + 1
end_1based = bed_end_exclusive
```

Choose one mode:

| Mode | Input | Result |
|---|---|---|
| `region` | `seqid/start/end/strand` or regions TSV | Arbitrary genomic interval |
| `gene` | Gene or transcript ID | Complete parent gene span |
| `gene-window` | Gene or transcript ID plus upstream/downstream | Strand-aware gene-relative window |
| `promoter` | Gene or transcript ID plus length | Sequence immediately upstream of the CDS 5-prime/ATG coordinate |
| `transcript` | Gene or transcript ID | Exons spliced in transcript order |
| `cds` | Gene or transcript ID | CDS segments spliced in transcript order |
| `protein` | Gene or transcript ID | Spliced CDS translated with the standard nuclear code |

Apply these isoform rules:

- Let `promoter` default to `--isoform representative`.
- Let other feature modes default to `--isoform error` so a multi-transcript gene does not silently select one.
- Use `--isoform longest` only when the user requests the longest model.
- Use `--isoform all` to emit every transcript.
- Treat an explicit transcript ID as authoritative and bypass gene-level isoform selection.

The centralized index supplies its representative transcript and `representativeSource` for both local canonical Potato extraction and API extraction. Explicit local files and generic Other species annotations have no shared representative index, so `representative` falls back deterministically to longest CDS for promoter/CDS/protein or longest exon span for transcript. Check `representative_source` in the report; fallback values include `longest_cds_fallback` and `longest_exon_fallback`.

Promoters exclude the ATG base and contain exactly `--length` upstream bases unless clipping is explicitly enabled. Require CDS coordinates; do not infer an ATG from the transcript or gene boundary.

Orient gene-derived output 5-prime to 3-prime. For `region`, use the requested `--strand`; default to `+` only when the user did not request orientation.

## Respect limits and clipping

For API extraction, keep every output plan at or below 1,000,000 requested nucleotides. The script automatically splits plans containing more than 256 exon/CDS segments into multiple API calls while preserving segment order.

Do not add `--clip` unless the user allows boundary truncation. Clipping applies only to `region`, `promoter`, and `gene-window`. Treat out-of-bounds annotation-defined gene, exon, or CDS segments as resource errors.

The API receives forward-strand segments. The script performs reverse complementation during its normal assembly step; do not request negative-strand segments from the API or reverse-complement the result again.

## Run the unified script

Always set `--output` and `--report` for extraction modes.

Extract an arbitrary interval:

```bash
python3 scripts/extract_sequences.py \
  --mode region \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --seqid chr01 \
  --start 10000 \
  --end 12000 \
  --strand + \
  --name chr01_10000_12000 \
  --output region.fa \
  --report region.report.tsv
```

For batch regions, provide a tab-delimited file with `name`, `seqid`, `start`, and `end`; optionally include `strand`:

```bash
python3 scripts/extract_sequences.py \
  --mode region \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --regions regions.tsv \
  --output regions.fa \
  --report regions.report.tsv
```

Extract the default representative-transcript promoter:

```bash
python3 scripts/extract_sequences.py \
  --mode promoter \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --id DM8.2_chr01G00010 \
  --length 2000 \
  --output promoter.2kb.fa \
  --report promoter.2kb.report.tsv
```

Extract or translate an explicitly selected transcript:

```bash
python3 scripts/extract_sequences.py \
  --mode protein \
  --source auto \
  --assembly monoploid/DMv8.2 \
  --id DM8.2_chr01G00010.1 \
  --output transcript.protein.fa \
  --report transcript.protein.report.tsv
```

Use explicit local files when needed:

```bash
python3 scripts/extract_sequences.py \
  --mode cds \
  --source local \
  --genome /path/to/genome.fa \
  --annotation /path/to/genes.gff3 \
  --id GeneA \
  --isoform longest \
  --output GeneA.cds.fa \
  --report GeneA.cds.report.tsv
```

Canonical local potato resources default to `/mnt/data/public_data/Genome_browser_DB`; other species default to `/mnt/data/public_data/Other_species_genomes`. The script reads `assemblies.tsv` rather than guessing potato resource filenames and expects the centralized index at `feature_index.sqlite`, overridable with `--feature-index`. The index must be readable by the skill process. If an unindexed local FASTA is supplied, the script creates a temporary symlink/index and does not modify the source file.

## Inspect outputs

Expect four outputs for each extraction run:

1. `--output`: extracted FASTA records.
2. `--report`: one audit row per query/transcript plan.
3. `OUTPUT.missing.txt`: unique failed query IDs, overridable with `--missing`.
4. `OUTPUT.meta.json`: script version, argv, resolved resource, backend, index mode, paths, and counts, overridable with `--metadata`.

Check at least these report fields:

- Identity: `query_id`, `resolved_id`, `resolved_type`, `transcript_id`.
- Provenance: `resource_id`, `resource_source`, `extraction_backend`, `genome`, `annotation`.
- Selection: `isoform_policy`, `representative_source`.
- Coordinates: `seqid`, `strand`, `segments`, `requested_length`, `actual_length`, `clipped`.
- Translation: `phase_trim`, `trailing_trim`, `internal_stops`, `invalid_chars`.
- Outcome: `status`, `message`.

Interpret exit codes as follows:

- `0`: all queries succeeded.
- `2`: extraction completed but at least one query failed; inspect the report and missing file even if FASTA is non-empty.
- `1`: fatal input, resource, API, or extraction error.

## Extract existing FASTA records

For a standalone derived FASTA, run:

```bash
python3 scripts/extract_fasta_records.py \
  --fasta input.pep.fa \
  --ids ids.txt \
  --match-mode smart \
  --output selected.pep.fa \
  --report selected.pep.report.tsv \
  --missing selected.pep.missing.txt
```

Do not use this compatibility path to replace genome-backed coordinate, promoter, splicing, or translation logic.

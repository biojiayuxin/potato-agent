# Interface Genome Browser API

Use this contract through `scripts/extract_sequences.py`. The skill uses public Interface endpoints for assembly discovery, complete configured-file downloads, feature resolution, and bounded sequence extraction. Do not scrape the Genome Browser page or call JBrowse internals.

## Assembly catalog

```http
GET /api/genome-browser/assemblies
```

Each assembly includes a stable `id`, display metadata, and configured relative file paths. The download workflow uses only:

```json
{
  "id": "monoploid/DMv8.2",
  "reference": "monoploid/DMv8.2/reference/DMv8.2.fa.bgz",
  "annotation": "monoploid/DMv8.2/annotation/DMv8.2.gff3.bgz"
}
```

Resolve user input against `id`, `sample`, or `displayName`. Require a unique exact match before trying a case-insensitive match. Do not use `sourceReferences` or `sourceAnnotations`: those are provenance fields, not public download targets.

## Complete configured-file downloads

```http
GET /api/genome-browser/data/{configured_relative_path}
```

Build the path from the catalog's `reference` or `annotation` value. Percent-encode each path segment independently. Do not accept absolute paths, backslashes, empty components, `.` components, or `..` components.

A normal GET without `Range` returns the complete configured file. The endpoint also supports `HEAD` and byte ranges for browser adapters, but the skill's download mode performs a single streamed GET. These transfers are not subject to the sequence endpoint's 1 Mb limit or the JSON client's 2.5 MB response limit.

The configured artifacts are:

- BGZF-compressed complete genome FASTA (`*.fa.bgz`);
- BGZF-compressed complete sorted/normalized GFF3 (`*.gff3.bgz`).

They are the database versions requested by the user, not guaranteed byte-identical copies of original upstream files. Index files are outside the skill's default two-file download contract.

Stream responses to a temporary file in the requested output directory. Validate nonzero size and `Content-Length` when present, calculate SHA-256 during transfer, and publish only after both files succeed. Refuse existing targets unless the caller explicitly provided `--overwrite`; refuse symbolic-link targets even with overwrite enabled.

## Feature resolution

```http
GET /api/genome-browser/features/resolve?assembly=ASSEMBLY_ID&id=FEATURE_ID
```

The response contains `query`, `gene`, and `transcripts`. Transcript records include `isRepresentative`, `representativeSource`, `exons`, `cds`, and `cdsFivePrimePosition`. Accept `codingStart` as a compatibility alias for `cdsFivePrimePosition`.

Within a `phased_tetraploid/*` assembly, a raw gene or transcript ID can occur on multiple reference sequences. Resolving that alias returns HTTP `409` with exact candidates:

```json
{
  "detail": {
    "message": "feature alias is ambiguous",
    "candidates": [
      {"type": "gene", "id": "gene1@chr01_hap1"},
      {"type": "gene", "id": "gene1@chr01_hap2"}
    ]
  }
}
```

Retry only with a candidate explicitly selected by the user. Never select or merge candidate loci silently.

## Sequence extraction

```http
POST /api/genome-browser/sequences
Content-Type: application/json
```

Request body:

```json
{
  "assembly": "monoploid/DMv8.2",
  "clip": false,
  "segments": [
    {
      "name": "segment-0",
      "refName": "chr01",
      "start": 1,
      "end": 100,
      "strand": "+"
    }
  ]
}
```

Coordinates are 1-based inclusive. Segment names must be unique within one request. A request accepts at most 256 segments, at most 1,000,000 requested bases per segment, and at most 1,000,000 requested bases in aggregate.

The response preserves request order:

```json
{
  "assembly": "monoploid/DMv8.2",
  "coordinateSystem": "1-based-inclusive",
  "records": [
    {
      "name": "segment-0",
      "refName": "chr01",
      "refLength": 88500000,
      "requestedStart": 1,
      "requestedEnd": 100,
      "start": 1,
      "end": 100,
      "strand": "+",
      "clipped": false,
      "length": 100,
      "sequence": "ACGT..."
    }
  ]
}
```

The script requests all API segments with `strand: "+"` and performs strand orientation during local assembly. Validate record name, reference, requested and actual coordinates, strand, order, clipping flag, and sequence length. Reject reordered, partial, or malformed data.

## Backend selection

The default API origin is `https://potato-agent.ynnu.edu.cn`. `--api-base-url` is an explicit deployment-testing override, not a value to infer from localhost, systemd, or another user's environment.

With `--source auto`, canonical Potato requests use readable local configured files first. Download mode requires both local reference and annotation files. Region mode additionally requires usable reference indexes and `samtools`; feature modes require a current centralized `feature_index.sqlite`. If the local requirements for the chosen mode are unavailable, select the public API before starting work.

Do not retry a missing feature or invalid coordinate through another backend. Those are query/data errors, not backend availability failures.

## Error behavior

Treat HTTP failures, malformed JSON, oversized JSON responses, missing download files, truncated transfers, path validation failures, identity mismatches, reordered records, changed coordinates, and invalid sequence lengths as failures. Preserve the real error and do not report partial artifacts as complete.

Use `--clip` only for `region`, `promoter`, and `gene-window`. Without clipping, an interval outside its reference is an error. With clipping, preserve requested coordinates and report returned actual coordinates and `clipped=true`.

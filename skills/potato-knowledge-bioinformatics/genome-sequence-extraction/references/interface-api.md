# Interface Genome Browser Sequence API

Use this contract through `scripts/extract_sequences.py`. Do not call JBrowse internals: JBrowse consumes browser data adapters and does not provide the stable semantic feature/sequence API required by this skill.

## Endpoints

List assemblies:

```http
GET /api/genome-browser/assemblies
```

Resolve a gene or transcript and obtain indexed coordinates:

```http
GET /api/genome-browser/features/resolve?assembly=ASSEMBLY_ID&id=FEATURE_ID
```

The response contains `query`, `gene`, and `transcripts`. Transcript records include `isRepresentative`, `representativeSource`, `exons`, `cds`, and `cdsFivePrimePosition`. Accept `codingStart` as a compatibility alias for `cdsFivePrimePosition`.

Within a `phased_tetraploid/*` assembly, a raw gene or transcript ID that occurs on multiple reference sequences is indexed as `raw_id@seqid`. The raw ID remains an alias. Resolving that ambiguous alias returns HTTP `409`:

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

Retry only with an exact returned candidate. If the user did not specify a haplotype/reference sequence, report the candidates and request that choice; do not select a locus or combine sequences silently.

Fetch genomic segments:

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

The script uses `https://potato-agent.ynnu.edu.cn` as its fixed API default. It does not discover an origin from systemd or environment variables. Pass `--api-base-url http://10.186.0.25:3000` only when directly testing the deployed Interface; do not persist that local IP in skill defaults or routine commands.

With `--source auto`, canonical Potato queries use the local `Genome_browser_DB` first. Feature modes require a readable centralized `feature_index.sqlite` whose recorded reference, FAI, annotation, and optional representative-map provenance matches the local files; region mode requires a readable indexed reference. The API is selected before extraction when those required local resources are missing, stale, incomplete, or unreadable. Feature-not-found and invalid-coordinate errors are data/query failures and do not trigger a second backend.

The response preserves request order and returns, for every record:

```json
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
```

## Skill responsibilities

Keep semantic work in the skill:

- Resolve gene/transcript IDs through the feature endpoint.
- Select representative, longest, all, or explicit transcripts.
- Calculate promoter and gene-relative coordinates.
- Split exon/CDS plans larger than 256 segments into ordered requests.
- Request every segment with `strand: "+"`.
- Validate record name, reference, requested coordinates, actual coordinates, strand, order, clipping flag, and length.
- Splice segments, reverse-complement negative-strand plans once, and translate proteins locally.
- Reject any single output plan over 1 Mb before requesting sequence.

The sequence endpoint may reverse-complement a `strand: "-"` request, but this skill deliberately requests `+` so its shared local/API assembly logic remains identical.

## Error behavior

Treat HTTP failures other than the explicit `409` candidate workflow, malformed JSON, oversized responses, missing records, reordered records, changed requested coordinates, reference mismatches, and invalid sequence lengths as extraction failures. Do not silently accept partial or reordered data.

Use `--clip` only for `region`, `promoter`, and `gene-window`. Without clipping, an interval outside its reference returns an error. With clipping, preserve the original `requestedStart`/`requestedEnd`, use returned `start`/`end` for assembly, and record `clipped=true`.

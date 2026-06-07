# E4-63 GeneID_RepreID_AltID.tsv generation note

Use this note when E4-63 GO/KEGG enrichment fails because `GeneID_RepreID_AltID.tsv` is missing or suspected stale.

## Source and target

- Source GFF3: `/mnt/data/public_data/Genomes/E4-63/E4-63.unified_ID.gff3`
- Target mapping: `/mnt/data/public_data/GO_KEGG_data/E4-63/GeneID_RepreID_AltID.tsv`

## Extraction rule

The E4-63 annotation has one `mRNA` feature per `gene`. Build the mapping as:

- `GeneID`: `ID` attribute of each `gene` feature
- `Repre TransID`: `ID` attribute of the unique child `mRNA` whose `Parent` is the gene ID
- `Alt TransID`: empty

Do not assume the representative transcript is always `GeneID + ".1"`; the GFF3 contains valid unique transcripts with suffixes `.2`, `.3`, `.4`, and `.5`.

## Last verified counts

- Genes: 40,172
- mRNAs: 40,172
- Mapping rows excluding header: 40,172
- Unique GeneID: 40,172
- Unique Repre TransID: 40,172
- Empty Alt TransID rows: 40,172
- E4-63 GO background IDs covered by mapping: 15,461 / 15,461
- E4-63 KEGG background IDs covered by mapping: 15,445 / 15,445

## Validation points

Before replacing the mapping, verify:

1. Every `mRNA` Parent exists among gene IDs.
2. Every gene has exactly one child `mRNA`.
3. No duplicated GeneID or representative transcript ID.
4. Output header is exactly `GeneID<TAB>Repre TransID<TAB>Alt TransID`.
5. GO/KEGG background representative IDs are all covered by `Repre TransID`.

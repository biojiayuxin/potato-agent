# Example Venn-diagram gene sets

`example_venn_genes.tsv` is a fully synthetic wide-format input for `../scripts/venn_from_tsv.R`. The four columns represent four example gene sets; every identifier begins with `SYN_` and has no biological interpretation.

## Deliberate overlap structure

The data contain non-empty exclusive regions for all 15 possible memberships among four groups:

- 8 genes unique to each group.
- 4 genes in each pair-only region.
- 3 genes in each three-group-only region.
- 6 genes shared by all four groups.

Consequently, each complete set contains 35 unique genes. The structure is intentionally regular so that plotted counts and the region-summary TSV can be checked deterministically.

## Example group selections

Use the Hermes command pattern in `SKILL.md` and select one of these group
lists:

- Two sets: `--groups Leaf_DEGs,Root_DEGs`
- Three sets: `--groups Leaf_DEGs,Root_DEGs,Tuber_DEGs`
- Four sets: `--groups Leaf_DEGs,Root_DEGs,Tuber_DEGs,Stolon_DEGs`

The PDF files are the formal vector outputs. PNG files are previews only. Each region-summary TSV lists exclusive membership codes, counts, and item identifiers; membership-code digits follow the selected group order.

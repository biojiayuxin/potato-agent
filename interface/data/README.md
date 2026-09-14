# Spatial gene ID mapping

`dm6.1_dm8.2.tsv` is the supplied `dm6.1_dm8.2.txt` table, copied without
modification (tab-separated, CRLF line endings). SHA-256:
`ba71ed6eac161ac3c008b7391587a32129a012fab918dbdc898879a13fc30f66`.

The 32,917 rows have columns `Gene` (DMv6.1), `MappedGene` (DMv8.2), and
`Source` (mapping evidence). Empty IDs and `-` are ignored. Of 24,310 distinct
mapped DMv8.2 IDs, 24,161 have one distinct DMv6.1 target. All 149 IDs with
multiple targets are excluded, regardless of evidence, including synteny.
Unique mappings retain the supplied evidence, including sequence identity.
Original DMv6.1 IDs remain queryable even when involved in an excluded mapping.

The spatial gene, dotplot, and Agent expression APIs return `gene` as the
actual dataset ID, `requestedGene` as the trimmed input, and `mappingSource`
as the mapping evidence (`null` for direct queries). Expression values still
come from the original DMv6.1 datasets. A unique mapping does not guarantee
that the target gene is present in every dataset.

The backend caches this repository-owned table for the process lifetime;
restart the application after replacing it. No personal directory is needed
at runtime. Autocomplete continues to list only the original dataset IDs.

Regression checks (use a Python environment with Interface test dependencies):

```bash
python -m pytest interface/test_spatial_viewer.py interface/test_spatial_pdf_export.py
POTATO_SPATIAL_BROWSER_TESTS=1 python -m pytest interface/test_spatial_browser.py
```

The browser suite also needs Playwright and Chromium. Set
`POTATO_PLAYWRIGHT_EXECUTABLE` to use an existing browser and
`POTATO_SPATIAL_SCREENSHOTS` to retain screenshots. All fixtures use temporary
datasets and a minimal app without production startup hooks.

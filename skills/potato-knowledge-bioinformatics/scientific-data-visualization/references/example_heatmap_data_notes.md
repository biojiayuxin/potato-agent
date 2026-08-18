# Heatmap example and vector-PDF verification

`example_heatmap_data.tsv` is a synthetic 8-row × 4-column numeric matrix. The first field is a row-label column and is not part of the numeric matrix. The values are illustrative only and have no biological interpretation.

The 8 × 4 layout is only a compact reference example. For real user data, inspect the matrix first and fine-tune the plotting call or script as needed, including expected dimensions, figure size, annotation visibility and precision, label rotation, axis titles, color-scale center, normalization, and any scientifically justified row/column ordering or clustering. Do not assume that settings suitable for this example are suitable for a larger or differently structured matrix.

Use the Hermes command pattern in `SKILL.md`, which resolves the script and
this input through the skill directory while writing output to the user's
working directory.

The default `blue_white_red` mapping sends the global minimum to pure blue `#0000FF`, the global maximum to pure red `#FF0000`, and the midpoint of the active normalization to white. Use `--center 0` only when zero or another value is a scientifically meaningful midpoint; do not silently force symmetric limits. A different Matplotlib color map can be selected with `--cmap` when the user requests it.

## Durable implementation details

- Use `pcolormesh(..., rasterized=False)` rather than `imshow` when the heatmap cells must remain vector objects in PDF.
- Matplotlib colorbars may rasterize a long continuous gradient even when the main heatmap is vector. After creating the colorbar, call `colorbar.solids.set_rasterized(False)`.
- To avoid fine seams between vector colorbar segments in some viewers, set the solids edge color to `face` and line width to zero.
- Choose annotation text color from cell luminance so values remain legible on both dark red and dark blue cells.
- Treat row/column ordering, scaling, transformation, and clustering as analytical decisions; never add them silently merely to make the figure look better.

## Verification

```bash
pdfinfo outputs/example_heatmap.pdf
pdffonts outputs/example_heatmap.pdf
pdfimages -list outputs/example_heatmap.pdf
pdftoppm -png -singlefile -r 180 \
  outputs/example_heatmap.pdf \
  outputs/example_heatmap_rendered
```

Required checks:

1. `pdfinfo` reports one readable PDF page with a sensible page size.
2. `pdffonts` reports embedded fonts.
3. `pdfimages -list` contains only its header and no image rows when a fully vector figure is required.
4. The rendered preview has the expected matrix dimensions, unclipped labels, readable annotations, and the requested min/max color direction.
5. Verify matrix dimensions and extrema from the current input, not from visual inspection alone.

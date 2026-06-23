---
name: potato-spatial-expression
description: Query Potato Agent spatial transcriptomics expression statistics and generate cluster or tissue dotplots. Use when a user asks for potato spatial expression of an exact Soltu.DM.* gene ID, wants stolon, stem, tuber, early swelling tuber, cluster, tissue, or sample expression summaries, or requests a dotplot from the local spatial viewer API.
---

# Potato Spatial Expression

Use the local Potato Agent spatial viewer API to query read-only aggregate expression statistics. Always require an explicit dataset ID and an exact gene ID from the spatial dataset, such as `Soltu.DM.03G024100`.

Do not convert `DM8C*` IDs to `Soltu.DM.*` IDs in this skill. If the user gives a non-spatial gene ID, first resolve the correct spatial gene ID with another source or ask for the exact ID.

## Dataset Selection

Use these biological meanings for the current spatial datasets:

- `s1_s2` is stolon (`S1`) plus early swelling tuber (`S2`).
- `s1_stem` is stolon (`S1`) plus stem (`Stem`).

Choose data for user requests as follows:

- For expression in stolon or 匍匐茎, prefer `dataset=s1_stem` and use only the `scope="sample"`, `sample="S1"` rows for analysis and plotting.
- For expression in stem or 茎, use `dataset=s1_stem` and only the `scope="sample"`, `sample="Stem"` rows for analysis and plotting.
- For expression in tuber, 薯块, 块茎, early swelling tuber, or 块茎发育早期, use `dataset=s1_s2` as the primary dataset. Use the dataset-level rows (`scope="dataset"`) unless the user explicitly asks for `S2` only.
- If the user explicitly asks to compare stolon against early swelling tuber, use `dataset=s1_s2` and compare `sample="S1"` with `sample="S2"`.
- If the user explicitly asks to compare stolon against stem, use `dataset=s1_stem` and compare `sample="S1"` with `sample="Stem"`.

When generating dotplots for a single biological context, filter the API/TSV rows to the selected dataset/sample before presenting or plotting. If using the bundled dotplot command directly, explain which `dataset / sample` row represents the requested context.

## Commands

Prefer the bundled script:

```bash
python3 "$SKILL_DIR/scripts/query_potato_spatial.py" expression \
  Soltu.DM.03G024100 --dataset s1_s2
```

Generate a dotplot and TSV for one or more datasets:

```bash
python3 "$SKILL_DIR/scripts/query_potato_spatial.py" dotplot \
  Soltu.DM.03G024100 --dataset s1_s2 --dataset s1_stem \
  --group cluster --outdir spatial_plots
```

Use `--group cluster` for cluster summaries and `--group tissue` for tissue summaries. The default API base URL is `http://127.0.0.1:3000`; override it with `POTATO_SPATIAL_BASE_URL` or `--base-url`.

## Outputs

The `expression` command prints the API JSON response. The response contains aggregate statistics only:

- `clusterExpression` for cluster groups.
- `tissueExpression` for tissue groups.
- Both `scope="dataset"` and `scope="sample"` rows.
- No cell-level expression matrix or assignment arrays.

The `dotplot` command writes:

- `<gene>_<group>_dotplot.pdf`
- `<gene>_<group>_dotplot.tsv`

The TSV columns are fixed:

```text
dataset, gene, group_type, scope, sample, group_id, group_label,
cell_count, expressing_count, pct_expr, avg_expr,
avg_expr_expressing, sum_expr, max_expr
```

In the PDF, the x axis is cluster or tissue, the y axis is `dataset` and `dataset / sample`, dot size is `pct_expr`, and color is `avg_expr`.

## Response Handling

Report `cellCount`, `expressingCount`, `pctExpr`, and `avgExpr` when summarizing expression. Mention that assigned cells absent from the sparse expression table are counted as zero expression in the denominator.

If the API returns `404`, state that the dataset or exact gene was not found. If the API is unavailable, report the connection or HTTP error rather than guessing from other datasets.

---
name: pdf-document-analysis
description: Analyze PDF text, figures, tables, and scanned pages.
version: 1.2.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [pdf, document-analysis, scientific-papers, figures, vision, poppler]
    related_skills: [ocr-and-documents]
    requires_toolsets: [vision, terminal]
prerequisites:
  commands: [pdfinfo, pdftotext, pdftocairo]
---

# PDF Document Analysis Skill

Interpret a user-provided PDF according to the user's question. Select text
extraction, page rendering, and visual inspection adaptively instead of running
a fixed pipeline. Treat the supplied PDF as the primary source; use external
sources only for clearly labeled background or cross-checks.

## When to Use

Use this skill for facts, numerical values, summaries, named Figures, tables,
equations, scientific evidence synthesis, and image-only or scanned pages.

Do not use it for editing, signing, merging, or creating PDFs. Use the related
`ocr-and-documents` skill when the main task is general conversion, batch text
extraction, or full-document OCR rather than question-driven interpretation.

## Prerequisites

Both `vision` and `terminal` are required. Visual inspection must remain
available even for text-led questions because it verifies figures, tables,
equations, page structure, and extraction failures.

Use `terminal` to check the advisory command prerequisites:

```bash
command -v pdfinfo pdftotext pdftocairo
```

If a command is missing, explain the limitation and obtain permission before
installing software or creating an environment.

## How to Run

1. Resolve the PDF to a confirmed absolute path.
2. Create a dedicated work directory and record its printed absolute path:

   ```bash
   mktemp -d -t pdf-analysis.XXXXXX
   ```

3. Use that literal path in later `terminal` calls; shell variables do not
   necessarily persist across tool calls.
4. Quote every path and never overwrite or modify the source PDF.
5. Preserve requested artifacts, then remove only the confirmed dedicated work
   directory after completing the answer.

Commands below use `INPUT.pdf` and `WORKDIR` as placeholders. Replace them with
the confirmed quoted absolute paths.

## Quick Reference

| Need | First choice | Escalate when |
|---|---|---|
| Metadata, pages, restrictions | `pdfinfo` | The file is damaged or protected |
| Facts, summaries, captions | `pdftotext -layout` | Reading order is corrupted |
| Word positions | `pdftotext -tsv` | Visual inspection remains ambiguous |
| Figures, equations, structured tables | `pdftocairo` + `vision_analyze` | Labels need a higher-DPI crop |
| A few scanned pages | Render + `vision_analyze` | Searchable text across many pages is needed |
| Full-document OCR | `ocr-and-documents` | OCR dependencies need installation |

Use 150-200 DPI for page reconnaissance, about 300 DPI for normal Figure or
table inspection, and 600 DPI only for small or ambiguous labels.

## Procedure

### 1. Choose the Route

- Textual question: extract and search text first.
- Figure, chart, equation, or visually structured table: locate its page,
  render it, and use `vision_analyze`.
- Mixed question: combine text evidence with only the relevant rendered pages.
- Image-only page: render and inspect it; use OCR only when searchable text
  across many pages is necessary.

Do not render every page or run every tool by default.

### 2. Inspect and Extract

Inspect metadata, page count, rotation, encryption, and extraction permissions:

```bash
pdfinfo "INPUT.pdf"
```

If the PDF is corrupt, requires a password, or prohibits the requested
extraction, stop and explain the limitation. Ask for a valid unlocked copy; do
not bypass document protection.

Extract text for most textual questions:

```bash
pdftotext -layout "INPUT.pdf" "WORKDIR/document.txt"
pdftotext -f FIRST -l LAST -layout "INPUT.pdf" "WORKDIR/pages.txt"
```

Use `search_files` for the user's wording and likely synonyms, then `read_file`
for enough surrounding context to determine what each fact or number measures.
When multi-column order is interleaved, compare page-specific text with the
rendered page. Try `pdftotext -raw` only as a fallback.

Use word coordinates only when they materially help locate captions or rebuild
a table:

```bash
pdftotext -f PAGE -l PAGE -r 72 -tsv "INPUT.pdf" "WORKDIR/page.tsv"
```

Prefer visual inspection over complicated coordinate parsing.

### 3. Render and Inspect

Render a page preview or selected region:

```bash
pdftocairo -f PAGE -l PAGE -singlefile -png -r 180 \
  "INPUT.pdf" "WORKDIR/page-PAGE"

pdftocairo -f PAGE -l PAGE -singlefile -png -r DPI \
  -x X -y Y -W WIDTH -H HEIGHT \
  "INPUT.pdf" "WORKDIR/region"
```

Pass the PNG to `vision_analyze` with a focused question. Use whole pages to
locate content and high-resolution crops to read small labels. Independently
inspect every crop before relying on it.

`pdftocairo` crop coordinates are output pixels at the chosen DPI. Convert
coordinates measured on another preview with:

```text
output coordinate = preview coordinate * output DPI / preview DPI
```

Keep a margin around panels so labels, legends, and edges are not clipped.

### 4. Handle Scientific Content

For a named Figure:

1. Search for the exact caption, including `Figure 5` and `Fig. 5` forms.
2. Distinguish captions from body references and Supplemental Figures.
3. Render the whole candidate page at 150-200 DPI.
4. Locate the complete Figure, excluding headers, captions, body text,
   watermarks, and neighboring figures.
5. Render the Figure near 300 DPI and individual panels when needed.
6. Re-render uncertain labels at 600 DPI.
7. Cross-check panel labels with the caption and surrounding text.
8. Verify all required panels, legends, scale bars, and labels are present.

Captions may be above, below, beside, or on another page, and Figures may span
columns or pages. Figure location is a reasoning task, not a fixed crop rule.

For tables, equations, and scans:

- Try `pdftotext -layout` first for simple tables and render when merged cells,
  colors, symbols, footnotes, or visual grouping affect meaning.
- Render equations when extraction damages superscripts, subscripts, Greek
  letters, or operators.
- If text is empty or sparse, render a representative page and determine
  whether it is scanned, vector-heavy, unusually encoded, or mainly a Figure.
- Use `vision_analyze` for a few relevant scanned pages. Load
  `ocr-and-documents` when a long scan needs searchable full text.
- Treat OCR as uncertain for gene IDs, equations, superscripts, and numerical
  tables. Do not install OCR software without permission.

### 5. Build the Answer

For exact facts, report the entity, method, material or condition, and document
location rather than a bare number. Distinguish nearby quantities with different
meanings. For summaries, read the relevant results, methods, discussion,
conclusions, and limitations rather than only the abstract.

For evidence synthesis, separate:

1. direct observations or measurements;
2. relationships supported by multiple experiments;
3. the authors' integrated interpretation or model;
4. missing causal tests, contradictions, and limitations.

Do not turn colocalization, enrichment, expression correlation, or a model
diagram into stronger causality than the document demonstrates. Cite the
physical PDF page and printed page when practical, plus the relevant section,
Figure, panel, or table. Preserve discrepancies; mark unreadable characters as
uncertain instead of guessing.

## Pitfalls

Do not use `pdfimages` for complete scientific Figures. A Figure can combine
vector paths, live text, raster objects, masks, axes, legends, and annotations;
`pdfimages` extracts embedded raster objects rather than the complete visual
composition. Render the page with `pdftocairo`, crop the Figure, and inspect it
with `vision_analyze`. Use `pdfimages` only when the user explicitly requests
raw embedded raster assets and accepts that they may be incomplete.

Also avoid:

- assuming an in-text Figure reference is the Figure's page;
- trusting extracted reading order or vision-generated crop coordinates;
- clipping labels, legends, scale bars, or footnotes;
- reading tiny identifiers at low DPI;
- silently reconciling contradictory labels or measurements;
- leaving temporary files behind or deleting an unverified path.

## Verification

- [ ] The correct PDF and requested scope were used.
- [ ] File integrity, encryption, and extraction restrictions were checked.
- [ ] Text claims have sufficient context and similar quantities are distinct.
- [ ] Named Figures were located from captions and completely rendered with
      `pdftocairo`, not reconstructed with `pdfimages`.
- [ ] Crops, small labels, captions, and body text were cross-checked.
- [ ] Direct evidence, interpretation, causal limits, and uncertainty are clear.
- [ ] Useful page, section, Figure, panel, or table references are included.
- [ ] Requested outputs were preserved and only the dedicated work directory
      was cleaned up.

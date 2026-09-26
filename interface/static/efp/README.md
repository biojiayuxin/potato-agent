# Tissue Expression Map (eFP)

Public page: `/efp`. It uses the Gene Expression API with `scope=tissue` and
`transform=log2_tpm`, `tpm`, or `row_zscore`. The API remains responsible for tissue
means and transformations. In particular, Z-scores are calculated across all
atlas tissues after log2(TPM + 1), using the existing sample standard deviation.
Both the color range and Z-score reference set include tissues absent from the
drawing, matching the Gene Expression page's numerical behavior.

`expression.mjs` contains the explicit tissue mapping. Space/underscore aliases
are accepted for young/mature leaves and flower buds. Generic `flower` does not
map to flower bud. It maps to the two front-facing open flowers at the top of the
drawing, including their petals and centers, as marked in `Potato-flower-2.png`.
These shapes belong exclusively to `flower`; the other floral shapes retain
their separate perianth, anther, and flower-bud assignments. The following
correspondences were explicitly selected for this dataset:

| Transcriptome tissue | Diagram region |
| --- | --- |
| `tuber` | `mature_tuber` |
| `immature small tuber (transection diameter < 1 cm)` | `young_tuber_S3` |
| `immature big tuber (1 cm <transection diameter < 5 cm)` | `young_tuber_S4` |

`stolon` fills both `stolon` and `stolon_tip_S1`, with both currently labeled
“Stolon”. The regions retain separate IDs. A distinct `stolon tip`, `stolon_tip`,
`stolon_tip_S1`, or `stolon tip (S1)` column takes precedence for `stolon_tip_S1`
and restores its “Stolon tip (S1)” label; an explicit null tip value stays NA.
Each displayed tissue row records all matching region IDs in `diagramIds`.
`stamen` is excluded in the eFP adapter, including the table and PDF metadata.
The shared API's all-atlas transformations and color range are retained.
Unmapped or null values are gray NA; zero remains a real value. No averaging or
fuzzy tissue matching is performed.

The original SVG and tissue definitions were supplied in
`Potato_eFP_web_handoff_v7.zip` (original template SHA-256
`c9a6c7dcd9ba70387e91f89e66a369bbfe9e9380ed94a4821a96bfdda630aca0`).
The current v8 template adds the independent `flower` region by moving seven
petal paths and two center paths from perianth/anther into `flower-shape-v8`.
All 303 original path geometries and the linework remain intact. `tissues.json`
records the current template version, checksum, and 16 tissue regions.
The supplied `potato-efp.mjs` renderer was adapted for English labels, signed
values, and a diverging Z-score palette. Its SVG reference namespacing is retained.

The diagram sits beside a permanent list of transcriptome tissues except stamen,
including unmapped tissues with their actual expression values. The list shows raw TPM and
the selected transform (up to six decimal places); TPM mode avoids a duplicate
column. An asterisk marks tissues without a matching diagram region.
Rows are grouped as floral organs, fruit, leaves, stem, root, stolon, and tubers,
preserving source order within each group; unknown groups follow at the end.
This presentation order is shared by the page and PDF export.

The legend stays in the diagram's upper-left corner. Zoom buttons, the mouse wheel,
and keyboard +/− support 50–400% zoom. Drag or use arrow keys to pan when zoomed in;
Fit or the 0 key resets the view. A new query resets the view as well.
Tissue details use one custom tooltip; native SVG titles are removed to prevent
overlapping hover labels. Accessible names remain available through ARIA labels.

PDF download includes the full drawing, gene ID, transform, upper-left legend,
NA note, and the displayed tissue value list, regardless of the on-screen zoom.
Raw/transformed values are included in the PDF's `PotatoExpression` information
entry. The PDF 1.4 file contains filled/stroked Bezier paths, vector legend bars,
and text with both regular and bold Liberation Sans fonts embedded. It has no
image objects, SVG references, transparency masks, or runtime font dependencies.
The fonts are distributed unmodified under the bundled SIL Open Font License.

`figure.mjs` builds a DOM-free layout shared by browser and API exports.
`export.mjs` can turn that layout into an SVG reference; `pdf.mjs` uses the same
layout and prepared vector geometry to produce the PDF in the browser or Node.
`pdf-geometry.json` resolves the SVG's reusable shapes,
leaf clipping, and stem mask through vector Boolean operations, preserving the
original curve geometry. Regenerate it whenever the source SVG changes. The
asset regression verifies the SVG and font checksums to catch stale geometry.
Build dependencies are only needed in an isolated development environment:

```bash
python -m pip install skia-pathops==0.9.1 fonttools==4.61.1
python interface/build_efp_pdf_assets.py
```

The builder accepts `--font-directory` and `--font-license` for the unmodified
Liberation Sans regular/bold TTFs and their redistribution license. Its default
paths match the Debian `fonts-liberation` package. The deployed server requires
no additional Python packages. The API renderer requires Node.js 18+ (`node` on
the Interface service's PATH, or the executable selected by `POTATO_EFP_NODE`),
with no npm packages or browser dependencies. Browser validation optionally uses Poppler's
`pdftotext` and `pdftoppm`, with Pillow, to compare the exported diagram against
the original SVG, including the masked and clipped regions.

## API for agents and external skills

These public, read-only endpoints require no session or API key and do not
refresh agent runtime activity:

| Endpoint | Parameters | Response |
| --- | --- | --- |
| `GET /api/efp/source` | None | Figure source page, expression source page and tissue-mean explanation |
| `GET /api/efp/genes` | `q` (optional), `limit` (1–100, default 20) | Matching atlas gene IDs and summaries |
| `GET /api/efp/expression` | `gene`, `transform` | eFP tissue values, region mapping, colors, legend and NA state |
| `GET /api/efp/export.pdf` | `gene`, `transform` | `application/pdf` attachment with a safe filename |

`gene` is one exact gene ID (maximum 200 ASCII characters, no whitespace, commas
or semicolons). `transform` defaults to `log2_tpm`; `tpm` and `row_zscore` are also
accepted. Scope is always `tissue`. Search uses the same atlas as Gene Expression.

`/api/efp/source` needs no gene ID, database access or renderer. Its `description`
states that the figure is exported from Tissue Expression Map (eFP), using the
average tissue expression levels from the Gene Expression page's Tissue mean
view. The description also explains that gray means the diagram region lacks
mappable tissue expression data, while white is an uncolored schematic area or
background and is excluded from color-scale comparisons.
`figure` supplies the `/efp` page and PDF endpoint; `data` supplies the
`/bulk-rnaseq` page, expression endpoint, `scope=tissue`, TPM unit and aggregation
explanation. Page paths are relative to the requested Interface origin. The
skill's `source` command reads this description and includes it when delivering
a PDF; `plot` also returns its `source_url`.

The expression JSON includes `geneId`, `dataset`, `scope`, `transform`, `unit`,
`scale`, `ticks`, `tissueValues`, `regions`, `missingIds`, `unmappedTissues`,
`rawTpm` and `values`. `tissueValues` contains the displayed table rows
(`tissue`, `rawTpm`, `value`, `diagramIds`). `regions` describes diagram regions
(`id`, `label`, `value`, `colour`, `missing`, `clipped`). The PDF's
`PotatoExpression` metadata carries the values and mapping used for that export.
Each request reads the current atlas; callers needing an exact export snapshot
should use the metadata embedded in that PDF.

```bash
curl --fail --get 'http://127.0.0.1:3000/api/efp/expression' \
  --data-urlencode 'gene=DM8.2_chr05G25210' --data-urlencode 'transform=log2_tpm'
curl --fail --get 'http://127.0.0.1:3000/api/efp/export.pdf' \
  --data-urlencode 'gene=DM8.2_chr05G25210' --data-urlencode 'transform=log2_tpm' \
  --output DM8.2_chr05G25210_efp_log2_tpm.pdf
```

Invalid parameters return 400/422, missing genes return 404, and unavailable
data/rendering assets return 503. Rendering is limited to two subprocesses per
Interface process; excess requests receive 503 with `Retry-After: 2`. A render
timeout returns 504 after 20 seconds. The server invokes a fixed Node script
with JSON on stdin, a restricted environment, and no shell or user-selected
files. It checks template/font checksums and does not write export files.

The managed skill is
[`potato-efp-expression`](../../../skills/potato-knowledge-bioinformatics/potato-efp-expression/SKILL.md).
It uses only Python's standard library to search, query and save PDFs. Deploy
the Interface changes before refreshing managed skills for existing users.
An installation may supply `api-base-url.txt` at the skill root to select its
actual Interface origin without changing the script or system environment.
CLI and `POTATO_EFP_BASE_URL` overrides take precedence. Verify the public origin
separately: it may route to a different backend from the local deployment.

Run an isolated preview without starting the chat runtime or changing deployment:

```bash
python -m interface.preview_efp --port 3011 --api-origin http://YOUR_INTERFACE_HOST:3000
```

Open `http://127.0.0.1:3011/efp`. The optional origin forwards only public
Gene Expression and eFP GET endpoints; authentication headers and cookies are not forwarded.
Without `--api-origin`, the preview reads the database selected by
`BULK_RNASEQ_DB_PATH`, just like Gene Expression. It does not substitute simulated
data when the real API is unavailable. Add `--host` to select a preview address.

Deep links accept `?gene=DM8.2_chr05G25210&transform=row_zscore`.
The preview serves eFP and Gene Expression; other portal modules belong to the
full Interface application.

Validation from the repository root:

```bash
python -m pytest interface/test_efp_viewer.py interface/test_efp_api.py interface/test_efp_skill.py interface/test_bulk_rnaseq_viewer.py
POTATO_EFP_BROWSER_TESTS=1 python -m pytest interface/test_efp_browser.py
POTATO_NAVIGATION_BROWSER_TESTS=1 python -m pytest interface/test_navigation_browser.py
```

Browser tests use a temporary expression database and Playwright Chromium.
`POTATO_EFP_SCREENSHOTS` selects the screenshot directory;
`POTATO_PLAYWRIGHT_EXECUTABLE` optionally selects a Chromium executable.

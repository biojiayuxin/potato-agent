from pathlib import Path

from fastapi.testclient import TestClient

from interface.app import app


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = REPO_ROOT / "interface" / "static"


def test_genes_page_and_deep_link_serve_the_gene_catalog_frontend() -> None:
    client = TestClient(app)
    try:
        for path in ("/genes", "/genes/DM8.2_chr05G04850"):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert 'id="gene-search-form"' in response.text
            assert 'id="genes-heading"' not in response.text
            assert 'class="gene-search-heading"' not in response.text
            assert 'aria-label="Gene search"' in response.text
            assert 'id="gene-detail-section"' in response.text
            assert 'id="gene-predicted-function"' in response.text
            assert 'id="gene-sequence-panel"' in response.text
            assert 'data-portal-module="genes"' in response.text

        styles = client.get("/static/genes/styles.css")
        script = client.get("/static/genes/app.js")
        assert styles.status_code == 200
        assert script.status_code == 200
        assert "DMv8.2 Gene Catalog" not in response.text
        assert 'id="catalog-summary"' not in response.text
        assert "MAX_SYMBOLS" not in script.text
        assert "MAX_REPORTED_IDS" not in script.text
        assert "createResultRow('Gene ID:')" in script.text
        assert "createResultField('Symbol:', gene.symbols)" in script.text
        assert "createResultField('Reported ID:', gene.reportedIds, reportedIdLabel)" in script.text
        create_gene_result = script.text.split("function createGeneResult(gene)", 1)[1].split(
            "function renderPagination", 1
        )[0]
        assert "title.target = '_blank'" in create_gene_result
        assert "title.rel = 'noopener noreferrer'" in create_gene_result
        assert (
            "appendValueList(row.value, values, Number.POSITIVE_INFINITY, formatter)"
            in script.text
        )
        assert "createResultRow('Summary:')" in script.text
        assert "createResultRow('Description:')" not in script.text
        assert "descriptionExcerpt" in script.text
        assert "-webkit-line-clamp" not in styles.text
        assert "border-bottom: 1px solid #e5e7eb" in styles.text
        assert ".gene-results-section {\n  width: 100%;\n  min-width: 0;" in styles.text
        assert "mode: 'detail'" in script.text
        assert "loadGeneDetail" in script.text
        assert "transcript_id" in script.text
        assert "label: 'Promoter'" in script.text
        assert "ATG upstream (2 kb)" not in script.text
        assert "Expression Atlas" in response.text
        assert "textContent" in script.text
        assert "innerHTML" not in script.text
        assert "description/evidence" not in script.text
        assert "gradeReason" not in script.text
        assert "sourceRun" not in script.text
        assert "sourceKey" not in script.text
        detail_markup = response.text.split('id="gene-detail-content"', 1)[1]
        detail_headings = (
            "Symbols",
            "Reported IDs",
            "Transcripts",
            "Summary",
            "InterPro Domains",
            "UniRef100 similarity",
            "Tissue expression",
            "Literature",
        )
        heading_positions = [detail_markup.index(f">{heading}<") for heading in detail_headings]
        assert heading_positions == sorted(heading_positions)
        assert 'id="gene-detail-assembly"' not in response.text
        assert 'id="gene-detail-overview"' not in response.text
        assert "Catalog release" not in response.text
        assert "Genomic location" not in response.text
        assert "Reported IDs and transcripts" not in response.text
        assert "Predicted function" not in response.text
        assert "Functional annotations" not in response.text
        assert 'id="gene-annotation-tabs"' not in response.text
        render_symbols = script.text.split("function renderSymbols(payload)", 1)[1].split(
            "function renderReportedIds", 1
        )[0]
        render_reported_ids = script.text.split(
            "function renderReportedIds(payload)", 1
        )[1].split("function renderDescription", 1)[0]
        assert "renderDelimitedValues(detailSymbols, symbols)" in render_symbols
        assert (
            "renderDelimitedValues(reportedIdList, reportedIds, reportedIdLabel)"
            in render_reported_ids
        )
        assert "normalized.join('; ')" in script.text
        assert "createExpandableValues" not in script.text
        assert "document.createElement('small')" in script.text
        assert "Representative" in script.text
        assert "annotations.interpro" in script.text
        assert "goTerms" not in script.text
        assert "keggTerms" not in script.text
        assert "nSources" not in script.text
        assert "nRuns" not in script.text
        assert "EXPRESSION_TISSUE_GROUPS" in script.text
        assert "orderedExpressionTissues" in script.text
        assert "gene-expression-error-bar" in script.text
        assert "gene-expression-tooltip" in script.text
        assert "gene-expression-help" not in script.text
        assert "gene-expression-help" not in styles.text
        assert "Small tuber" in script.text
        assert "Large tuber" in script.text
        assert 'id="gene-paper-count"' not in response.text
        assert 'id="gene-similarity-count"' not in response.text
        assert "paperCount" not in script.text
        assert "similarityCount" not in script.text
        assert "repeat(${tissues.length}, 60px)" in script.text
        assert "max-width: 1120px" in styles.text.split(
            ".gene-detail-section {", 1
        )[1].split("}", 1)[0]
        assert ".gene-annotation-terms li {\n  min-width: 0;\n  display: flex;" in styles.text
        assert ".gene-detail-header h2 {" in styles.text
        assert "font-size: 28px" in styles.text
        assert ".gene-block-header h3 {" in styles.text
        assert "font-size: 19px" in styles.text
        assert styles.text.count("color: #1F5631") == 2
        assert "--gene-detail-content-size: 15px" in styles.text
        assert "font-size: var(--gene-detail-content-size)" in styles.text.split(
            ".gene-detail-block {", 1
        )[1].split("}", 1)[0]
        assert "font-size:" not in styles.text.split(
            ".gene-similarity-table th {", 1
        )[1].split("}", 1)[0]
        assert "font-size:" not in styles.text.rsplit(
            ".gene-similarity-table td {", 1
        )[1].split("}", 1)[0]
        assert 'id="gene-sequence-type"' in response.text
        assert 'id="gene-sequence-tabs"' not in response.text
        assert 'class="gene-sequence-copy-button"' in response.text
        assert 'src="/static/lite/icons/copy_button.png"' in response.text
        assert "renderSequenceTypeOptions('cds')" in script.text
        render_sequences = script.text.split("function renderSequences(payload)", 1)[1].split(
            "async function loadSequence", 1
        )[0]
        assert "loadSequence();" in render_sequences
        assert "const ICON_COPIED_PATH = '/static/lite/icons/copied.png'" in script.text
        assert "sequenceCopyIcon.src = ICON_COPIED_PATH" in script.text
        assert "}, 3000);" in script.text
        assert "sequenceCopy.textContent" not in script.text
        assert ".gene-sequence-copy-button {" in styles.text
        assert "width: 26px" in styles.text
        assert ".gene-sequence-copy-icon {" in styles.text
        assert "width: 14px" in styles.text
        assert ".gene-predicted-function {" in styles.text
        assert "font-size: 11px" in styles.text.split(
            ".gene-representative-badge {", 1
        )[1].split("}", 1)[0]
        assert ".gene-expression-bar {" in styles.text
        assert ".gene-expression-error-bar {" in styles.text
    finally:
        client.close()


def test_all_portal_navigation_surfaces_link_to_genes() -> None:
    index_paths = (
        STATIC_ROOT / "lite" / "index.html",
        STATIC_ROOT / "lite" / "high-resolution-required.html",
        STATIC_ROOT / "spatial" / "index.html",
        STATIC_ROOT / "wgcna" / "index.html",
        STATIC_ROOT / "bulk_rnaseq" / "index.html",
        STATIC_ROOT / "genome_browser" / "index.html",
    )
    for path in index_paths:
        assert '/static/shared/navigation.js?v=' in path.read_text(encoding="utf-8")

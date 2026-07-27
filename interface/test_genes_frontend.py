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
            assert 'href="/genes" aria-current="page"' in response.text

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
        assert "ATG upstream (2 kb)" in script.text
        assert "Expression Atlas" in response.text
        assert "textContent" in script.text
        assert "innerHTML" not in script.text
        assert "description/evidence" not in script.text
        assert "gradeReason" not in script.text
        assert "sourceRun" not in script.text
        assert "sourceKey" not in script.text
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
        assert 'href="/genes"' in path.read_text(encoding="utf-8")

    lite_index = index_paths[0].read_text(encoding="utf-8")
    lite_script = (STATIC_ROOT / "lite" / "app.js").read_text(encoding="utf-8")
    assert 'data-mobile-supported="true"' in lite_index
    assert "item.getAttribute('data-mobile-supported') === 'true'" in lite_script

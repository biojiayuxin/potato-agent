"""POTATO_EFP_BROWSER_TESTS=1; optional POTATO_EFP_SCREENSHOTS output directory."""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from interface.build_bulk_rnaseq_db import build_database
from interface.preview_efp import create_preview_app
from interface.test_dashboard_browser import browser  # noqa: F401


pytestmark = pytest.mark.skipif(os.getenv("POTATO_EFP_BROWSER_TESTS") != "1", reason="Opt-in Playwright eFP suite")


@pytest.fixture
def site(tmp_path, monkeypatch):
    import uvicorn

    source = tmp_path / "source"
    source.mkdir()
    tissues = [
        "root", "young leaf", "mature leaf", "stem", "flower", "tuber", "stolon", "stamen",
        "immature small tuber (transection diameter < 1 cm)",
        "immature big tuber (1 cm <transection diameter < 5 cm)",
    ]
    (source / "sample_tissue_list.tsv").write_text("sample_column\tsample_name\ttissue\n" + "".join(
        f"S{i}\tMaterialA\t{tissue}\n" for i, tissue in enumerate(tissues)
    ))
    (source / "transcript_tpm_matrix_merged.tsv").write_text(
        "transcript_id\tgene_id\tgene_name\t" + "\t".join(f"S{i}" for i in range(len(tissues))) + "\n"
        "TxA\tGeneA\t\t0\t2\t10\t20\t40\t80\t12\t160\t0\t4\n"
        "TxZ\tGeneZero\t\t" + "\t".join("0" for _ in tissues) + "\n"
        "TxC\tGeneConstant\t\t" + "\t".join("5" for _ in tissues) + "\n"
    )
    db = tmp_path / "expression.sqlite"
    build_database(source, db)
    monkeypatch.setenv("BULK_RNASEQ_DB_PATH", str(db))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_preview_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def search(page, gene):
    page.locator("#gene-input").fill(gene)
    page.locator("#submit-button").click()


def loaded(page, gene):
    from playwright.sync_api import expect

    expect(page.locator("#graph-title")).to_have_text(gene)
    expect(page.locator("#download-pdf")).to_be_enabled()


def screenshot(page, tmp_path, name):
    directory = Path(os.getenv("POTATO_EFP_SCREENSHOTS") or tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / f"{name}.png"), full_page=True)


def test_efp_scales_missing_values_export_and_navigation(site, browser, tmp_path):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": 1440, "height": 1000}) as context:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(site + "/efp?gene=GeneA")
        loaded(page, "GeneA")
        expect(page.locator("#graph-summary")).to_have_text("Tissue mean | log2(TPM + 1)")
        root = page.locator('#plant [data-tissue="root"]')
        expect(root).to_have_attribute("data-expression", "0")
        expect(root).to_have_attribute("fill", "#FFFFCC")
        expect(page.locator('#plant title, #plant [title]')).to_have_count(0)
        expect(page.locator('#plant [data-tissue="flower_bud"]')).to_have_attribute("fill", "#C9CED0")
        expect(page.locator('#plant [data-tissue="young_leaf"]')).to_have_attribute("data-expression", "1.584963")
        expect(page.locator('#tissue-rows tr')).to_have_count(9)
        expect(page.locator('#tissue-rows tr[data-tissue="stamen"]')).to_have_count(0)
        expect(page.locator('#plant [data-tissue="stolon_tip_S1"]')).to_have_attribute('data-label', 'Stolon')
        expect(page.locator('#plant [data-tissue="stolon_tip_S1"]')).to_have_attribute('data-expression', '3.70044')
        expect(page.locator('#plant [data-tissue="young_tuber_S3"]')).to_have_attribute('data-expression', '0')
        expect(page.locator('#plant [data-tissue="young_tuber_S4"]')).to_have_attribute('data-expression', '2.321928')
        expect(page.locator('#plant [data-tissue="mature_tuber"]')).to_have_attribute('data-expression', '6.33985')
        flower = page.locator('#tissue-rows tr[data-tissue="flower"]')
        expect(flower).to_have_attribute('data-mapped', 'true')
        expect(flower.locator('.efp-tissue-label')).to_have_text('flower')
        expect(page.locator('#plant [data-tissue="flower"]')).to_have_attribute('data-expression', '5.357552')
        expect(flower.locator('td').nth(1)).to_have_text('40')
        expect(flower.locator('td').nth(2)).to_have_text('5.357552')
        assert page.locator('.efp-stage').evaluate('''stage => {
            const image = stage.getBoundingClientRect();
            const legend = stage.querySelector('#efp-legend').getBoundingClientRect();
            const values = document.querySelector('#tissue-panel').getBoundingClientRect();
            return values.left >= image.right && legend.left >= image.left && legend.top >= image.top
              && legend.right < image.left + image.width / 2;
        }''')
        screenshot(page, tmp_path, "efp-log2")
        page.get_by_role("button", name="Expression", exact=True).click()
        expect(page.locator('.portal-nav-panel a')).to_have_text([
            "Gene Expression", "Tissue Expression Map", "WGCNA Network", "Spatial Expression",
        ])
        expect(page.locator('.portal-nav-panel [aria-current="page"]')).to_have_text("Tissue Expression Map")
        page.keyboard.press("Escape")
        page.locator("#transform-select").select_option("tpm")
        expect(page.locator('#plant [data-tissue="young_leaf"]')).to_have_attribute("data-expression", "2")
        expect(flower.locator('td')).to_have_count(2)
        expect(flower.locator('td').nth(1)).to_have_text('40')
        root.focus()
        expect(page.locator("#tissue-tooltip")).to_contain_text("TPM: 0")
        expect(page.locator('#plant title, #plant [title]')).to_have_count(0)
        page.locator('#map-viewport').hover(position={'x': 3, 'y': 3})
        expect(page.locator('#tissue-tooltip')).to_be_hidden()
        page.locator('#map-viewport').focus()
        root.focus()
        expect(page.locator('#tissue-tooltip')).to_contain_text('TPM: 0')
        page.keyboard.press("Escape")
        expect(page.locator("#tissue-tooltip")).to_be_hidden()
        page.locator("#transform-select").select_option("row_zscore")
        expect(page.locator("#legend-unit")).to_have_text("Z-score")
        expect(page.locator('#plant title, #plant [title]')).to_have_count(0)
        assert float(root.get_attribute("data-expression")) < 0
        assert root.get_attribute("fill") != "#C9CED0"
        screenshot(page, tmp_path, "efp-zscore")
        page.locator('#zoom-in').click()
        expect(page.locator('#zoom-level')).to_have_text('125%')
        with page.expect_download() as download:
            page.locator("#download-pdf").click()
        exported = tmp_path / download.value.suggested_filename
        download.value.save_as(exported)
        assert exported.suffix == '.pdf'
        pdf = exported.read_bytes()
        assert pdf.startswith(b'%PDF-1.4')
        metadata_match = re.search(rb'/PotatoExpression <([0-9a-fA-F]+)>', pdf)
        assert metadata_match
        metadata = json.loads(bytes.fromhex(metadata_match[1].decode()).decode('utf-16'))
        assert metadata["rawTpm"]["root"] == 0
        assert metadata["rawTpm"]["flower"] == 40
        assert metadata["values"]["flower"] == float(page.locator('#plant [data-tissue="flower"]').get_attribute('data-expression'))
        assert next(row for row in metadata['tissueValues'] if row['tissue'] == 'flower')['diagramIds'] == ['flower']
        assert metadata["values"]["root"] < 0
        assert metadata["values"]["mature_tuber"] > 0
        assert metadata["rawTpm"]["mature_tuber"] == 80
        assert metadata["rawTpm"]["young_tuber_S3"] == 0
        assert metadata["rawTpm"]["young_tuber_S4"] == 4
        assert metadata["rawTpm"]["stolon_tip_S1"] == metadata["rawTpm"]["stolon"] == 12
        assert metadata["values"]["stolon_tip_S1"] == metadata["values"]["stolon"]
        stolon_row = next(row for row in metadata['tissueValues'] if row['tissue'] == 'stolon')
        assert stolon_row['diagramIds'] == ['stolon', 'stolon_tip_S1']
        assert 'stamen' not in json.dumps(metadata)
        assert metadata["transform"] == "row_zscore"
        assert len(metadata['tissueValues']) == 9
        assert next(row for row in metadata['tissueValues'] if row['tissue'] == 'flower')['rawTpm'] == 40
        # Font programs are embedded and graphics contain no raster images or
        # transparency masks. Validate real PDF operators, xref offsets and text.
        assert b'/Subtype /Image' not in pdf and b'/SMask' not in pdf
        assert pdf.count(b'/FontFile2 ') == 2
        xref = int(re.search(rb'startxref\n(\d+)', pdf)[1])
        assert pdf[xref:xref + 4] == b'xref'
        streams = []
        for match in re.finditer(rb'<< /Length (\d+)([^>]*) >>\nstream\n', pdf):
            content = pdf[match.end():match.end() + int(match[1])]
            streams.append(zlib.decompress(content) if b'/FlateDecode' in match[2] else content)
        drawing = next(content for content in streams if content.startswith(b'q 0.75'))
        assert drawing.count(b' c\n') > 3000
        assert b'(Z-score) Tj' in drawing and b'(GeneA) Tj' in drawing
        if shutil.which('pdftotext') and shutil.which('pdftoppm'):
            extracted = subprocess.check_output(['pdftotext', str(exported), '-'], text=True)
            assert 'GeneA' in extracted and 'Z-score' in extracted and 'stamen' not in extracted
            assert 'mature leaf' in extracted and 'immature small tuber' in extracted
            # Compare the complete PDF drawing with the original SVG layout,
            # even though the user zoomed the interactive viewport to 125%.
            reference = page.evaluate("""async () => {
                const {adaptExpression} = await import('/static/efp/expression.mjs?v=20260923-flower');
                const {prepareExpression} = await import('/static/efp/potato-efp.mjs?v=20260923-flower');
                const {exportSvg} = await import('/static/efp/export.mjs?v=20260923-pdf1');
                const response = await fetch('/api/bulk-rnaseq/expression?genes=GeneA&scope=tissue&transform=row_zscore');
                const data = adaptExpression(await response.json());
                return exportSvg(document.querySelector('#plant > svg'), prepareExpression(data.payload), data);
            }""")
            image_page = context.new_page()
            image_page.set_content('<body style="margin:0;background:white">' + reference + '</body>')
            reference_file = tmp_path / 'pdf-reference.png'
            image_page.locator('svg').first.screenshot(path=str(reference_file))
            prefix = tmp_path / 'pdf-rendered'
            subprocess.run(['pdftoppm', '-r', '96', '-singlefile', '-png', str(exported), str(prefix)], check=True, capture_output=True)
            from PIL import Image, ImageFilter
            reference_image = Image.open(reference_file).convert('RGB')
            rendered = Image.open(prefix.with_suffix('.png')).convert('RGB')
            colors = page.locator('#plant [data-tissue]').evaluate_all('nodes => nodes.map(node => node.getAttribute("fill"))')
            palette = {tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) for color in colors}
            region = (24, 205, 691, 1239)
            original_pixels = list(reference_image.crop(region).getdata())
            pdf_pixels = list(rendered.crop(region).getdata())
            mask = Image.new('L', (region[2] - region[0], region[3] - region[1]))
            mask.putdata([255 if pixel in palette else 0 for pixel in original_pixels])
            interior = list(mask.filter(ImageFilter.MinFilter(3)).getdata())
            compared = [max(abs(a-b) for a,b in zip(left,right)) for left,right,valid in zip(original_pixels,pdf_pixels,interior) if valid]
            assert len(compared) > 10000
            assert sum(delta <= 3 for delta in compared) / len(compared) > .995
            directory = Path(os.getenv('POTATO_EFP_SCREENSHOTS') or tmp_path)
            rendered.save(directory / 'efp-vector-pdf.png')
        assert not errors


def test_efp_pdf_asset_failure_can_retry(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        page = context.new_page()
        page.goto(site + '/efp?gene=GeneA')
        loaded(page, 'GeneA')
        page.route('**/pdf-geometry.json?**', lambda route: route.fulfill(status=503))
        page.locator('#download-pdf').click()
        expect(page.locator('#query-error')).to_contain_text('PDF export failed')
        expect(page.locator('#download-pdf')).to_be_enabled()
        expect(page.locator('#download-pdf')).to_have_text('PDF')
        page.unroute('**/pdf-geometry.json?**')
        with page.expect_download() as download:
            page.locator('#download-pdf').click()
        assert download.value.suggested_filename == 'GeneA_efp_log2_tpm.pdf'
        expect(page.locator('#query-error')).to_be_hidden()
        expect(page.locator('#download-pdf')).to_be_enabled()


@pytest.mark.parametrize('transform', ['tpm', 'log2_tpm', 'row_zscore'])
def test_efp_api_pdf_matches_browser_export(site, browser, tmp_path, transform):
    import httpx
    from interface.test_efp_api import drawing_stream, pdf_metadata

    with browser.new_context(accept_downloads=True) as context:
        page = context.new_page()
        page.goto(f'{site}/efp?gene=GeneA&transform={transform}')
        loaded(page, 'GeneA')
        with page.expect_download() as download:
            page.locator('#download-pdf').click()
        output = tmp_path / 'browser.pdf'
        download.value.save_as(output)
        browser_pdf = output.read_bytes()
    response = httpx.get(f'{site}/api/efp/export.pdf', params={'gene': 'GeneA', 'transform': transform}, timeout=30)
    assert response.status_code == 200
    assert pdf_metadata(response.content) == pdf_metadata(browser_pdf)
    assert drawing_stream(response.content) == drawing_stream(browser_pdf)


def test_efp_flower_regions_follow_annotation(site, browser):
    from playwright.sync_api import expect

    payload = {
        'scope': 'tissue', 'transform': 'tpm', 'genes': [{'geneId': 'FloralGene'}],
        'columns': [{'tissue': tissue} for tissue in ['flower', 'perianth', 'anther', 'flower bud']],
        'values': [[40, 2, 10, 20]], 'rawValues': [[40, 2, 10, 20]],
    }
    with browser.new_context(viewport={'width': 1440, 'height': 1100}) as context:
        page = context.new_page()
        page.route('**/api/bulk-rnaseq/expression?**', lambda route: route.fulfill(json=payload))
        page.goto(site + '/efp?gene=FloralGene&transform=tpm')
        loaded(page, 'FloralGene')
        # SVG coordinates: petals and centers of both annotated open flowers,
        # then the unmarked side-facing flower and a nearby flower bud.
        points = [
            (269, 78, 'flower', 'Flower', 40), (286, 81, 'flower', 'Flower', 40),
            (311, 125, 'flower', 'Flower', 40), (331, 135, 'flower', 'Flower', 40),
            (384, 120, 'perianth', 'Perianth', 2), (397, 126, 'anther', 'Anther', 10),
            (199, 181, 'flower_bud', 'Flower bud', 20),
        ]
        for x, y, tissue, label, value in points:
            screen = page.locator('#plant > svg').evaluate('''(svg, point) => {
                const screen = new DOMPoint(...point).matrixTransform(svg.getScreenCTM());
                const tissue = document.elementFromPoint(screen.x, screen.y)?.closest('[data-tissue]')?.dataset.tissue;
                return {x: screen.x, y: screen.y, tissue};
            }''', [x, y])
            assert screen['tissue'] == tissue
            page.mouse.move(screen['x'], screen['y'])
            expect(page.locator('#tissue-tooltip')).to_have_text(f'{label}\nTPM: {value}')
        flower = page.locator('#plant [data-tissue="flower"]')
        perianth = page.locator('#plant [data-tissue="perianth"]')
        for value, expected_color in [(0, '#FFFFCC'), (None, '#C9CED0')]:
            payload['values'][0][0] = value
            payload['rawValues'][0][0] = value
            search(page, 'FloralGene')
            loaded(page, 'FloralGene')
            expect(flower).to_have_attribute('fill', expected_color)
            # The shared scale can change when flower is no longer the maximum;
            # perianth must still keep its own value and remain colored.
            expect(perianth).to_have_attribute('data-expression', '2')
            expect(perianth).not_to_have_attribute('fill', '#C9CED0')


def test_efp_stolon_tip_stays_independent_and_updates_labels(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        page = context.new_page()
        page.goto(site + '/efp?gene=GeneA&transform=tpm')
        loaded(page, 'GeneA')
        stolon = page.locator('#plant [data-tissue="stolon"]')
        tip = page.locator('#plant [data-tissue="stolon_tip_S1"]')
        expect(stolon).to_have_count(1)
        expect(tip).to_have_count(1)
        expect(tip).to_have_attribute('data-label', 'Stolon')
        expect(tip).to_have_attribute('data-expression', '12')
        tip_id = tip.get_attribute('id')
        assert tip_id and tip_id != stolon.get_attribute('id')
        payload = {
            'scope': 'tissue', 'transform': 'tpm', 'genes': [{'geneId': 'GeneA'}],
            'columns': [{'tissue': 'stolon'}, {'tissue': 'stolon tip'}],
        }
        page.route('**/api/bulk-rnaseq/expression?**', lambda route: route.fulfill(json=payload))
        for value in [7, 0, None]:
            payload.update(values=[[12, value]], rawValues=[[12, value]])
            search(page, 'GeneA')
            loaded(page, 'GeneA')
            expect(stolon).to_have_attribute('data-expression', '12')
            expect(tip).to_have_attribute('data-expression', 'NA' if value is None else str(value))
            expect(tip).to_have_attribute('data-label', 'Stolon tip (S1)')
            expect(tip).to_have_attribute('id', tip_id)
            tip.focus()
            expect(page.locator('#tissue-tooltip')).to_contain_text('Stolon tip (S1)')
            expect(page.locator('#tissue-tooltip')).to_contain_text('TPM: NA' if value is None else f'TPM: {value}')
        # Removing the separate tip source restores the temporary label and
        # stolon value on the same SVG region, with no stale hover text.
        payload.update(columns=[{'tissue': 'stolon'}], values=[[12]], rawValues=[[12]])
        search(page, 'GeneA')
        loaded(page, 'GeneA')
        expect(tip).to_have_attribute('data-label', 'Stolon')
        expect(tip).to_have_attribute('data-expression', '12')
        expect(tip).to_have_attribute('id', tip_id)
        tip.focus()
        expect(page.locator('#tissue-tooltip')).to_have_text('Stolon\nTPM: 12')


def test_efp_clears_stale_data_and_handles_constant_expression(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        page = context.new_page()
        page.goto(site + "/efp?gene=GeneA")
        loaded(page, "GeneA")
        search(page, "Unknown")
        expect(page.locator("#query-error")).to_contain_text("Gene not found")
        expect(page.locator("#plant")).to_be_hidden()
        expect(page.locator("#efp-legend")).to_be_hidden()
        expect(page.locator("#download-pdf")).to_be_disabled()
        expect(page.locator('#tissue-rows tr')).to_have_count(0)
        expect(page.locator('#zoom-in')).to_be_disabled()
        search(page, "GeneZero")
        loaded(page, "GeneZero")
        expect(page.locator('#plant [data-tissue="stem"]')).to_have_attribute("data-expression", "0")
        page.locator("#transform-select").select_option("row_zscore")
        expect(page.locator("#legend-unit")).to_have_text("Z-score")
        expect(page.locator('#plant [data-tissue="root"]')).to_have_attribute("fill", "#F7F7F7")
        search(page, "GeneConstant")
        loaded(page, "GeneConstant")
        expect(page.locator('#plant [data-tissue="root"]')).to_have_attribute("data-expression", "0")
        search(page, "GeneA GeneZero")
        expect(page.locator("#query-error")).to_contain_text("one gene ID")
        expect(page.locator("#download-pdf")).to_be_disabled()
        search(page, "GeneA")
        loaded(page, "GeneA")
        page.route("**/api/bulk-rnaseq/expression?**", lambda route: route.fulfill(status=503, json={"detail":"Unavailable"}))
        page.locator("#transform-select").select_option("tpm")
        expect(page.locator("#query-error")).to_contain_text("unavailable")
        expect(page.locator("#plant")).to_be_hidden()
        expect(page.locator("#download-pdf")).to_be_disabled()


def test_efp_newer_query_wins(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        page = context.new_page()
        page.goto(site + "/efp?gene=GeneA")
        loaded(page, "GeneA")
        pending = []
        page.route("**/api/bulk-rnaseq/expression?genes=Slow&**", lambda route: pending.append(route))
        search(page, "Slow")
        expect(page.locator("#download-pdf")).to_be_disabled()
        expect(page.locator("#plant")).to_be_hidden()
        search(page, "GeneZero")
        loaded(page, "GeneZero")
        assert pending
        pending[0].fulfill(status=404, json={"detail":"Old query"})
        expect(page.locator("#query-error")).to_be_hidden()
        expect(page.locator("#graph-title")).to_have_text("GeneZero")


@pytest.mark.parametrize("width", [390, 820])
def test_efp_responsive_layout(site, browser, tmp_path, width):
    with browser.new_context(viewport={"width": width, "height": 900}) as context:
        page = context.new_page()
        page.goto(site + "/efp?gene=GeneA")
        loaded(page, "GeneA")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator('#zoom-in').click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator('#tissue-rows tr').count() == 9
        screenshot(page, tmp_path, f"efp-{width}")


def test_efp_zoom_pan_reset_and_limits(site, browser):
    from playwright.sync_api import expect

    with browser.new_context(viewport={'width': 1440, 'height': 1000}) as context:
        page = context.new_page()
        page.goto(site + '/efp?gene=GeneA')
        loaded(page, 'GeneA')
        svg = page.locator('#plant > svg')
        viewport = page.locator('#map-viewport')
        original = svg.get_attribute('viewBox')
        values = page.locator('#tissue-rows').inner_text()
        # Compare within the figure: a taller figure can scroll the page when
        # Playwright brings its zoom buttons into view.
        legend_geometry = 'e => ({left:e.offsetLeft, top:e.offsetTop, width:e.offsetWidth, height:e.offsetHeight})'
        legend = page.locator('#efp-legend').evaluate(legend_geometry)
        page.locator('#zoom-in').click()
        expect(page.locator('#zoom-level')).to_have_text('125%')
        assert svg.get_attribute('viewBox') != original
        assert page.locator('#efp-legend').evaluate(legend_geometry) == legend
        before_drag = svg.get_attribute('viewBox')
        box = viewport.bounding_box()
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down()
        page.mouse.move(box['x'] + box['width'] / 2 + 60, box['y'] + box['height'] / 2 + 40, steps=5)
        page.mouse.up()
        assert svg.get_attribute('viewBox') != before_drag
        assert viewport.get_attribute('data-dragging') == 'false'
        page.locator('#zoom-reset').click()
        expect(svg).to_have_attribute('viewBox', original)
        viewport.hover()
        page.mouse.wheel(0, -200)
        expect(svg).not_to_have_attribute('viewBox', original)
        viewport.focus()
        page.keyboard.press('0')
        expect(page.locator('#zoom-level')).to_have_text('100%')
        for _ in range(8):
            page.keyboard.press('+')
        expect(page.locator('#zoom-level')).to_have_text('400%')
        expect(page.locator('#zoom-in')).to_be_disabled()
        for _ in range(12):
            page.keyboard.press('-')
        expect(page.locator('#zoom-level')).to_have_text('50%')
        expect(page.locator('#zoom-out')).to_be_disabled()
        assert page.locator('#tissue-rows').inner_text() == values
        search(page, 'GeneZero')
        loaded(page, 'GeneZero')
        expect(page.locator('#zoom-level')).to_have_text('100%')

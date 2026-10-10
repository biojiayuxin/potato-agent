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
        "perianth", "anther", "carpel",
    ]
    (source / "sample_tissue_list.tsv").write_text("sample_column\tsample_name\ttissue\n" + "".join(
        f"S{i}\tMaterialA\t{tissue}\n" for i, tissue in enumerate(tissues)
    ))
    (source / "transcript_tpm_matrix_merged.tsv").write_text(
        "transcript_id\tgene_id\tgene_name\t" + "\t".join(f"S{i}" for i in range(len(tissues))) + "\n"
        "TxA\tGeneA\t\t0\t2\t10\t20\t40\t80\t12\t160\t0\t4\t2\t10\t30\n"
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
        expect(page.locator('#tissue-rows tr')).to_have_count(12)
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
            "Gene Expression", "Tissue Expression Map", "Co-expression network", "Spatial Expression",
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
        for tissue, value in [('perianth', 2), ('anther', 10), ('carpel', 30)]:
            assert metadata['rawTpm'][tissue] == value
            assert next(row for row in metadata['tissueValues'] if row['tissue'] == tissue)['diagramIds'] == [tissue]
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
        assert len(metadata['tissueValues']) == 12
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
            reference = page.evaluate(r"""async () => {
                const {adaptExpression} = await import('/static/efp/expression.mjs');
                const {prepareExpression} = await import('/static/efp/potato-efp.mjs');
                const {exportSvg} = await import('/static/efp/export.mjs');
                const {buildFigure} = await import('/static/efp/figure.mjs');
                const response = await fetch('/api/bulk-rnaseq/expression?genes=GeneA&scope=tissue&transform=row_zscore');
                const data = adaptExpression(await response.json());
                const plant = document.querySelector('#plant > svg');
                const result = prepareExpression(data.payload);
                const figure = buildFigure(result, data);
                const drawing = figure.elements.find(element => element.type === 'plant').attrs;
                const viewBox = plant.dataset.originalViewBox.split(/\s+/).map(Number);
                const scale = Math.min(drawing.width / viewBox[2], drawing.height / viewBox[3]);
                const left = drawing.x + (drawing.width - scale * viewBox[2]) / 2;
                const top = drawing.y + (drawing.height - scale * viewBox[3]) / 2;
                const region = (x, y, width, height) => [left + x * scale, top + y * scale,
                    left + (x + width) * scale, top + (y + height) * scale].map(Math.round);
                return {svg: exportSvg(plant, result, data), regions: [
                    region(0, 0, viewBox[2], viewBox[3]),
                    region(550, 35, 793.7 * .7, 291.8 * .7),
                ], petalRegion: region(674, 160, 22, 12)};
            }""")
            image_page = context.new_page()
            image_page.set_content('<body style="margin:0;background:white">' + reference['svg'] + '</body>')
            reference_file = tmp_path / 'pdf-reference.png'
            image_page.locator('svg').first.screenshot(path=str(reference_file))
            prefix = tmp_path / 'pdf-rendered'
            subprocess.run(['pdftoppm', '-r', '96', '-singlefile', '-png', str(exported), str(prefix)], check=True, capture_output=True)
            from PIL import Image, ImageFilter
            reference_image = Image.open(reference_file).convert('RGB')
            rendered = Image.open(prefix.with_suffix('.png')).convert('RGB')
            colors = page.locator('#plant [data-tissue]').evaluate_all('nodes => nodes.map(node => node.getAttribute("fill"))')
            palette = {tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) for color in colors}
            assert reference_image.size == rendered.size
            for region, minimum_pixels in zip(reference['regions'], [10000, 300]):
                original_pixels = list(reference_image.crop(region).getdata())
                pdf_pixels = list(rendered.crop(region).getdata())
                mask = Image.new('L', (region[2] - region[0], region[3] - region[1]))
                mask.putdata([255 if pixel in palette else 0 for pixel in original_pixels])
                interior = list(mask.filter(ImageFilter.MinFilter(3)).getdata())
                compared = [max(abs(a-b) for a,b in zip(left,right)) for left,right,valid in zip(original_pixels,pdf_pixels,interior) if valid]
                assert len(compared) > minimum_pixels
                assert sum(delta <= 3 for delta in compared) / len(compared) > .995
            # The detail artwork also contains filled black texture and thin
            # outlines; comparing only tissue interiors misses lost linework.
            # Allow one pixel for Chromium/Poppler antialiasing differences.
            detail_region = reference['regions'][1]
            reference_ink = [max(pixel) <= 80 for pixel in reference_image.crop(detail_region).getdata()]
            pdf_detail = rendered.crop(detail_region)
            pdf_ink = Image.new('L', pdf_detail.size)
            pdf_ink.putdata([255 if max(pixel) <= 128 else 0 for pixel in pdf_detail.getdata()])
            nearby_ink = list(pdf_ink.filter(ImageFilter.MaxFilter(3)).getdata())
            assert sum(reference_ink) > 250
            assert sum(found > 0 for expected, found in zip(reference_ink, nearby_ink) if expected) / sum(reference_ink) > .98
            # A small hole in overlapping petal paths can pass the full-figure
            # tolerance. Compare this area separately at eight times the scale.
            left, top, right, bottom = reference['petalRegion']
            width, height = right - left, bottom - top
            image_page.locator('svg').first.evaluate('''(svg, region) => {
                const [x, y, width, height] = region;
                svg.setAttribute('viewBox', `${x} ${y} ${width} ${height}`);
                svg.setAttribute('width', width * 8);
                svg.setAttribute('height', height * 8);
            }''', [left, top, width, height])
            petal_reference = tmp_path / 'petal-reference.png'
            image_page.locator('svg').first.screenshot(path=str(petal_reference))
            petal_prefix = tmp_path / 'petal-pdf'
            subprocess.run(['pdftoppm', '-r', '768', '-x', str(left * 8), '-y', str(top * 8),
                            '-W', str(width * 8), '-H', str(height * 8), '-singlefile', '-png',
                            str(exported), str(petal_prefix)], check=True, capture_output=True)
            petal_svg = Image.open(petal_reference).convert('RGB')
            petal_pdf = Image.open(petal_prefix.with_suffix('.png')).convert('RGB')
            assert petal_svg.size == petal_pdf.size
            mask = Image.new('L', petal_svg.size)
            mask.putdata([255 if pixel in palette else 0 for pixel in petal_svg.getdata()])
            interior = mask.filter(ImageFilter.MinFilter(3)).getdata()
            compared = [max(abs(a-b) for a, b in zip(left, right))
                        for left, right, valid in zip(petal_svg.getdata(), petal_pdf.getdata(), interior) if valid]
            assert len(compared) > 1000
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
    from playwright.sync_api import expect
    from interface.test_efp_api import drawing_stream, pdf_metadata

    with browser.new_context(accept_downloads=True) as context:
        page = context.new_page()
        page.goto(f'{site}/efp?gene=GeneA&transform={transform}')
        loaded(page, 'GeneA')
        for tissue, raw_value in [('flower', 40), ('perianth', 2), ('anther', 10), ('carpel', 30)]:
            group = page.locator(f'#plant [data-tissue="{tissue}"]')
            row = page.locator(f'#tissue-rows tr[data-tissue="{tissue}"]')
            expect(row).to_have_attribute('data-mapped', 'true')
            expect(row.locator('td').nth(1)).to_have_text(str(raw_value))
            displayed_value = row.locator('td').nth(1 if transform == 'tpm' else 2).inner_text()
            expect(group).to_have_attribute('data-expression', displayed_value)
            group.focus()
            expect(page.locator('#tissue-tooltip')).to_contain_text(f'TPM: {raw_value}')
            if transform != 'tpm':
                unit = 'log2(TPM + 1)' if transform == 'log2_tpm' else 'Z-score'
                expect(page.locator('#tissue-tooltip')).to_contain_text(f'{unit}: {displayed_value}')
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
        'columns': [{'tissue': tissue} for tissue in ['flower', 'perianth', 'anther', 'carpel', 'flower bud']],
        'values': [[40, 2, 10, 30, 20]], 'rawValues': [[40, 2, 10, 30, 20]],
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
        # Original coordinates stay unchanged. New coordinates are the source
        # drawing at translate(550, 35) scale(.7): whole flower, four detached
        # anthers and the detached carpel. Thin central regions deliberately
        # exercise SVG hit testing rather than synthetic group hover events.
        detail_points = [
            (80, 180, 'perianth', 'Perianth', 2),
            (200, 135, 'perianth', 'Perianth', 2),
            (150, 150, 'anther', 'Anther', 10),
            (140, 145, 'carpel', 'Carpel', 30),
            (340, 150, 'anther', 'Anther', 10),
            (400, 150, 'anther', 'Anther', 10),
            (455, 150, 'anther', 'Anther', 10),
            (570, 160, 'anther', 'Anther', 10),
            (686, 180, 'carpel', 'Carpel', 30),
            (690, 100, 'carpel', 'Carpel', 30),
        ]
        points.extend((550 + .7*x, 35 + .7*y, tissue, label, value)
                      for x, y, tissue, label, value in detail_points)
        for x, y, tissue, label, value in points:
            screen = page.locator('#plant > svg').evaluate('''(svg, point) => {
                const screen = new DOMPoint(...point).matrixTransform(svg.getScreenCTM());
                const target = document.elementFromPoint(screen.x, screen.y);
                const group = target?.closest('[data-tissue]');
                return {x: screen.x, y: screen.y, tissue: group?.dataset.tissue,
                    fill: target && getComputedStyle(target).fill,
                    tissueFill: group && getComputedStyle(group).fill};
            }''', [x, y])
            assert screen['tissue'] == tissue
            assert screen['fill'] == screen['tissueFill']
            page.mouse.move(screen['x'], screen['y'])
            expect(page.locator('#tissue-tooltip')).to_have_text(f'{label}\nTPM: {value}')
        groups = {tissue: page.locator(f'#plant [data-tissue="{tissue}"]')
                  for tissue in ['flower', 'perianth', 'anther', 'carpel']}
        for tissue, group in groups.items():
            expect(group).to_have_count(1)
            expect(group).to_have_attribute('tabindex', '0')
            group.focus()
            expect(page.locator('#tissue-tooltip')).to_contain_text(tissue.capitalize())
            page.keyboard.press('Escape')
            expect(page.locator('#tissue-tooltip')).to_be_hidden()
        expect(page.locator('#plant [data-tissue="pistil"]')).to_have_count(0)
        # Each floral value can be zero or missing without borrowing another
        # region's value; the whole flower keeps its independent API source.
        original = [40, 2, 10, 30, 20]
        for index, tissue in enumerate(groups):
            for value, expected_color in [(0, '#FFFFCC'), (None, '#C9CED0')]:
                raw = original.copy()
                raw[index] = value
                payload.update(values=[raw], rawValues=[raw])
                search(page, 'FloralGene')
                loaded(page, 'FloralGene')
                expect(groups[tissue]).to_have_attribute('fill', expected_color)
                expect(groups[tissue]).to_have_attribute('data-expression', 'NA' if value is None else '0')
                groups[tissue].focus()
                expect(page.locator('#tissue-tooltip')).to_contain_text('TPM: NA' if value is None else 'TPM: 0')
                for other_index, other in enumerate(groups):
                    if other != tissue:
                        expect(groups[other]).to_have_attribute('data-expression', str(original[other_index]))
                        expect(groups[other]).not_to_have_attribute('fill', '#C9CED0')


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
        stage = page.locator('.efp-stage').bounding_box()
        table = page.locator('#tissue-panel').bounding_box()
        assert table['y'] >= stage['y'] + stage['height']
        screenshot(page, tmp_path, f"efp-{width}")
        page.locator('#zoom-in').click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator('#tissue-rows tr').count() == 12
        screenshot(page, tmp_path, f"efp-{width}-zoom")


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

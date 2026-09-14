"""Run with POTATO_SPATIAL_BROWSER_TESTS=1 and Playwright/Chromium installed.

POTATO_SPATIAL_SCREENSHOTS selects a persistent screenshot directory.
POTATO_PLAYWRIGHT_EXECUTABLE optionally selects an existing Chromium binary.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from interface.spatial_viewer import router
from interface.test_spatial_viewer import _build_spatial_fixture, _write_json


pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_SPATIAL_BROWSER_TESTS") != "1",
    reason="Opt-in browser suite; requires Playwright and Chromium",
)
STATIC = Path(__file__).parent / "static"
NEW_GENE = "DM8.2_chr03G22620"
OLD_GENE = "Soltu.DM.03G024100"


def build_preview_data(root: Path) -> None:
    _build_spatial_fixture(root)
    _build_spatial_fixture(root / "second")
    with sqlite3.connect(root / "data" / "expression.sqlite") as conn:
        conn.execute("UPDATE genes SET gene = ? WHERE gene_id = 1", (OLD_GENE,))
    catalog = json.loads((root / "datasets.json").read_text())
    catalog["datasets"][0]["defaultGene"] = OLD_GENE
    catalog["datasets"].append({
        **catalog["datasets"][0], "id": "second", "label": "Second Dataset",
        "dataRoot": "second/data",
    })
    _write_json(root / "datasets.json", catalog)
    replicates = json.loads((root / "data/replicates.json").read_text())
    for sample, rows in replicates["samples"].items():
        rows[0]["bbox"] = [0, 0, 999, 999]
        rows[0]["cellIds"] = rows[0].pop("cells")
        rows[0]["assignedCellCount"] = len(rows[0]["cellIds"])
        rows[0]["tileKeys"] = ["0,0"]
        rows[0]["label"] = rows[0]["id"]
        _write_json(root / f"data/contours/{sample}/manifest.json", {
            "sample": sample, "width": 1000, "height": 1000, "tileSize": 1000,
            "tiles": [{"x": 0, "y": 0, "url": f"/data/contours/{sample}/tile_0_0.json"}],
        })
        _write_json(root / f"data/contours/{sample}/tile_0_0.json", {
            "cells": [
                {"id": cell, "bbox": [100 + i * 250, 100, 300 + i * 250, 300],
                 "contours": [[[0, 0], [200, 0], [200, 200], [0, 200]]]}
                for i, cell in enumerate(rows[0]["cellIds"])
            ],
        })
        replicates["samples"][sample] = {"replicates": rows}
    _write_json(root / "data/replicates.json", replicates)


def preview_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


@pytest.fixture
def site(tmp_path, monkeypatch):
    import uvicorn

    build_preview_data(tmp_path)
    monkeypatch.setenv("SPATIAL_VIEWER_DATA_ROOT", str(tmp_path))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(preview_app(), log_level="error", lifespan="off"))
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


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch(
            executable_path=os.getenv("POTATO_PLAYWRIGHT_EXECUTABLE") or None,
            args=["--no-sandbox"],
        )
        yield instance
        instance.close()


def screenshot(page, tmp_path, name):
    directory = Path(os.getenv("POTATO_SPATIAL_SCREENSHOTS") or tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / f"{name}.png"), full_page=True)


def search(page, gene):
    page.locator("#geneInput").fill(gene)
    page.locator("#geneForm button").click()


def wait_loaded(page, gene):
    from playwright.sync_api import expect

    expect(page.locator("#resultGene")).to_have_text(gene)
    expect(page.locator("#exportPdf")).to_be_enabled()
    page.wait_for_function("currentSpatial().loadedTiles.size > 0")
    expect(page.locator("#queryStatus")).to_contain_text("cells drawn")
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def assert_cleared(page):
    from playwright.sync_api import expect

    expect(page.locator("#geneResult")).to_be_hidden()
    expect(page.locator("#exportPdf")).to_be_disabled()
    assert page.evaluate("Object.keys(state.expressions).length") == 0
    assert page.evaluate("state.dotplot.payload") is None
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert page.locator("#plot").evaluate("""canvas => {
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] > pixels[i+1] * 1.5 && pixels[i] > pixels[i+2] * 1.5) return false;
      }
      return true;
    }""")


@pytest.mark.parametrize("width", [1440, 820])
def test_spatial_mapped_labels_pdf_and_agent(browser, site, tmp_path, width):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": width, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site + "/spatial")
    wait_loaded(page, OLD_GENE)
    search(page, NEW_GENE)
    wait_loaded(page, NEW_GENE)
    expect(page.locator("#geneInput")).to_have_value(NEW_GENE)
    expect(page.locator("#mappedGene")).to_have_text(f"DMv6.1 data: {OLD_GENE}")
    assert page.evaluate("state.currentGene") == OLD_GENE
    assert page.locator("#plot").evaluate("""canvas => {
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let colored = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i] > pixels[i+1] * 1.5 && pixels[i] > pixels[i+2] * 1.5) colored++;
      }
      return colored > 100;
    }""")
    assert page.locator("#geneResult").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(page, tmp_path, f"spatial-mapped-{width}")

    with page.expect_download() as download_info:
        page.locator("#exportPdf").click()
    download = download_info.value
    assert download.suggested_filename.startswith(NEW_GENE + "_")
    pdf_path = tmp_path / download.suggested_filename
    download.save_as(pdf_path)
    assert pdf_path.read_bytes().startswith(b"%PDF-1.4")
    page.route("**/chat**", lambda route: route.fulfill(body="Agent preview"))
    page.locator("#ask-potato-agent").click()
    page.wait_for_url("**/chat#example=**")
    intent = json.loads(unquote(urlsplit(page.url).fragment.removeprefix("example=")))
    assert OLD_GENE in intent["text"]
    assert NEW_GENE not in intent["text"]
    assert not errors
    page.close()


def test_spatial_failures_clear_results_and_stale_queries(browser, site, tmp_path):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(site + "/spatial")
    wait_loaded(page, OLD_GENE)
    for gene, message in [("DM8.2_chr01G03840", "multiple DMv6.1"), ("DM8.2_missing", "No valid"), ("", "Enter a gene ID")]:
        search(page, gene)
        expect(page.locator("#queryStatus")).to_contain_text(message)
        assert_cleared(page)
        screenshot(page, tmp_path, f"spatial-query-error-{gene or 'empty'}")
        search(page, NEW_GENE)
        wait_loaded(page, NEW_GENE)

    held = []

    def hold_mapped(route):
        if parse_qs(urlsplit(route.request.url).query).get("gene") == [NEW_GENE]:
            held.append((route, route.fetch()))
        else:
            route.continue_()

    page.route("**/api/spatial/gene?**", hold_mapped)
    page.route("**/api/spatial/dotplot?**", hold_mapped)
    page.evaluate("() => { window.pendingQuery = queryGene('DM8.2_chr03G22620'); }")
    expect(page.locator("#exportPdf")).to_be_disabled()
    page.wait_for_function("state.dotplot.loading")
    search(page, "DM8.2_missing")
    expect(page.locator("#queryStatus")).to_contain_text("No valid")
    assert len(held) == 2
    for route, response in held:
        route.fulfill(response=response)
    page.evaluate("window.pendingQuery")
    assert_cleared(page)
    expect(page.locator("#queryStatus")).to_contain_text("No valid")
    page.unroute_all(behavior="wait")

    search(page, NEW_GENE)
    wait_loaded(page, NEW_GENE)
    page.route("**/api/spatial/dotplot?**", lambda route: route.abort())
    search(page, NEW_GENE)
    expect(page.locator("#queryStatus")).to_have_class("query-status is-error")
    assert_cleared(page)
    page.unroute_all(behavior="wait")
    search(page, NEW_GENE)
    wait_loaded(page, NEW_GENE)
    held.clear()
    page.route("**/api/spatial/gene?**", hold_mapped)
    page.route("**/api/spatial/dotplot?**", hold_mapped)
    page.evaluate("() => { window.pendingQuery = queryGene('DM8.2_chr03G22620'); }")
    page.locator("#datasetSelect").select_option("second")
    expect(page.locator("#queryStatus")).to_contain_text("not found in dataset second")
    assert len(held) == 2
    for route, response in held:
        route.fulfill(response=response)
    page.evaluate("window.pendingQuery")
    page.unroute_all(behavior="wait")
    assert_cleared(page)
    search(page, NEW_GENE)
    expect(page.locator("#queryStatus")).to_contain_text("maps to DMv6.1 gene")
    assert_cleared(page)
    page.locator("#datasetSelect").select_option("toy")
    wait_loaded(page, OLD_GENE)
    page.close()


def test_spatial_mobile_keeps_existing_display_requirement(browser, site, tmp_path):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(site + "/spatial")
    expect(page).to_have_url(site + "/static/lite/high-resolution-required.html?module=spatial")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(page, tmp_path, "spatial-mobile-display-requirement")
    page.close()

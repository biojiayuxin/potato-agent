"""Opt-in public annotation UI checks; no production data or runtime is used.

POTATO_ANNOTATION_BROWSER_TESTS=1 enables Playwright Chromium.
POTATO_ANNOTATION_SCREENSHOTS sets a directory for review screenshots.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from interface.test_dashboard_browser import browser, site  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.getenv("POTATO_ANNOTATION_BROWSER_TESTS") != "1", reason="Opt-in annotation browser suite"
)

ASSEMBLIES = [
    {"assemblyId": "monoploid/DMv8.2", "label": "DMv8.2", "browserAssemblyId": "monoploid/DMv8.2"},
    {"assemblyId": "monoploid/E4-63", "label": "E4-63", "browserAssemblyId": "monoploid/E4-63"},
    {"assemblyId": "monoploid/A6-26", "label": "A6-26", "browserAssemblyId": "monoploid/A6-26"},
    {"assemblyId": "phased_tetraploid/Des", "label": "Des", "browserAssemblyId": "phased_tetraploid/Des"},
    {"assemblyId": "phased_tetraploid/C88", "label": "C88", "browserAssemblyId": "phased_tetraploid/C88"},
]
FULL_ASSEMBLIES = [
    {"assemblyId": f"diploid/additional-{index}", "label": f"Additional genome {index}"}
    for index in range(149)
] + list(reversed(ASSEMBLIES))


def gene(index=1):
    return {"assemblyId": ASSEMBLIES[0]["assemblyId"], "geneId": f"DM8.2_chr05G{index:05d}",
            "transcriptCount": 2, "matchedTranscriptCount": 1,
            "matchedTranscriptIds": [f"DM8.2_chr05G{index:05d}.1"], "tfFamilies": ["WRKY"],
            "confidenceGrades": ["A"], "geneDecision": "selected_with_isoform_conflict",
            "isoformPresenceConflict": True, "familyConflict": False,
            "annotationStatus": "mixed", "signatures": ["PF03106"], "signatureCount": 1}


def transcript(index=1, no_cds=False):
    row = gene(index)
    return {**row, "transcriptId": row["geneId"] + (".2" if no_cds else ".1"),
            "proteinId": None if no_cds else "P000001", "proteinLength": None if no_cds else 420,
            "annotationStatus": "no_cds" if no_cds else "hit", "decisionStatus": "unassessable" if no_cds else "selected",
            "tfFamilies": [] if no_cds else ["WRKY"], "confidenceGrades": [] if no_cds else ["A"]}


def mock_annotations(context, *, unavailable=False, export_error=False, assemblies=None):
    calls = []

    def handle(route):
        request = route.request
        path = urlsplit(request.url).path
        payload = None
        if request.method == "POST":
            payload = (json.loads(parse_qs(request.post_data)["payload"][0])
                       if path.endswith("/export-download") else request.post_data_json)
        calls.append((path, payload))
        if unavailable:
            route.fulfill(status=503, json={"detail": "Annotation release is not configured."})
        elif path.endswith("/metadata"):
            route.fulfill(json={"datasetVersion": "test-release", "assemblies": FULL_ASSEMBLIES if assemblies is None else assemblies,
                                "method": "Local PlantTFDB rule evaluation", "limitations": ["Unavailable custom HMM families are not assessable."]})
        elif path.endswith("/query"):
            offset = payload.get("offset", 0)
            items = ([transcript()] if payload["view"] == "transcripts" else [gene(offset + 1)])
            total = 31 if payload.get("tfStatus") == "selected" else 51
            for item in items:
                item["assemblyId"] = payload["assemblyIds"][0]
            route.fulfill(json={"datasetVersion": "test-release", "items": items, "total": total,
                                "offset": offset, "limit": payload["limit"], "returned": 1, "hasMore": offset + payload["limit"] < total,
                                "idReport": {"unmatchedIds": ["UNKNOWN"] if payload.get("ids") else [], "ambiguousIds": [], "filteredIds": []}})
        elif "/genes/" in path:
            route.fulfill(json={"datasetVersion": "test-release", "gene": gene(), "transcripts": [transcript(), transcript(no_cds=True)]})
        elif "/transcripts/" in path:
            no_cds = path.endswith(".2")
            hits = [] if no_cds else [
                {"hitId": 1, "analysis": "Pfam", "signatureAccession": "PF03106", "signatureDescription": "WRKY DNA-binding domain",
                 "start": 20, "end": 110, "score": "2.1E-18", "interproAccession": "IPR003657", "interproDescription": "WRKY",
                 "goTerms": "GO:0003700", "pathways": "Reactome: R-TEST; " * 500},
                {"hitId": 2, "analysis": "Pfam", "signatureAccession": "PF03106", "signatureDescription": "Overlapping evidence",
                 "start": 45, "end": 130, "score": "0.00003", "interproAccession": "IPR003657", "goTerms": "", "pathways": ""},
            ]
            route.fulfill(json={"datasetVersion": "test-release", "transcript": transcript(no_cds=no_cds), "matches": hits,
                                "tfEvidence": [{"family": "WRKY", "confidence_grade": "A", "evidence": "PF03106"}],
                                "proteinDecision": {"decision_status": "selected"}})
        elif path.endswith("/export-download"):
            if export_error:
                route.fulfill(status=409, json={"detail": "Annotation release changed."})
                return
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("metadata.json", json.dumps(payload))
                target.writestr("genes.tsv", "gene_id\nDM8.2_chr05G00001\n")
            route.fulfill(body=archive.getvalue(), headers={"Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="annotations.zip"'})
        else:
            route.fulfill(status=404, json={"detail": "Unexpected test API request"})

    context.route("**/api/genome-annotations/**", handle)
    context.route("**/api/genome-browser/features/resolve?*", lambda route: route.fulfill(json={"gene": {"refName": "chr05", "start": 100, "end": 400}}))
    return calls


def screenshot(page, name, tmp_path):
    directory = Path(os.getenv("POTATO_ANNOTATION_SCREENSHOTS", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / name), full_page=True)


def test_full_metadata_keeps_original_genomes_and_bounds_all_export(site, browser):
    from playwright.sync_api import expect

    with browser.new_context(accept_downloads=True) as context:
        calls = mock_annotations(context)
        page = context.new_page()
        page.goto(site + "/functional-annotation")
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        expect(page.locator("#assembly-options label span")).to_have_text([
            "DMv8.2", "E4-63", "A6-26", "Désirée", "C88"])
        assert len(FULL_ASSEMBLIES) == 154
        assert page.locator("#assembly-options input:checked").evaluate_all("nodes => nodes.map(n => n.value)") == [ASSEMBLIES[0]["assemblyId"]]
        page.locator("#all-assemblies").click()
        page.locator("#search-button").click()
        expected_ids = [assembly["assemblyId"] for assembly in ASSEMBLIES]
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        assert [payload for path, payload in calls if path.endswith("/query")][-1]["assemblyIds"] == expected_ids
        assert parse_qs(urlsplit(page.url).query)["assemblyIds"] == expected_ids
        page.locator("#export-all").click()
        with page.expect_download():
            page.locator("#download-export").click()
        payload = [payload for path, payload in calls if path.endswith("/export-download")][-1]
        assert payload["query"]["assemblyIds"] == expected_ids
        page.locator("#close-export").click()
        page.locator("#assembly-options input").evaluate_all("nodes => nodes.forEach(n => n.checked = false)")
        queries_before = sum(path.endswith("/query") for path, _ in calls)
        page.locator("#search-button").click()
        expect(page.locator("#query-status")).to_have_text("Select at least one genome.")
        assert sum(path.endswith("/query") for path, _ in calls) == queries_before
        page.locator("#reset-search").click()
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        assert [payload for path, payload in calls if path.endswith("/query")][-1]["assemblyIds"] == [ASSEMBLIES[0]["assemblyId"]]


@pytest.mark.parametrize("assembly_params", [
    "assemblyIds=diploid%2Fadditional-0&assemblyIds=monoploid%2FE4-63",
    "assemblyIds=diploid%2Fadditional-0",
    "assemblyIds=",
])
def test_url_genomes_never_expand_to_full_release(site, browser, assembly_params):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        calls = mock_annotations(context)
        page = context.new_page()
        page.goto(site + "/functional-annotation?" + assembly_params)
        if "E4-63" in assembly_params:
            expect(page.locator("#query-status")).to_contain_text("51 matching genes")
            assert [payload for path, payload in calls if path.endswith("/query")][-1]["assemblyIds"] == ["monoploid/E4-63"]
            assert parse_qs(urlsplit(page.url).query)["assemblyIds"] == ["monoploid/E4-63"]
        else:
            expect(page.locator("#query-status")).to_have_text("Select at least one genome.")
            expect(page.locator("#dataset-status")).to_be_hidden()
            expect(page.locator("#search-button")).to_be_enabled()
            expect(page.locator("#export-all")).to_be_disabled()
            expect(page.locator("#assembly-options input:checked")).to_have_count(0)
            expect(page.locator("#results-body tr")).to_have_count(0)
            assert not any(path.endswith("/query") for path, _ in calls)
            page.get_by_role("checkbox", name="DMv8.2", exact=True).check()
            page.locator("#search-button").click()
            expect(page.locator("#query-status")).to_contain_text("51 matching genes")
            assert [payload for path, payload in calls if path.endswith("/query")][-1]["assemblyIds"] == ["monoploid/DMv8.2"]


def test_release_without_visible_genomes_does_not_query_hidden_genomes(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        calls = mock_annotations(context, assemblies=FULL_ASSEMBLIES[:149])
        page = context.new_page()
        page.goto(site + "/functional-annotation")
        expect(page.locator("#dataset-status")).to_contain_text("No annotation genomes are available")
        expect(page.locator("#search-button")).to_be_disabled()
        assert not any(path.endswith("/query") for path, _ in calls)


@pytest.mark.parametrize("width", [390, 1440])
def test_basic_query_domain_details_and_mobile_layout(site, browser, tmp_path, width):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": width, "height": 1000}) as context:
        calls = mock_annotations(context)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        legacy_filters = {"signatures": "PF03106", "domainMode": "any", "tfFamilies": "WRKY",
                          "tfStatus": "not_selected", "confidenceGrades": "A", "annotationStatus": "hit",
                          "isoformPresenceConflict": "true", "familyConflict": "true"}
        from urllib.parse import urlencode
        page.goto(site + "/functional-annotation?" + urlencode(legacy_filters))
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        expect(page.locator("#advanced-section")).to_have_count(0)
        expect(page.locator("#results-table > thead th")).to_have_text([
            "", "Gene", "Genome", "Transcripts", "Domains / families", "TF families"])
        expect(page.locator(".record-row td").nth(3)).to_have_text("2")
        expect(page.locator(".record-row td").last).to_have_text("WRKY")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["limit"] == 20 and query["offset"] == 0
        assert not (set(legacy_filters) - {"tfStatus"}).intersection(query)
        assert query["tfStatus"] == "all"
        expect(page.get_by_role("radio", name="All genes", exact=True)).to_be_checked()
        assert not set(legacy_filters).intersection(parse_qs(urlsplit(page.url).query))
        assert page.locator("#result-view").evaluate("e => e.getBoundingClientRect().width >= 100 && e.getBoundingClientRect().right <= innerWidth")
        assert query["assemblyIds"] == ["monoploid/DMv8.2"]
        page.locator(".record-link").click()
        cards = page.locator(".transcript-card")
        expect(cards).to_have_count(2)
        expect(cards.locator(".transcript-title")).to_have_text(["DM8.2_chr05G00001.1", "DM8.2_chr05G00001.2"])
        expect(page.locator(".detail-content details, .detail-content summary")).to_have_count(0)
        expect(page.locator(".domain-hit")).to_have_count(2)
        expect(cards.first.locator(".domain-chart")).to_be_visible()
        assert page.locator(".domain-hit").evaluate_all("nodes => nodes[0].getAttribute('y') !== nodes[1].getAttribute('y')")
        expect(page.locator(".domain-hit").first).to_have_attribute("aria-label", __import__("re").compile(".*2.1E-18.*GO:0003700.*"))
        expect(page.locator(".domain-hit title")).to_have_count(0)
        hit = page.locator(".domain-hit").first
        tooltip = page.locator("#domain-tooltip")
        hit.scroll_into_view_if_needed()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        # A pointer event reveals the annotation synchronously, without a hover timer.
        assert hit.evaluate("""node => {
            node.dispatchEvent(new PointerEvent('pointerover', {bubbles:true,clientX:innerWidth-10,clientY:innerHeight-10}));
            return !document.getElementById('domain-tooltip').hidden;
        }""")
        expect(tooltip).to_contain_text("PF03106")
        expect(tooltip).to_contain_text("GO:0003700")
        assert tooltip.evaluate("e => { const r = e.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; }")
        page.keyboard.press("Escape")
        expect(tooltip).to_be_hidden()
        hit.hover()
        expect(tooltip).to_be_visible()
        page.mouse.move(0, 0)
        expect(tooltip).to_be_hidden()
        hit.focus()
        expect(tooltip).to_be_visible()
        page.locator(".record-link").focus()
        expect(tooltip).to_be_hidden()
        expect(cards.first.locator(".evidence-table th")).to_have_text([
            "Database", "Signature / description", "Coordinates", "Raw score", "InterPro", "GO terms"])
        assert "R-TEST" not in page.locator(".domain-chart").inner_html()
        expect(cards.nth(1)).to_contain_text("No domain annotations to display.")
        expect(cards.first.locator(".tf-families")).to_have_text("TF families: WRKY")
        details = page.locator(".detail-content").inner_text()
        assert all(text not in details for text in ["TF evidence", "TF decisions", "Matches query", "selected", "unassessable", "No CDS", "conflict", "Pathways", "R-TEST", "Protein amino acid coordinates are 1-based"])
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot(page, f"annotations-evidence-{width}.png", tmp_path)
        page.locator(".record-link").click()
        expect(page.locator(".detail-row")).to_have_count(0)
        expect(page.locator(".record-link")).to_have_attribute("aria-expanded", "false")
        page.locator(".record-link").click()
        expect(cards.first.locator(".domain-chart")).to_be_visible()
        expect(cards.nth(1)).to_contain_text("No domain annotations to display.")
        page.locator("#search-input").fill("DM8.2_chr05G00001")
        page.locator("#search-button").click()
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["ids"] == ["DM8.2_chr05G00001"]
        assert "ids=DM8.2_chr05G00001" in page.url
        expect(page.locator("#id-report")).to_contain_text("UNKNOWN")
        page.reload()
        expect(page.locator("#search-input")).to_have_value("DM8.2_chr05G00001")
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        page.locator("#search-kind").select_option("q")
        page.locator("#search-input").fill("DNA-binding")
        page.locator("#result-view").select_option("transcripts")
        page.locator("#search-button").click()
        expect(page.locator("#query-status")).to_contain_text("51 matching transcripts")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["q"] == "DNA-binding" and query["ids"] == []
        expect(page.locator(".record-row td").last).to_have_text("WRKY")
        page.locator(".record-link").click()
        expect(page.locator(".domain-hit")).to_have_count(2)
        expect(page.locator(".detail-content .tf-families")).to_have_text("TF families: WRKY")
        page.locator("#reset-search").click()
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        expect(page.locator("#search-input")).to_have_value("")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["ids"] == [] and query["q"] == "" and query["assemblyIds"] == ["monoploid/DMv8.2"]
        assert not errors


def test_transcript_load_failure_can_retry_without_hiding_other_isoforms(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        mock_annotations(context)
        context.route("**/api/genome-annotations/transcripts/DM8.2_chr05G00001.1?*",
                      lambda route: route.fulfill(status=503, json={"detail": "Temporary annotation error"}), times=1)
        page = context.new_page()
        page.goto(site + "/functional-annotation")
        page.locator(".record-link").click()
        cards = page.locator(".transcript-card")
        expect(cards).to_have_count(2)
        expect(cards.first).to_contain_text("Temporary annotation error")
        expect(cards.nth(1)).to_contain_text("No domain annotations to display.")
        cards.first.get_by_role("button", name="Retry", exact=True).click()
        expect(cards.first.locator(".domain-chart")).to_be_visible()
        expect(cards.first.locator(".is-error")).to_have_count(0)
        expect(cards.first.get_by_role("button", name="Retry", exact=True)).to_have_count(0)
        expect(cards.nth(1)).to_contain_text("No domain annotations to display.")


def test_cross_page_selection_native_export_and_agent_example(site, browser, tmp_path):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True) as context:
        # Production's LAN / ZeroTier HTTP origins do not expose randomUUID.
        context.add_init_script("Object.defineProperty(Crypto.prototype, 'randomUUID', {value: undefined});")
        calls = mock_annotations(context)
        page = context.new_page()
        page.goto(site + "/functional-annotation?tfStatus=selected")
        expect(page.locator("#query-status")).to_contain_text("31 matching genes")
        page.locator("#select-page").check()
        page.locator("#next-page").click()
        expect(page.locator(".record-link")).to_have_text("DM8.2_chr05G00021")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["limit"] == 20 and query["offset"] == 20
        page.locator("#select-page").check()
        page.locator("#previous-page").click()
        expect(page.locator(".record-link")).to_have_text("DM8.2_chr05G00001")
        assert [payload for path, payload in calls if path.endswith("/query")][-1]["offset"] == 0
        expect(page.locator("#export-selected")).to_have_text("Export selected (2)")
        page.locator("#export-selected").click()
        expect(page.locator("#export-description")).to_contain_text("2 selected records across pages")
        assert page.locator('#export-form input[name="table"]').evaluate_all("nodes => nodes.map(n => n.value)") == ["genes", "transcripts", "domains"]
        with page.expect_download() as download:
            page.locator("#download-export").click()
        target = tmp_path / "annotations.zip"
        download.value.save_as(target)
        payload = [payload for path, payload in calls if path.endswith("/export-download")][-1]
        assert payload["query"]["tfStatus"] == "selected"
        assert payload["datasetVersion"] == "test-release"
        assert [row["geneId"] for row in payload["selection"]] == ["DM8.2_chr05G00001", "DM8.2_chr05G00021"]
        with zipfile.ZipFile(target) as archive:
            assert "metadata.json" in archive.namelist()
        page.locator("#close-export").click()
        page.locator("#export-all").click()
        expect(page.locator("#export-description")).to_contain_text("All 31 matching genes")
        with page.expect_download():
            page.locator("#download-export").click()
        payload = [payload for path, payload in calls if path.endswith("/export-download")][-1]
        assert payload["selection"] == [] and payload["query"]["tfStatus"] == "selected"
        page.locator("#close-export").click()
        page.route("**/chat", lambda route: route.fulfill(body="<html><body>Agent handoff</body></html>", content_type="text/html"))
        page.locator("#ask-potato-agent").click()
        page.wait_for_url("**/chat#example=*")
        from urllib.parse import unquote
        intent = json.loads(unquote(page.url.split("#example=", 1)[1]))
        assert intent["page"] == "functional_annotation"
        assert intent["text"] == "Count the genes annotated as ERF transcription factors in the C88 genome."


@pytest.mark.parametrize("legacy_tab", ["tf", "downloads"])
def test_removed_tabs_and_tf_batch_reimport(site, browser, legacy_tab):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        calls = mock_annotations(context)
        page = context.new_page()
        page.goto(site + f"/functional-annotation?tab={legacy_tab}&tfStatus=selected")
        expect(page.locator("#query-status")).to_contain_text("31 matching genes")
        expect(page.get_by_role("tab")).to_have_count(0)
        expect(page.locator("#panel-tf, #panel-downloads")).to_have_count(0)
        assert "tab=" not in page.url
        assert not any(path.endswith(("/tf-families", "/downloads")) for path, _ in calls)
        page.locator("#batch-section summary").click()
        page.locator("#batch-file").set_input_files({"name": "genes.txt", "mimeType": "text/plain", "buffer": b"G1\nG2\nG1\n"})
        expect(page.locator("#batch-ids")).to_have_value("G1\nG2")
        page.locator("#search-button").click()
        expect(page.locator("#query-status")).to_contain_text("Reimport batch IDs")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["ids"] == ["G1", "G2"] and query["tfStatus"] == "selected"
        assert "batch=reimport" in page.url and "G1" not in page.url
        page.reload()
        expect(page.locator("#query-status")).to_contain_text("Reimport the batch")
        expect(page.locator("#export-all")).to_be_disabled()
        expect(page.get_by_role("radio", name="Transcription factors", exact=True)).to_be_checked()


@pytest.mark.parametrize("width", [390, 1440])
def test_tf_switch_genome_search_reload_and_reset(site, browser, tmp_path, width):
    from playwright.sync_api import expect

    with browser.new_context(viewport={"width": width, "height": 1000}) as context:
        calls = mock_annotations(context)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(site + "/functional-annotation")
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        page.locator("#select-page").check()
        page.locator("#next-page").click()
        expect(page.locator(".record-link")).to_have_text("DM8.2_chr05G00021")
        page.get_by_role("radio", name="Transcription factors", exact=True).check()
        expect(page.locator("#query-status")).to_contain_text("31 matching genes")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["tfStatus"] == "selected" and query["offset"] == 0 and query["limit"] == 20
        expect(page.locator("#export-selected")).to_have_text("Export selected (0)")
        expect(page.locator("#export-selected")).to_be_disabled()
        assert "tfStatus=selected" in page.url
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot(page, f"annotations-tf-filter-{width}.png", tmp_path)
        page.get_by_role("checkbox", name="DMv8.2", exact=True).uncheck()
        page.get_by_role("checkbox", name="E4-63", exact=True).check()
        page.locator("#search-input").fill("E4_GENE")
        page.locator("#search-button").click()
        expect(page.locator(".record-row td").nth(2)).to_have_text("E4-63")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["assemblyIds"] == ["monoploid/E4-63"]
        assert query["tfStatus"] == "selected" and query["ids"] == ["E4_GENE"]
        page.reload()
        expect(page.locator("#query-status")).to_contain_text("31 matching genes")
        expect(page.get_by_role("radio", name="Transcription factors", exact=True)).to_be_checked()
        expect(page.get_by_role("checkbox", name="E4-63", exact=True)).to_be_checked()
        expect(page.locator("#search-input")).to_have_value("E4_GENE")
        page.locator("#search-kind").select_option("q")
        page.locator("#search-input").fill("DNA-binding")
        page.locator("#result-view").select_option("transcripts")
        page.locator("#search-button").click()
        expect(page.locator("#query-status")).to_contain_text("31 matching transcripts")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["q"] == "DNA-binding" and query["ids"] == [] and query["tfStatus"] == "selected"
        page.get_by_role("radio", name="All genes", exact=True).check()
        expect(page.locator("#query-status")).to_contain_text("51 matching transcripts")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["tfStatus"] == "all" and query["q"] == "DNA-binding"
        assert "tfStatus=" not in page.url
        page.get_by_role("radio", name="Transcription factors", exact=True).check()
        expect(page.locator("#query-status")).to_contain_text("31 matching transcripts")
        page.locator("#reset-search").click()
        expect(page.locator("#query-status")).to_contain_text("51 matching genes")
        query = [payload for path, payload in calls if path.endswith("/query")][-1]
        assert query["tfStatus"] == "all" and query["ids"] == [] and query["q"] == ""
        assert query["assemblyIds"] == ["monoploid/DMv8.2"]
        expect(page.get_by_role("radio", name="All genes", exact=True)).to_be_checked()
        assert not errors


def test_missing_release_and_export_version_error(site, browser):
    from playwright.sync_api import expect

    with browser.new_context() as context:
        mock_annotations(context, unavailable=True)
        page = context.new_page()
        page.goto(site + "/functional-annotation")
        expect(page.locator("#dataset-status")).to_contain_text("Annotation data is unavailable")
        expect(page.locator("#search-button")).to_be_disabled()
        expect(page.locator("#export-all")).to_be_disabled()
    with browser.new_context() as context:
        mock_annotations(context, export_error=True)
        page = context.new_page()
        page.goto(site + "/functional-annotation")
        page.locator("#export-all").click()
        page.locator("#download-export").click()
        expect(page.locator("#export-status")).to_contain_text("Annotation release changed")

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface import app as interface_app_mod


def _write_genome_browser_fixture(root: Path) -> None:
    assembly_dir = root / "monoploid" / "Test" / "reference"
    annotation_dir = root / "monoploid" / "Test" / "annotation"
    assembly_dir.mkdir(parents=True)
    annotation_dir.mkdir(parents=True)
    (assembly_dir / "Test.fa.bgz").write_bytes(b"abcdefghijklmnopqrstuvwxyz")
    (assembly_dir / "Test.fa.bgz.fai").write_text("chr1\t1000\t0\t80\t81\n", encoding="utf-8")
    (assembly_dir / "Test.fa.bgz.gzi").write_bytes(b"gzi")
    (assembly_dir / "Test.chrom.sizes").write_text("chr1\t1000\n", encoding="utf-8")
    (annotation_dir / "Test.gff3.bgz").write_bytes(b"gff")
    (annotation_dir / "Test.gff3.bgz.tbi").write_bytes(b"tbi")
    (root / "assemblies.json").write_text(
        json.dumps(
            {
                "name": "Genome_browser_DB",
                "version": "1",
                "counts": {
                    "assemblies": 1,
                    "monoploidAssemblies": 1,
                    "phasedDiploidAssemblies": 0,
                    "phasedTetraploidAssemblies": 0,
                    "sourceFastaFiles": 1,
                },
                "assemblies": [
                    {
                        "id": "monoploid/Test",
                        "sample": "Test",
                        "displayName": "Test",
                        "category": "monoploid",
                        "ploidy": "monoploid",
                        "directory": "monoploid/Test",
                        "referenceMode": "bgzip_fasta",
                        "reference": "monoploid/Test/reference/Test.fa.bgz",
                        "fai": "monoploid/Test/reference/Test.fa.bgz.fai",
                        "gzi": "monoploid/Test/reference/Test.fa.bgz.gzi",
                        "chromSizes": "monoploid/Test/reference/Test.chrom.sizes",
                        "annotation": "monoploid/Test/annotation/Test.gff3.bgz",
                        "annotationIndex": "monoploid/Test/annotation/Test.gff3.bgz.tbi",
                        "featureCount": 2,
                        "haplotypes": ["H1"],
                        "note": "internal note",
                        "sourceReferences": ["/source/Test.fa"],
                        "sourceAnnotations": ["/source/Test.gff3"],
                        "refNameCount": 1,
                        "totalBp": 1000,
                        "geneCount": 10,
                        "transcriptCount": 12,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_genome_browser_entry_and_static_paths_are_prefixed() -> None:
    lite_index = (REPO_ROOT / "interface/static/lite/index.html").read_text(encoding="utf-8")
    assert '<a class="portal-nav-item" href="/genome-browser">Genome Browser</a>' in lite_index

    genome_index = (REPO_ROOT / "interface/static/genome_browser/index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/static/genome_browser/styles.css' in genome_index
    assert 'src="/static/genome_browser/vendor/react.production.min.js"' in genome_index
    assert 'src="/static/genome_browser/vendor/react-dom.production.min.js"' in genome_index
    assert 'src="/static/genome_browser/vendor/react-linear-genome-view.umd.production.min.js"' in genome_index
    assert 'src="/static/genome_browser/app.js' in genome_index
    assert 'href="/genome-browser" aria-current="page"' in genome_index

    genome_css = (REPO_ROOT / "interface/static/genome_browser/styles.css").read_text(
        encoding="utf-8"
    )
    assert "background: url('../background.png')" in genome_css


def test_genome_browser_page_route_serves_static_page() -> None:
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/genome-browser")
        assert response.status_code == 200
        assert "Genome Browser" in response.text
        head_response = client.head("/genome-browser")
        assert head_response.status_code == 200
    finally:
        client.close()


def test_genome_browser_api_does_not_refresh_runtime_activity() -> None:
    class Request:
        class Url:
            path = "/api/genome-browser/assemblies"

        url = Url()

    assert interface_app_mod._should_refresh_activity_for_request(Request()) is False


def test_genome_browser_manifest_adds_default_location(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/api/genome-browser/assemblies")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["counts"]["assemblies"] == 1
        assert payload["assemblies"][0]["id"] == "monoploid/Test"
        assert payload["assemblies"][0]["defaultLocation"] == "chr1:1..1000"
        assert payload["assemblies"][0]["geneCount"] == 10
        assert payload["assemblies"][0]["transcriptCount"] == 12
        assert "note" not in payload["assemblies"][0]
        assert "featureCount" not in payload["assemblies"][0]
    finally:
        client.close()


def test_genome_browser_data_route_supports_range_requests(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/genome-browser/data/monoploid/Test/reference/Test.fa.bgz",
            headers={"Range": "bytes=1-3"},
        )
        assert response.status_code == 206, response.text
        assert response.content == b"bcd"
        assert response.headers["content-range"] == "bytes 1-3/26"
        head_response = client.head("/api/genome-browser/data/monoploid/Test/reference/Test.fa.bgz")
        assert head_response.status_code == 200
        assert head_response.headers["accept-ranges"] == "bytes"
    finally:
        client.close()


def test_genome_browser_data_route_rejects_path_traversal(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/api/genome-browser/data/%2e%2e/assemblies.json")
        assert response.status_code == 404
    finally:
        client.close()

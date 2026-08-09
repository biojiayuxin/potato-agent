from __future__ import annotations

import asyncio
import gzip
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface import app as interface_app_mod
from interface import genome_browser as genome_browser_mod
from interface.genome_feature_index import build_full_index, create_schema


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
                        "doi": "10.1234/test.1",
                        "featureCount": 2,
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


def _write_feature_index_fixture(root: Path, index_path: Path | None = None) -> None:
    index_path = index_path or root / "feature_index.sqlite"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(index_path) as conn:
        conn.execute("pragma foreign_keys=on")
        create_schema(conn)
        conn.execute(
            """
            insert into assemblies(
              assembly_pk,assembly_id,reference_path,reference_bytes,
              reference_mtime_ns,fai_path,fai_sha256,annotation_path,
              annotation_bytes,annotation_mtime_ns,annotation_sha256,
              representative_map_sha256,gene_count,transcript_count,exon_count,
              cds_count,synthetic_gene_count,indexed_at
            ) values(
              1,'monoploid/Test','monoploid/Test/reference/Test.fa.bgz',26,1,
              'monoploid/Test/reference/Test.fa.bgz.fai','fai-sha',
              'monoploid/Test/annotation/Test.gff3.bgz',3,1,'sha','',1,2,3,3,0,'now'
            )
            """
        )
        conn.execute("insert into seqids values(1,'chr1',1000)")
        conn.execute(
            "insert into genes values(1,1,'gene1','gene','chr1',100,500,'+',null)"
        )
        conn.executemany(
            "insert into transcripts values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    1,
                    1,
                    "tx1",
                    "mRNA",
                    "chr1",
                    100,
                    300,
                    "+",
                    "[[100,150],[200,300]]",
                    "[[110,140,0],[200,260,2]]",
                    152,
                    92,
                    110,
                    0,
                    0,
                    "not_selected",
                ),
                (
                    2,
                    1,
                    1,
                    "tx2",
                    "mRNA",
                    "chr1",
                    100,
                    500,
                    "+",
                    "[[100,500]]",
                    "[[120,450,0]]",
                    401,
                    331,
                    120,
                    0,
                    1,
                    "longest_cds",
                ),
            ),
        )
        conn.execute("update genes set representative_transcript_pk=2 where gene_pk=1")
        conn.execute("insert into gene_aliases values(1,'GeneAlias',1)")
        conn.execute("insert into transcript_aliases values(1,'TxAlias',2)")
        conn.commit()


def test_genome_browser_entry_and_static_paths_are_prefixed() -> None:
    lite_index = (REPO_ROOT / "interface/static/lite/index.html").read_text(encoding="utf-8")
    assert '<a class="portal-nav-item" href="/genomes" data-mobile-supported="true">Genomes</a>' in lite_index

    genomes_index = (REPO_ROOT / "interface/static/genomes/index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/static/genomes/styles.css' in genomes_index
    assert 'src="/static/genomes/app.js' in genomes_index
    assert 'href="/genomes" aria-current="page"' in genomes_index
    assert 'class="browser-button" href="/genomes/browser"' in genomes_index
    assert 'src="/static/genomes/assets/pan_core_accumulation_compact.svg"' in genomes_index
    assert 'src="/static/genomes/assets/pangenome_gene_family_distribution_ybreak.svg"' in genomes_index
    assert genomes_index.count("Assembly Ploidy") == 2
    assert "All assembly ploidies" in genomes_index
    assert "All ploidy levels" not in genomes_index

    genome_index = (REPO_ROOT / "interface/static/genome_browser/index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="/static/genome_browser/styles.css' in genome_index
    assert 'src="/static/genome_browser/vendor/react.production.min.js"' in genome_index
    assert 'src="/static/genome_browser/vendor/react-dom.production.min.js"' in genome_index
    assert 'src="/static/genome_browser/vendor/react-linear-genome-view.umd.production.min.js"' in genome_index
    assert 'src="/static/genome_browser/app.js' in genome_index
    assert 'href="/genomes" aria-current="page"' in genome_index
    assert 'class="parent-page-link" href="/genomes"' in genome_index

    genome_css = (REPO_ROOT / "interface/static/genome_browser/styles.css").read_text(
        encoding="utf-8"
    )
    assert "background: url('../background.png')" in genome_css

    genomes_css = (REPO_ROOT / "interface/static/genomes/styles.css").read_text(
        encoding="utf-8"
    )
    assert ".genomes-main {\n  width: 100%;\n  max-width: 1120px;" in genomes_css

    genomes_app = (REPO_ROOT / "interface/static/genomes/app.js").read_text(
        encoding="utf-8"
    )
    assert "assembly.sample" in genomes_app
    assert "new URLSearchParams({ assembly: assemblyId })" in genomes_app
    assert "`/genomes/browser?${params.toString()}`" in genomes_app

    genes_index = (REPO_ROOT / "interface/static/genes/index.html").read_text(
        encoding="utf-8"
    )
    genes_app = (REPO_ROOT / "interface/static/genes/app.js").read_text(
        encoding="utf-8"
    )
    assert 'href="/genomes/browser"' in genes_index
    assert "`/genomes/browser?${params.toString()}`" in genes_app


def test_portal_navigation_uses_genomes_as_the_primary_page() -> None:
    portal_indexes = [
        "interface/static/lite/high-resolution-required.html",
        "interface/static/genes/index.html",
        "interface/static/spatial/index.html",
        "interface/static/wgcna/index.html",
        "interface/static/bulk_rnaseq/index.html",
    ]
    for relative_path in portal_indexes:
        content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert 'href="/genomes">Genomes</a>' in content
        assert '<a class="portal-nav-item" href="/genome-browser">' not in content


def test_genome_browser_viewer_height_follows_jbrowse_content() -> None:
    genome_css = (REPO_ROOT / "interface/static/genome_browser/styles.css").read_text(
        encoding="utf-8"
    )

    assert ".query-panel,\n.viewer-panel" not in genome_css
    assert "height: clamp(380px, 48vh, 460px);" in genome_css
    assert "#jbrowse-linear-genome-view {\n  width: 100%;\n}" in genome_css
    assert ".browser-stage {\n  position: relative;\n  min-width: 0;\n}" in genome_css


def test_genome_browser_assembly_detail_omits_haplotypes() -> None:
    genome_app = (REPO_ROOT / "interface/static/genome_browser/app.js").read_text(
        encoding="utf-8"
    )

    assert "Haplotypes" not in genome_app
    assert "assembly.haplotypes" not in genome_app


def test_genome_browser_page_uses_canonical_genomes_route() -> None:
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/genomes/browser")
        assert response.status_code == 200
        assert "Genome Browser" in response.text
        assert "Genomes - Genome Browser" in response.text
        head_response = client.head("/genomes/browser")
        assert head_response.status_code == 200
    finally:
        client.close()


def test_legacy_genome_browser_route_redirects_with_query() -> None:
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/genome-browser?assembly=monoploid%2FTest&loc=chr1%3A1..10",
            follow_redirects=False,
        )
        assert response.status_code == 308
        assert response.headers["location"] == (
            "/genomes/browser?assembly=monoploid%2FTest&loc=chr1%3A1..10"
        )

        head_response = client.head("/genome-browser", follow_redirects=False)
        assert head_response.status_code == 308
        assert head_response.headers["location"] == "/genomes/browser"
    finally:
        client.close()


def test_genomes_page_route_serves_static_page() -> None:
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/genomes")
        assert response.status_code == 200
        assert "Genome accessions" in response.text
        head_response = client.head("/genomes")
        assert head_response.status_code == 200

        stylesheet = client.get("/static/genomes/styles.css")
        assert stylesheet.status_code == 200
        assert ".genomes-main" in stylesheet.text

        script = client.get("/static/genomes/app.js")
        assert script.status_code == 200
        assert "assembly.sample" in script.text

        figure = client.get(
            "/static/genomes/assets/pan_core_accumulation_compact.svg"
        )
        assert figure.status_code == 200
        assert figure.headers["content-type"].startswith("image/svg+xml")
    finally:
        client.close()


def test_genome_browser_api_does_not_refresh_runtime_activity() -> None:
    for path in (
        "/api/genome-browser/assemblies",
        "/api/genome-browser/features/resolve",
        "/api/genome-browser/sequences",
    ):
        class Request:
            scope = {"path": path}

        assert interface_app_mod._should_refresh_activity_for_request(Request()) is False


def test_genome_browser_manifest_adds_default_location(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    manifest_path = tmp_path / "assemblies.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assemblies"][0]["representativeMap"] = (
        "metadata/representative-maps/Test.tsv"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/api/genome-browser/assemblies")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["counts"]["assemblies"] == 1
        assert payload["assemblies"][0]["id"] == "monoploid/Test"
        assert payload["assemblies"][0]["sample"] == "Test"
        assert payload["assemblies"][0]["ploidy"] == "monoploid"
        assert payload["assemblies"][0]["doi"] == "10.1234/test.1"
        assert payload["assemblies"][0]["defaultLocation"] == "chr1:1..1000"
        assert payload["assemblies"][0]["geneCount"] == 10
        assert payload["assemblies"][0]["transcriptCount"] == 12
        assert "haplotypes" not in payload["assemblies"][0]
        assert "note" not in payload["assemblies"][0]
        assert "featureCount" not in payload["assemblies"][0]
        assert "representativeMap" not in payload["assemblies"][0]
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


def test_genome_browser_data_route_only_serves_manifest_files(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    (tmp_path / "feature_index.sqlite").write_bytes(b"private-derived-index")
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/api/genome-browser/data/feature_index.sqlite")
        assert response.status_code == 404
        manifest = client.get("/api/genome-browser/data/assemblies.json")
        assert manifest.status_code == 404
    finally:
        client.close()


def test_genome_browser_data_route_rejects_manifest_symlink_escape(
    monkeypatch, tmp_path
) -> None:
    _write_genome_browser_fixture(tmp_path)
    reference = tmp_path / "monoploid/Test/reference/Test.fa.bgz"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-reference"
    outside.write_bytes(b"outside")
    reference.unlink()
    reference.symlink_to(outside)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/genome-browser/data/monoploid/Test/reference/Test.fa.bgz"
        )
        assert response.status_code == 404
    finally:
        client.close()
        outside.unlink(missing_ok=True)


def test_feature_resolve_api_returns_representative_and_segments(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    _write_feature_index_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    monkeypatch.delenv("GENOME_BROWSER_FEATURE_INDEX_PATH", raising=False)
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/genome-browser/features/resolve",
            params={"assembly": "monoploid/Test", "id": "GeneAlias"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["query"]["matchedBy"] == "alias"
        assert payload["gene"]["representativeTranscriptId"] == "tx2"
        assert [item["id"] for item in payload["transcripts"]] == ["tx2", "tx1"]
        assert payload["transcripts"][0]["representativeSource"] == "longest_cds"
        assert payload["transcripts"][0]["cds"][0]["phase"] == 0
    finally:
        client.close()


def test_feature_resolve_api_rejects_unknown_assembly_before_index(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    _write_feature_index_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/genome-browser/features/resolve",
            params={"assembly": "monoploid/Missing", "id": "gene1"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "assembly not found"
    finally:
        client.close()


def test_feature_resolve_api_honors_readonly_index_path_override(
    monkeypatch, tmp_path
) -> None:
    _write_genome_browser_fixture(tmp_path)
    index_path = tmp_path / "derived/index.sqlite"
    _write_feature_index_fixture(tmp_path, index_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    monkeypatch.setenv("GENOME_BROWSER_FEATURE_INDEX_PATH", str(index_path))
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/genome-browser/features/resolve",
            params={"assembly": "monoploid/Test", "id": "gene1"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["gene"]["id"] == "gene1"

        missing_path = tmp_path / "derived/missing.sqlite"
        monkeypatch.setenv("GENOME_BROWSER_FEATURE_INDEX_PATH", str(missing_path))
        unavailable = client.get(
            "/api/genome-browser/features/resolve",
            params={"assembly": "monoploid/Test", "id": "gene1"},
        )
        assert unavailable.status_code == 503
        assert not missing_path.exists()
    finally:
        client.close()


def test_feature_resolve_api_preserves_negative_strand_cds_order_and_phase(
    monkeypatch, tmp_path
) -> None:
    _write_genome_browser_fixture(tmp_path)
    annotation = tmp_path / "monoploid/Test/annotation/Test.gff3.bgz"
    with gzip.open(annotation, "wt", encoding="utf-8") as handle:
        handle.write("##gff-version 3\n")
        handle.write("chr1\tsrc\tgene\t100\t900\t.\t-\t.\tID=geneN\n")
        handle.write("chr1\tsrc\tmRNA\t100\t900\t.\t-\t.\tID=txN;Parent=geneN\n")
        handle.write("chr1\tsrc\texon\t100\t200\t.\t-\t.\tParent=txN\n")
        handle.write("chr1\tsrc\texon\t700\t900\t.\t-\t.\tParent=txN\n")
        handle.write("chr1\tsrc\tCDS\t120\t180\t.\t-\t1\tParent=txN\n")
        handle.write("chr1\tsrc\tCDS\t720\t880\t.\t-\t0\tParent=txN\n")
    manifest_path = tmp_path / "assemblies.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assemblies"][0]["geneCount"] = 1
    manifest["assemblies"][0]["transcriptCount"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    build_full_index(db_root=tmp_path, output=tmp_path / "feature_index.sqlite")
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    monkeypatch.delenv("GENOME_BROWSER_FEATURE_INDEX_PATH", raising=False)
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/genome-browser/features/resolve",
            params={"assembly": "monoploid/Test", "id": "geneN"},
        )
        assert response.status_code == 200, response.text
        transcript = response.json()["transcripts"][0]
        assert transcript["strand"] == "-"
        assert transcript["codingStart"] == 880
        assert transcript["cdsFivePrimePosition"] == 880
        assert transcript["firstCdsPhase"] == 0
        assert [
            (segment["start"], segment["end"], segment["phase"])
            for segment in transcript["cds"]
        ] == [(720, 880, 0), (120, 180, 1)]
    finally:
        client.close()


def test_sequence_api_batches_once_preserves_order_and_reverse_complements(
    monkeypatch, tmp_path
) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    calls: list[list[tuple[str, int, int]]] = []

    def fake_run_faidx(_reference: Path, intervals: list[tuple[str, int, int]]) -> list[str]:
        calls.append(intervals)
        return ["ACGTRY" for _interval in intervals]

    monkeypatch.setattr(genome_browser_mod, "run_faidx", fake_run_faidx)
    client = TestClient(interface_app_mod.app)
    try:
        response = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {"name": "plus", "refName": "chr1", "start": 1, "end": 6, "strand": "+"},
                    {"name": "minus", "refName": "chr1", "start": 1, "end": 6, "strand": "-"},
                ],
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert calls == [[("chr1", 1, 6)]]
        assert [record["name"] for record in payload["records"]] == ["plus", "minus"]
        assert payload["records"][0]["sequence"] == "ACGTRY"
        assert payload["records"][1]["sequence"] == "RYACGT"
        assert payload["requestedTotalLength"] == 12
        assert payload["totalLength"] == 12
    finally:
        client.close()


def test_sequence_api_clips_and_reports_requested_coordinates(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        genome_browser_mod,
        "run_faidx",
        lambda _reference, intervals: ["A" * (end - start + 1) for _ref, start, end in intervals],
    )
    client = TestClient(interface_app_mod.app)
    try:
        response = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "clip": True,
                "segments": [
                    {"name": "left", "refName": "chr1", "start": -9, "end": 10},
                    {"name": "right", "refName": "chr1", "start": 995, "end": 1005},
                ],
            },
        )
        assert response.status_code == 200, response.text
        records = response.json()["records"]
        assert records[0]["requestedStart"] == -9
        assert records[0]["start"] == 1
        assert records[0]["length"] == 10
        assert records[0]["clipped"] is True
        assert records[1]["end"] == 1000
        assert records[1]["length"] == 6
    finally:
        client.close()


def test_sequence_api_enforces_one_megabase_limits(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    manifest = json.loads((tmp_path / "assemblies.json").read_text(encoding="utf-8"))
    fai_path = tmp_path / manifest["assemblies"][0]["fai"]
    fai_path.write_text("chr1\t2000000\t0\t80\t81\n", encoding="utf-8")
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    monkeypatch.setattr(
        genome_browser_mod,
        "run_faidx",
        lambda _reference, intervals: ["A" * (end - start + 1) for _ref, start, end in intervals],
    )
    client = TestClient(interface_app_mod.app)
    try:
        exact_limit = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {"name": "x", "refName": "chr1", "start": 1, "end": 1000000}
                ],
            },
        )
        assert exact_limit.status_code == 200, exact_limit.text
        assert exact_limit.json()["totalLength"] == 1000000

        too_long = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [{"name": "x", "refName": "chr1", "start": 1, "end": 1000001}],
            },
        )
        assert too_long.status_code == 413

        aggregate = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {"name": "x", "refName": "chr1", "start": 1, "end": 600000},
                    {"name": "y", "refName": "chr1", "start": 700000, "end": 1100000},
                ],
            },
        )
        assert aggregate.status_code == 413
    finally:
        client.close()


def test_sequence_api_rejects_unknown_fields_and_duplicate_names(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        extra = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "unexpected": True,
                "segments": [{"name": "x", "refName": "chr1", "start": 1, "end": 2}],
            },
        )
        assert extra.status_code == 422

        duplicate = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {"name": "x", "refName": "chr1", "start": 1, "end": 2},
                    {"name": "x", "refName": "chr1", "start": 3, "end": 4},
                ],
            },
        )
        assert duplicate.status_code == 422

        boolean_coordinate = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {"name": "x", "refName": "chr1", "start": True, "end": 2}
                ],
            },
        )
        assert boolean_coordinate.status_code == 422

        too_many_segments = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {
                        "name": f"segment-{index}",
                        "refName": "chr1",
                        "start": 1,
                        "end": 1,
                    }
                    for index in range(genome_browser_mod.MAX_SEQUENCE_SEGMENTS + 1)
                ],
            },
        )
        assert too_many_segments.status_code == 422
    finally:
        client.close()


def test_sequence_api_reports_reference_and_interval_errors(monkeypatch, tmp_path) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    client = TestClient(interface_app_mod.app)
    try:
        unknown_reference = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {"name": "x", "refName": "missing", "start": 1, "end": 1}
                ],
            },
        )
        assert unknown_reference.status_code == 404

        outside = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "segments": [
                    {"name": "x", "refName": "chr1", "start": 999, "end": 1001}
                ],
            },
        )
        assert outside.status_code == 416

        no_overlap = client.post(
            "/api/genome-browser/sequences",
            json={
                "assembly": "monoploid/Test",
                "clip": True,
                "segments": [
                    {"name": "x", "refName": "chr1", "start": 1001, "end": 1010}
                ],
            },
        )
        assert no_overlap.status_code == 416
    finally:
        client.close()


def test_sequence_api_limits_concurrency_and_releases_after_failures(
    monkeypatch, tmp_path
) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    request = {
        "assembly": "monoploid/Test",
        "segments": [{"name": "x", "refName": "chr1", "start": 1, "end": 2}],
    }
    client = TestClient(interface_app_mod.app)
    try:
        acquired_count = 0
        try:
            for _index in range(genome_browser_mod.MAX_CONCURRENT_SEQUENCE_JOBS):
                if genome_browser_mod._sequence_limiter.acquire(blocking=False):
                    acquired_count += 1
            assert acquired_count == genome_browser_mod.MAX_CONCURRENT_SEQUENCE_JOBS
            busy = client.post("/api/genome-browser/sequences", json=request)
            assert busy.status_code == 429
            assert busy.headers["retry-after"] == "1"
        finally:
            for _index in range(acquired_count):
                genome_browser_mod._sequence_limiter.release()

        def fail_faidx(
            _reference: Path, _intervals: list[tuple[str, int, int]]
        ) -> list[str]:
            raise genome_browser_mod.SequenceExtractionError("test failure")

        monkeypatch.setattr(genome_browser_mod, "run_faidx", fail_faidx)
        failed = client.post("/api/genome-browser/sequences", json=request)
        assert failed.status_code == 503

        monkeypatch.setattr(
            genome_browser_mod,
            "run_faidx",
            lambda _reference, _intervals: ["AA"],
        )
        recovered = client.post("/api/genome-browser/sequences", json=request)
        assert recovered.status_code == 200, recovered.text
    finally:
        client.close()


@pytest.mark.asyncio
async def test_cancelled_sequence_request_keeps_slot_until_worker_finishes(
    monkeypatch, tmp_path
) -> None:
    _write_genome_browser_fixture(tmp_path)
    monkeypatch.setenv("GENOME_BROWSER_DB_ROOT", str(tmp_path))
    entered = threading.Event()
    release_worker = threading.Event()

    def block_extraction(
        _root: Path,
        _assembly: dict[str, object],
        request: genome_browser_mod.SequenceBatchRequest,
    ) -> dict[str, object]:
        entered.set()
        assert release_worker.wait(timeout=5)
        return {"assembly": request.assembly, "records": []}

    monkeypatch.setattr(genome_browser_mod, "extract_sequence_batch", block_extraction)
    request = genome_browser_mod.SequenceBatchRequest.model_validate(
        {
            "assembly": "monoploid/Test",
            "segments": [
                {"name": "x", "refName": "chr1", "start": 1, "end": 2}
            ],
        }
    )
    task = asyncio.create_task(genome_browser_mod.api_genome_browser_sequences(request))
    manual_slots = 0
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        for _index in range(genome_browser_mod.MAX_CONCURRENT_SEQUENCE_JOBS):
            if genome_browser_mod._sequence_limiter.acquire(blocking=False):
                manual_slots += 1
        assert manual_slots == genome_browser_mod.MAX_CONCURRENT_SEQUENCE_JOBS - 1
    finally:
        for _index in range(manual_slots):
            genome_browser_mod._sequence_limiter.release()
        release_worker.set()

    for _index in range(100):
        if genome_browser_mod._sequence_limiter.acquire(blocking=False):
            genome_browser_mod._sequence_limiter.release()
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("cancelled worker did not release its sequence slot")

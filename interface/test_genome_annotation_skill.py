from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/potato-knowledge-bioinformatics/genome-annotation-query/scripts/query_genome_annotations.py"
spec = importlib.util.spec_from_file_location("query_genome_annotations", SCRIPT)
assert spec is not None and spec.loader is not None
skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skill)


def export_zip() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps({"datasetVersion": "test-release", "counts": {"genes": 2}}))
        archive.writestr("genes.tsv", "assemblyId\tgeneId\nmonoploid/DMv8.2\tGene.1\n")
    return stream.getvalue()


@pytest.fixture
def api_server():
    requests = []
    state = {"zip": export_zip(), "type": "application/zip", "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self):
            payload = None
            if self.command == "POST":
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.command, self.path, payload))
            if self.path.endswith("/export"):
                status = state["status"]
                content_type = state["type"]
                body = state["zip"]
            else:
                status = 200
                content_type = "application/json"
                body = json.dumps({"datasetVersion": "test-release", "total": 17, "returned": 1,
                                   "items": [{"geneId": "Gene.1"}], "idReport": {"unmatchedIds": ["Absent"]}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_query_preserves_exact_ids_and_reports_and_sends_combined_filters(api_server, tmp_path, capsys):
    site, requests, _ = api_server
    ids = tmp_path / "ids.txt"
    ids.write_text("Gene.1\nTranscript.1; UP0000123,Absent\nGene.1", encoding="utf-8")
    assert skill.main(["query", "--ids-file", str(ids), "--all-assemblies", "--view", "transcripts",
                       "--signature", "PF03106", "--signature", "PF00001", "--domain-match", "all",
                       "--tf-family", "WRKY", "--tf-grade", "C", "--conflict", "any",
                       "--limit", "1", "--offset", "2", "--base-url", site]) == 0
    method, path, body = requests[0]
    assert (method, path) == ("POST", "/api/genome-annotations/query")
    assert body["assemblyIds"] == []
    assert body["ids"] == ["Gene.1", "Transcript.1", "UP0000123", "Absent"]
    assert body["signatures"] == ["PF03106", "PF00001"]
    assert body["tfFamilies"] == ["WRKY"] and body["grades"] == ["C"]
    assert body["domainMode"] == "all" and body["conflict"] == "any"
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["total"] == 17
    assert result["data"]["idReport"]["unmatchedIds"] == ["Absent"]


@pytest.mark.parametrize("command, endpoint", [("gene", "genes"), ("transcript", "transcripts")])
def test_detail_url_encodes_exact_identifier_and_assembly(api_server, command, endpoint):
    site, requests, _ = api_server
    args = skill.build_parser().parse_args([command, "Gene.1+special?", "--assembly", "phased_tetraploid/Des", "--base-url", site])
    result = skill.run(args)
    parsed = urllib.parse.urlsplit(result["api_url"])
    assert parsed.path.endswith(f"/{endpoint}/Gene.1%2Bspecial%3F")
    assert urllib.parse.parse_qs(parsed.query) == {"assembly": ["phased_tetraploid/Des"]}
    assert requests[0][0] == "GET"


def test_export_streams_all_matches_and_pins_version_without_transmitting_local_paths(api_server, tmp_path):
    site, requests, state = api_server
    selection = tmp_path / "selected.json"
    selection.write_text(json.dumps([{"assemblyId": "monoploid/DMv8.2", "geneId": "Gene.1"}]))
    query = tmp_path / "query.json"
    query.write_text(json.dumps({"assemblyIds": ["monoploid/DMv8.2"], "q": "WRKY", "limit": 1, "offset": 50}))
    output = tmp_path / "nested" / "WRKY annotations.zip"
    args = skill.build_parser().parse_args(["export", "--query-json", str(query), "--selected-json", str(selection),
                                           "--table", "genes", "--table", "domains", "--format", "csv",
                                           "--output", str(output), "--base-url", site])
    result = skill.run(args)
    assert requests[0][:2] == ("GET", "/api/genome-annotations/metadata")
    body = requests[1][2]
    assert body["datasetVersion"] == "test-release"
    assert body["query"]["limit"] == 1 and body["query"]["offset"] == 50
    assert body["tables"] == ["genes", "domains"] and body["format"] == "csv"
    assert body["selection"] == [{"assemblyId": "monoploid/DMv8.2", "geneId": "Gene.1"}]
    assert str(tmp_path) not in json.dumps(body)
    assert output.read_bytes() == state["zip"]
    assert result["output"] == str(output.resolve())
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["metadata"]["counts"]["genes"] == 2
    assert not list(output.parent.glob(".annotation-*"))


def test_export_uses_query_version_and_cli_filter_override(api_server, tmp_path):
    site, requests, _ = api_server
    query = tmp_path / "query.json"
    query.write_text(json.dumps({"datasetVersion": "test-release", "assemblyIds": [], "q": "old"}))
    args = skill.build_parser().parse_args(["export", "--query-json", str(query), "--text", "new",
                                           "--output", str(tmp_path / "out.zip"), "--base-url", site])
    skill.run(args)
    assert len(requests) == 1
    assert requests[0][2]["datasetVersion"] == "test-release"
    assert requests[0][2]["query"]["assemblyIds"] == []
    assert requests[0][2]["query"]["q"] == "new"


@pytest.mark.parametrize("content_type, body", [("text/html", b"<html>Login</html>"),
                                                ("application/zip", b"PK\x03\x04truncated")])
def test_invalid_download_cleans_temporary_files(api_server, tmp_path, content_type, body):
    site, _, state = api_server
    state.update(type=content_type, zip=body)
    output = tmp_path / "out.zip"
    args = skill.build_parser().parse_args(["export", "--version", "test-release", "--output", str(output), "--base-url", site])
    with pytest.raises(RuntimeError, match="ZIP export"):
        skill.run(args)
    assert not output.exists()
    assert not list(tmp_path.glob(".annotation-*"))


def test_export_never_overwrites_existing_or_racing_file(api_server, tmp_path, monkeypatch):
    site, requests, _ = api_server
    output = tmp_path / "out.zip"
    args = skill.build_parser().parse_args(["export", "--version", "test-release", "--output", str(output), "--base-url", site])
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        skill.run(args)
    assert output.read_bytes() == b"existing" and requests == []
    output.unlink()
    real_link = skill.os.link

    def race(source, target):
        Path(target).write_bytes(b"concurrent result")
        real_link(source, target)

    monkeypatch.setattr(skill.os, "link", race)
    with pytest.raises(FileExistsError):
        skill.run(args)
    assert output.read_bytes() == b"concurrent result"
    assert not list(tmp_path.glob(".annotation-*"))


def test_version_error_is_reported_without_creating_output(api_server, tmp_path, capsys):
    site, _, state = api_server
    state.update(status=409, type="application/json", zip=b'{"detail":"Dataset version changed"}')
    output = tmp_path / "out.zip"
    assert skill.main(["export", "--version", "old", "--output", str(output), "--base-url", site]) == 1
    assert "HTTP 409" in capsys.readouterr().err
    assert not output.exists()


def test_client_base_url_and_input_bounds(monkeypatch):
    monkeypatch.delenv("POTATO_GENOME_ANNOTATIONS_BASE_URL", raising=False)
    assert skill.build_parser().parse_args(["metadata"]).base_url == "https://potato-agent.ynnu.edu.cn"
    for suffix in ("", "/", "/functional-annotation", "/api/genome-annotations"):
        assert skill.api_root("https://example.org" + suffix) == "https://example.org/api/genome-annotations"
    for url in ("file:///tmp/x", "https://user:password@example.org", "https://example.org/?key=x", "https://example.org/other"):
        with pytest.raises(ValueError):
            skill.api_root(url)
    with pytest.raises(ValueError, match="5000"):
        skill.parse_ids([" ".join(f"id{i}" for i in range(5001))])
    args = skill.build_parser().parse_args(["query", "--all-assemblies", "--assembly", "monoploid/DMv8.2"])
    with pytest.raises(ValueError):
        skill.query_payload(args)


def test_client_round_trip_against_built_annotation_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from interface import genome_annotations
    from interface.build_genome_annotations_db import build_database
    from interface.test_build_genome_annotations_db import make_source

    source = make_source(tmp_path / "source")
    database = tmp_path / "release/annotations.sqlite"
    build_database(source, database, "test-v1")
    monkeypatch.setenv("GENOME_ANNOTATIONS_DB_PATH", str(database))
    app = FastAPI()
    app.include_router(genome_annotations.router)

    with TestClient(app) as client:
        def open_request(request, timeout):
            parsed = urllib.parse.urlsplit(request.full_url)
            response = client.request(request.method, parsed.path + ("?" + parsed.query if parsed.query else ""),
                                      content=request.data, headers=dict(request.header_items()))
            assert response.status_code == 200, response.text
            stream = io.BytesIO(response.content)
            stream.headers = response.headers
            return stream

        monkeypatch.setattr(skill.urllib.request, "urlopen", open_request)
        args = skill.build_parser().parse_args(["query", "gene.1", "--signature", "PF03106", "--limit", "1"])
        response = skill.run(args)["data"]
        assert response["total"] == 1
        assert response["items"][0]["matchedTranscriptIds"] == ["gene.1.1"]
        assert response["items"][0]["isoformPresenceConflict"] is True
        output = tmp_path / "actual-api.zip"
        args = skill.build_parser().parse_args(["export", "gene.1", "--signature", "PF03106",
                                               "--table", "genes", "--table", "domains", "--output", str(output)])
        result = skill.run(args)
        assert result["metadata"]["datasetVersion"] == "test-v1"
        assert result["metadata"]["tableCounts"] == {"genes": 1, "domains": 2}
        with zipfile.ZipFile(output) as archive:
            domain_rows = archive.read("domains.tsv").decode().splitlines()
            assert len(domain_rows) == 3  # Both overlapping source hits are preserved.

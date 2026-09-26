from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

from interface.test_efp_browser import site  # noqa: F401 - real API with a temporary database
from interface.test_efp_api import pdf_metadata


SCRIPT = Path(__file__).resolve().parents[1] / "skills/potato-knowledge-bioinformatics/potato-efp-expression/scripts/query_potato_efp.py"
spec = importlib.util.spec_from_file_location("query_potato_efp", SCRIPT)
skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skill)


def test_skill_cli_search_query_and_download(site, tmp_path):
    def cli(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *args, "--base-url", site],
                              capture_output=True, text=True, timeout=30)

    result = cli("source")
    assert result.returncode == 0, result.stderr
    source = json.loads(result.stdout)
    assert source["api_url"] == site + "/api/efp/source"
    assert source["data"]["data"]["scope"] == "tissue"
    result = cli("search", "GeneA")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["genes"][0]["geneId"] == "GeneA"
    result = cli("query", "GeneA", "--transform", "row_zscore")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)["data"]
    output = tmp_path / "plots with spaces" / "GeneA.pdf"
    result = cli("plot", "GeneA", "--transform", "row_zscore", "--output", str(output))
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["pdf"] == str(output.resolve())
    assert summary["bytes"] == output.stat().st_size
    assert summary["source_url"] == source["api_url"]
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(summary["api_url"]).query) == {
        "gene": ["GeneA"], "transform": ["row_zscore"],
    }
    pdf = output.read_bytes()
    assert pdf_metadata(pdf)["tissueValues"] == data["tissueValues"]
    assert b"/Subtype /Image" not in pdf
    assert cli("plot", "GeneA", "--output", str(output)).returncode == 1
    assert output.read_bytes() == pdf
    absent = tmp_path / "missing.pdf"
    result = cli("plot", "NoSuchGene", "--output", str(absent))
    assert result.returncode == 1 and "HTTP 404" in result.stderr
    assert not absent.exists()


def test_client_rejects_non_pdf_response_before_creating_file(monkeypatch, tmp_path):
    from email.message import Message
    import io

    response = io.BytesIO(b"<html>Login required</html>")
    response.headers = Message()
    response.headers["Content-Type"] = "text/html"
    monkeypatch.setattr(skill.urllib.request, "urlopen", lambda *a, **kw: response)
    output = tmp_path / "bad.pdf"
    args = skill.build_parser().parse_args(["plot", "GeneA", "--output", str(output)])
    with pytest.raises(RuntimeError, match="complete PDF"):
        skill.run(args)
    assert not output.exists()


def test_client_url_selection_and_validation(monkeypatch, tmp_path):
    config = tmp_path / "api-base-url.txt"
    monkeypatch.setattr(skill, "INSTALL_BASE_URL_FILE", config)
    monkeypatch.delenv("POTATO_EFP_BASE_URL", raising=False)
    assert skill.build_parser().parse_args(["query", "GeneA"]).base_url == skill.DEFAULT_BASE_URL
    config.write_text("http://10.176.225.185:3000\n", encoding="utf-8")
    assert skill.build_parser().parse_args(["query", "GeneA"]).base_url == "http://10.176.225.185:3000"
    monkeypatch.setenv("POTATO_EFP_BASE_URL", "http://127.0.0.1:3000")
    assert skill.build_parser().parse_args(["query", "GeneA"]).base_url == "http://127.0.0.1:3000"
    args = skill.build_parser().parse_args(["query", "GeneA", "--base-url", "https://example.org/efp"])
    assert skill.api_root(args.base_url) == "https://example.org/api/efp"
    for invalid in ("file:///tmp/data", "https://user:secret@example.org", "https://example.org/efp?gene=x"):
        with pytest.raises(ValueError):
            skill.api_root(invalid)


def test_preview_proxy_preserves_pdf_and_download_headers(site):
    from fastapi.testclient import TestClient
    from interface.preview_efp import create_preview_app

    with TestClient(create_preview_app(site)) as client:
        source = client.get("/api/efp/source")
        assert source.status_code == 200
        assert source.json()["figure"]["page"] == "/efp"
        response = client.get("/api/efp/export.pdf?gene=GeneA&transform=tpm")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == 'attachment; filename="GeneA_efp_tpm.pdf"'
        assert pdf_metadata(response.content)["geneId"] == "GeneA"

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface import app as interface_app_mod
from interface import wgcna_viewer as wgcna_viewer_mod


def test_wgcna_entry_and_static_paths_are_prefixed() -> None:
    lite_index = (REPO_ROOT / "interface/static/lite/index.html").read_text(encoding="utf-8")
    assert '<a class="portal-nav-item" href="/wgcna">WGCNA Network</a>' in lite_index

    wgcna_index = (REPO_ROOT / "interface/static/wgcna/index.html").read_text(encoding="utf-8")
    assert 'href="/static/wgcna/styles.css' in wgcna_index
    assert 'src="/static/wgcna/vendor/cytoscape.min.js"' in wgcna_index
    assert 'src="/static/wgcna/app.js' in wgcna_index
    assert 'href="/wgcna" aria-current="page"' in wgcna_index
    assert 'href="/styles.css"' not in wgcna_index
    assert 'src="/app.js"' not in wgcna_index

    wgcna_css = (REPO_ROOT / "interface/static/wgcna/styles.css").read_text(encoding="utf-8")
    assert "background: url('../background.png')" in wgcna_css


def test_wgcna_page_route_serves_static_page() -> None:
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/wgcna")
        assert response.status_code == 200
        assert "WGCNA Network" in response.text
    finally:
        client.close()


def test_wgcna_api_does_not_refresh_runtime_activity() -> None:
    class Request:
        class Url:
            path = "/api/wgcna/status"

        url = Url()

    assert interface_app_mod._should_refresh_activity_for_request(Request()) is False


def test_wgcna_status_without_database_url_returns_503(monkeypatch) -> None:
    monkeypatch.delenv("WGCNA_DATABASE_URL", raising=False)
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get("/api/wgcna/status")
        assert response.status_code == 503
        assert "WGCNA_DATABASE_URL" in response.text
    finally:
        client.close()


def test_wgcna_coexpression_route_passes_query_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_load_coexpression(**kwargs):
        captured.update(kwargs)
        return {
            "query_genes": ["GeneA"],
            "warnings": [],
            "summary": {
                "networks": ["leaf", "root"],
                "node_count": 1,
                "edge_count": 0,
                "tom_edge_count": 0,
                "module_overlap_count": 0,
            },
            "elements": {"nodes": [], "edges": []},
            "module_overlaps": [],
        }

    monkeypatch.setattr(wgcna_viewer_mod, "load_coexpression", fake_load_coexpression)
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/wgcna/coexpression",
            params={
                "genes": "GeneA",
                "networks": "leaf,root",
                "top_n": "25",
                "tom_min": "0.12",
                "same_module_only": "false",
                "include_neighbor_edges": "false",
                "include_cross_network": "true",
                "include_module_overlaps": "true",
                "include_shared_edges": "false",
                "max_total_edges": "200",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["query_genes"] == ["GeneA"]
    finally:
        client.close()

    assert captured["genes"] == "GeneA"
    assert captured["networks"] == "leaf,root"
    assert captured["top_n"] == 25
    assert captured["tom_min"] == 0.12
    assert captured["same_module_only"] is False
    assert captured["include_neighbor_edges"] is False
    assert captured["include_cross_network"] is True
    assert captured["include_module_overlaps"] is True
    assert captured["include_shared_edges"] is False
    assert captured["max_total_edges"] == 200


def test_wgcna_parse_gene_list_deduplicates_and_limits() -> None:
    assert wgcna_viewer_mod.parse_gene_list(" GeneA, GeneB\nGeneA ") == ["GeneA", "GeneB"]


def test_wgcna_removes_query_nodes_without_tom_edges() -> None:
    nodes = {
        "leaf:GeneA": {
            "data": {
                "id": "leaf:GeneA",
                "network_id": "leaf",
                "gene_id": "GeneA",
                "is_query_gene": True,
            }
        },
        "root:GeneA": {
            "data": {
                "id": "root:GeneA",
                "network_id": "root",
                "gene_id": "GeneA",
                "is_query_gene": True,
            }
        },
        "leaf:GeneB": {
            "data": {
                "id": "leaf:GeneB",
                "network_id": "leaf",
                "gene_id": "GeneB",
                "is_query_gene": False,
            }
        },
    }
    tom_edges = {
        "tom:leaf:GeneA--GeneB": {
            "data": {
                "edge_type": "tom_edge",
                "network_id": "leaf",
                "gene_a": "GeneA",
                "gene_b": "GeneB",
            }
        }
    }

    removed = wgcna_viewer_mod._remove_query_nodes_without_tom_edges(
        nodes,
        tom_edges,
        ["GeneA"],
    )

    assert removed == 1
    assert "leaf:GeneA" in nodes
    assert "root:GeneA" not in nodes
    assert "leaf:GeneB" in nodes

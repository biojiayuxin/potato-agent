from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface import app as interface_app_mod


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _build_spatial_fixture(root: Path) -> None:
    _write_json(
        root / "datasets.json",
        {
            "defaultDataset": "toy",
            "datasets": [
                {
                    "id": "toy",
                    "label": "Toy Dataset",
                    "dataRoot": "data",
                    "dataPath": "/data",
                    "defaultSample": "S1",
                    "defaultGene": "GeneA",
                    "samples": [
                        {
                            "id": "S1",
                            "label": "S1",
                            "columns": 1,
                            "contoursPath": "/data/contours/S1",
                        }
                    ],
                }
            ],
        },
    )
    _write_json(root / "data" / "genes.json", ["GeneA"])
    _write_json(
        root / "data" / "replicates.json",
        {
            "samples": {
                "S1": [
                    {"id": "s1_rep1", "bbox": [0, 0, 9, 9], "cells": [1], "tiles": [[0, 0]]}
                ]
            }
        },
    )
    _write_json(root / "data" / "clusters.json", {"samples": {"S1": {"1": "0"}}})
    _write_json(
        root / "data" / "contours" / "S1" / "manifest.json",
        {
            "sample": "S1",
            "width": 10,
            "height": 10,
            "tileSize": 10,
            "tiles": [{"x": 0, "y": 0, "url": "/data/contours/S1/tile_0_0.json"}],
        },
    )
    _write_json(
        root / "data" / "contours" / "S1" / "tile_0_0.json",
        {"cells": [{"id": 1, "contours": [[[0, 0], [1, 0], [1, 1]]]}]},
    )
    (root / "colors.txt").write_text(
        "#Clusters Colors\n0 #FF0000\n\n#Tissues Colors\nLeaf #00FF00\n",
        encoding="utf-8",
    )

    conn = sqlite3.connect(root / "data" / "expression.sqlite")
    try:
        conn.executescript(
            """
            create table genes(gene_id integer primary key, gene text not null);
            create table sample_genes(sample text, gene_id integer, vmin real, vmax real, nonzero integer);
            create table expression_values(sample text, gene_id integer, cell_id integer, value real);
            create table dotplot_clusters(cluster_id text primary key, cluster_order integer, cell_count integer);
            create table dotplot_gene_cluster_stats(
              gene_id integer,
              cluster_id text,
              avg_expr real,
              pct_expr real,
              expressing_count integer,
              cell_count integer
            );
            create table tissues(
              tissue_id text primary key,
              tissue_label text,
              tissue_order integer,
              cell_count integer,
              assigned_cell_count integer
            );
            create table tissue_cell_assignments(sample text, cell_id integer, tissue_id text);
            create table metadata(key text primary key, value text);
            """
        )
        conn.execute("insert into genes values (1, 'GeneA')")
        conn.execute("insert into sample_genes values ('S1', 1, 0.0, 3.5, 1)")
        conn.execute("insert into expression_values values ('S1', 1, 1, 3.5)")
        conn.execute("insert into dotplot_clusters values ('0', 0, 1)")
        conn.execute("insert into dotplot_gene_cluster_stats values (1, '0', 3.5, 100.0, 1, 1)")
        conn.execute("insert into tissues values ('Leaf', 'Leaf', 0, 1, 1)")
        conn.execute("insert into tissue_cell_assignments values ('S1', 1, 'Leaf')")
        conn.commit()
    finally:
        conn.close()


def test_spatial_viewer_is_public_and_reads_fixture(monkeypatch, tmp_path) -> None:
    _build_spatial_fixture(tmp_path)
    monkeypatch.setenv("SPATIAL_VIEWER_DATA_ROOT", str(tmp_path))

    client = TestClient(interface_app_mod.app)
    try:
        index = client.get("/spatial")
        assert index.status_code == 200
        assert "/static/spatial/app.js" in index.text

        datasets = client.get("/api/spatial/datasets")
        assert datasets.status_code == 200, datasets.text
        payload = datasets.json()
        assert payload["defaultDataset"] == "toy"
        assert payload["datasets"][0]["dataPath"] == "/api/spatial/data/toy"
        assert payload["datasets"][0]["samples"][0]["contoursPath"] == (
            "/api/spatial/data/_root/data/contours/S1"
        )

        gene = client.get("/api/spatial/gene", params={"dataset": "toy", "gene": "GeneA"})
        assert gene.status_code == 200, gene.text
        assert gene.json()["samples"]["S1"]["values"] == [[1, 3.5]]

        dotplot = client.get("/api/spatial/dotplot", params={"dataset": "toy", "gene": "GeneA"})
        assert dotplot.status_code == 200, dotplot.text
        assert dotplot.json()["clusters"][0]["pctExpr"] == 100.0

        tissues = client.get("/api/spatial/tissues", params={"dataset": "toy"})
        assert tissues.status_code == 200, tissues.text
        assert tissues.json()["samples"]["S1"]["cells"] == [[1, "Leaf"]]

        colors = client.get("/api/spatial/colors", params={"dataset": "toy"})
        assert colors.status_code == 200, colors.text
        assert colors.json()["clusters"]["0"] == "#FF0000"
    finally:
        client.close()


def test_spatial_data_route_rejects_traversal_and_sqlite(monkeypatch, tmp_path) -> None:
    _build_spatial_fixture(tmp_path)
    monkeypatch.setenv("SPATIAL_VIEWER_DATA_ROOT", str(tmp_path))

    client = TestClient(interface_app_mod.app)
    try:
        manifest = client.get("/api/spatial/data/_root/data/contours/S1/manifest.json")
        assert manifest.status_code == 200, manifest.text

        sqlite_response = client.get("/api/spatial/data/toy/expression.sqlite")
        assert sqlite_response.status_code == 404

        traversal = client.get("/api/spatial/data/toy/../datasets.json")
        assert traversal.status_code in {400, 404}
    finally:
        client.close()


def test_spatial_api_does_not_refresh_runtime_activity() -> None:
    class Request:
        class Url:
            path = "/api/spatial/datasets"

        url = Url()

    assert interface_app_mod._should_refresh_activity_for_request(Request()) is False


def test_spatial_entry_and_static_paths_are_prefixed() -> None:
    lite_index = (REPO_ROOT / "interface/static/lite/index.html").read_text(encoding="utf-8")
    assert '<section id="login-view" class="login-view">\n        <a class="spatial-entry-button" href="/spatial">' in lite_index

    spatial_index = (REPO_ROOT / "interface/static/spatial/index.html").read_text(encoding="utf-8")
    assert 'href="/static/spatial/style.css"' in spatial_index
    assert 'src="/static/spatial/app.js"' in spatial_index
    assert 'href="/style.css"' not in spatial_index
    assert 'src="/app.js"' not in spatial_index

    spatial_app = (REPO_ROOT / "interface/static/spatial/app.js").read_text(encoding="utf-8")
    assert 'const API_BASE = "/api/spatial";' in spatial_app
    assert 'fetch("/api/datasets")' not in spatial_app

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface import app as interface_app_mod
from interface import spatial_viewer as spatial_viewer_mod


SPATIAL_SKILL_SCRIPT = (
    REPO_ROOT
    / "skills/potato-knowledge-bioinformatics/potato-spatial-expression/scripts/query_potato_spatial.py"
)


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
                        },
                        {
                            "id": "S2",
                            "label": "S2",
                            "columns": 1,
                            "contoursPath": "/data/contours/S2",
                        }
                    ],
                }
            ],
        },
    )
    _write_json(root / "data" / "genes.json", ["GeneA", "GeneB"])
    _write_json(
        root / "data" / "replicates.json",
        {
            "samples": {
                "S1": [
                    {
                        "id": "s1_rep1",
                        "bbox": [0, 0, 9, 9],
                        "cells": [1, 2, 3],
                        "tiles": [[0, 0]],
                    }
                ],
                "S2": [
                    {
                        "id": "s2_rep1",
                        "bbox": [0, 0, 9, 9],
                        "cells": [4, 5],
                        "tiles": [[0, 0]],
                    }
                ],
            }
        },
    )
    _write_json(
        root / "data" / "clusters.json",
        {
            "clusters": [
                {"id": "0", "label": "Cluster 0", "order": 0},
                {"id": "1", "label": "Cluster 1", "order": 1},
            ],
            "samples": {
                "S1": {"cells": [[1, "0"], [2, "0"], [3, "1"]]},
                "S2": {"cells": [[4, "0"], [5, "1"]]},
            },
        },
    )
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
        {
            "cells": [
                {"id": 1, "contours": [[[0, 0], [1, 0], [1, 1]]]},
                {"id": 2, "contours": [[[2, 0], [3, 0], [3, 1]]]},
                {"id": 3, "contours": [[[4, 0], [5, 0], [5, 1]]]},
            ]
        },
    )
    _write_json(
        root / "data" / "contours" / "S2" / "manifest.json",
        {
            "sample": "S2",
            "width": 10,
            "height": 10,
            "tileSize": 10,
            "tiles": [{"x": 0, "y": 0, "url": "/data/contours/S2/tile_0_0.json"}],
        },
    )
    _write_json(
        root / "data" / "contours" / "S2" / "tile_0_0.json",
        {
            "cells": [
                {"id": 4, "contours": [[[0, 0], [1, 0], [1, 1]]]},
                {"id": 5, "contours": [[[2, 0], [3, 0], [3, 1]]]},
            ]
        },
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
        conn.execute("insert into genes values (2, 'GeneB')")
        conn.execute("insert into sample_genes values ('S1', 1, 0.0, 3.5, 2)")
        conn.execute("insert into sample_genes values ('S2', 1, 0.0, 2.0, 1)")
        conn.execute("insert into expression_values values ('S1', 1, 1, 3.5)")
        conn.execute("insert into expression_values values ('S1', 1, 3, 1.0)")
        conn.execute("insert into expression_values values ('S2', 1, 5, 2.0)")
        conn.execute("insert into dotplot_clusters values ('0', 0, 1)")
        conn.execute("insert into dotplot_gene_cluster_stats values (1, '0', 3.5, 100.0, 1, 1)")
        conn.execute("insert into tissues values ('Leaf', 'Leaf', 0, 3, 3)")
        conn.execute("insert into tissues values ('Pith', 'Pith', 1, 2, 2)")
        conn.execute("insert into tissue_cell_assignments values ('S1', 1, 'Leaf')")
        conn.execute("insert into tissue_cell_assignments values ('S1', 2, 'Pith')")
        conn.execute("insert into tissue_cell_assignments values ('S1', 3, 'Leaf')")
        conn.execute("insert into tissue_cell_assignments values ('S2', 4, 'Pith')")
        conn.execute("insert into tissue_cell_assignments values ('S2', 5, 'Leaf')")
        conn.commit()
    finally:
        conn.close()


def _find_expression_row(
    rows: list[dict[str, object]],
    *,
    scope: str,
    group_id: str,
    sample: str | None = None,
) -> dict[str, object]:
    for row in rows:
        if row["scope"] == scope and row["groupId"] == group_id and row.get("sample") == sample:
            return row
    raise AssertionError(f"row not found: scope={scope} group_id={group_id} sample={sample}")


def _assert_no_forbidden_keys(payload: object, forbidden: set[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in forbidden
            _assert_no_forbidden_keys(value, forbidden)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_forbidden_keys(value, forbidden)


def _load_spatial_skill_script():
    spec = importlib.util.spec_from_file_location("query_potato_spatial", SPATIAL_SKILL_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_agent_expression_payload(dataset: str, gene: str) -> dict[str, object]:
    return {
        "dataset": dataset,
        "gene": gene,
        "samples": ["S1"],
        "range": {"vmin": 0.0, "vmax": 3.5},
        "clusterColumn": "seurat_clusters",
        "tissueColumn": "celltype",
        "clusterExpression": [
            {
                "scope": "dataset",
                "sample": None,
                "groupId": "0",
                "groupLabel": "Cluster 0",
                "cellCount": 2,
                "expressingCount": 1,
                "pctExpr": 50.0,
                "avgExpr": 1.75,
                "avgExprExpressing": 3.5,
                "sumExpr": 3.5,
                "maxExpr": 3.5,
            },
            {
                "scope": "sample",
                "sample": "S1",
                "groupId": "0",
                "groupLabel": "Cluster 0",
                "cellCount": 2,
                "expressingCount": 1,
                "pctExpr": 50.0,
                "avgExpr": 1.75,
                "avgExprExpressing": 3.5,
                "sumExpr": 3.5,
                "maxExpr": 3.5,
            },
        ],
        "tissueExpression": [
            {
                "scope": "dataset",
                "sample": None,
                "groupId": "Leaf",
                "groupLabel": "Leaf",
                "cellCount": 2,
                "expressingCount": 2,
                "pctExpr": 100.0,
                "avgExpr": 2.25,
                "avgExprExpressing": 2.25,
                "sumExpr": 4.5,
                "maxExpr": 3.5,
            },
            {
                "scope": "sample",
                "sample": "S1",
                "groupId": "Pith",
                "groupLabel": "Pith",
                "cellCount": 1,
                "expressingCount": 0,
                "pctExpr": 0.0,
                "avgExpr": 0.0,
                "avgExprExpressing": 0.0,
                "sumExpr": 0.0,
                "maxExpr": 0.0,
            },
        ],
    }


def test_spatial_viewer_is_public_and_reads_fixture(monkeypatch, tmp_path) -> None:
    _build_spatial_fixture(tmp_path)
    monkeypatch.setenv("SPATIAL_VIEWER_DATA_ROOT", str(tmp_path))
    cluster_names_path = tmp_path / "cluster_name.txt"
    cluster_names_path.write_text(
        "### Toy Dataset\n#ClusterID\tClusterName\n0\tToy cluster cells\n1\tOuter cells\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(spatial_viewer_mod, "CLUSTER_NAME_PATH", cluster_names_path)

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
        assert gene.json()["samples"]["S1"]["values"] == [[1, 3.5], [3, 1.0]]

        dotplot = client.get("/api/spatial/dotplot", params={"dataset": "toy", "gene": "GeneA"})
        assert dotplot.status_code == 200, dotplot.text
        assert dotplot.json()["clusters"][0]["pctExpr"] == 100.0
        assert dotplot.json()["clusters"][0]["name"] == "Toy cluster cells"

        tissues = client.get("/api/spatial/tissues", params={"dataset": "toy"})
        assert tissues.status_code == 200, tissues.text
        assert tissues.json()["samples"]["S1"]["cells"] == [
            [1, "Leaf"],
            [2, "Pith"],
            [3, "Leaf"],
        ]

        colors = client.get("/api/spatial/colors", params={"dataset": "toy"})
        assert colors.status_code == 200, colors.text
        assert colors.json()["clusters"]["0"] == "#FF0000"

        cluster_names = client.get("/api/spatial/cluster-names", params={"dataset": "toy"})
        assert cluster_names.status_code == 200, cluster_names.text
        assert cluster_names.json()["names"] == {"0": "Toy cluster cells", "1": "Outer cells"}
    finally:
        client.close()


def test_spatial_agent_expression_requires_explicit_dataset_and_gene(monkeypatch, tmp_path) -> None:
    _build_spatial_fixture(tmp_path)
    monkeypatch.setenv("SPATIAL_VIEWER_DATA_ROOT", str(tmp_path))

    client = TestClient(interface_app_mod.app)
    try:
        assert client.get(
            "/api/spatial/agent/expression",
            params={"gene": "GeneA"},
        ).status_code == 400
        assert client.get(
            "/api/spatial/agent/expression",
            params={"dataset": "toy"},
        ).status_code == 400
        assert client.get(
            "/api/spatial/agent/expression",
            params={"dataset": "missing", "gene": "GeneA"},
        ).status_code == 404
        assert client.get(
            "/api/spatial/agent/expression",
            params={"dataset": "toy", "gene": "MissingGene"},
        ).status_code == 404
    finally:
        client.close()


def test_spatial_agent_expression_aggregates_sparse_values(monkeypatch, tmp_path) -> None:
    _build_spatial_fixture(tmp_path)
    monkeypatch.setenv("SPATIAL_VIEWER_DATA_ROOT", str(tmp_path))
    cluster_names_path = tmp_path / "cluster_name.txt"
    cluster_names_path.write_text(
        "### Toy Dataset\n#ClusterID\tClusterName\n0\tToy cluster cells\n1\tOuter cells\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(spatial_viewer_mod, "CLUSTER_NAME_PATH", cluster_names_path)

    client = TestClient(interface_app_mod.app)
    try:
        response = client.get(
            "/api/spatial/agent/expression",
            params={"dataset": "toy", "gene": "GeneA"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
    finally:
        client.close()

    _assert_no_forbidden_keys(payload, {"values", "cells"})
    assert payload["dataset"] == "toy"
    assert payload["gene"] == "GeneA"
    assert payload["samples"] == ["S1", "S2"]
    assert payload["range"] == {"vmin": 0.0, "vmax": 3.5}
    assert payload["clusterColumn"] == "seurat_clusters"
    assert payload["tissueColumn"] == "celltype"

    cluster_s1_0 = _find_expression_row(
        payload["clusterExpression"],
        scope="sample",
        sample="S1",
        group_id="0",
    )
    assert cluster_s1_0["groupLabel"] == "Toy cluster cells"
    assert cluster_s1_0["cellCount"] == 2
    assert cluster_s1_0["expressingCount"] == 1
    assert cluster_s1_0["pctExpr"] == 50.0
    assert cluster_s1_0["avgExpr"] == 1.75
    assert cluster_s1_0["avgExprExpressing"] == 3.5
    assert cluster_s1_0["sumExpr"] == 3.5
    assert cluster_s1_0["maxExpr"] == 3.5

    cluster_dataset_0 = _find_expression_row(
        payload["clusterExpression"],
        scope="dataset",
        group_id="0",
    )
    assert cluster_dataset_0["sample"] is None
    assert cluster_dataset_0["cellCount"] == 3
    assert cluster_dataset_0["expressingCount"] == 1
    assert cluster_dataset_0["pctExpr"] == pytest.approx(100.0 / 3.0)
    assert cluster_dataset_0["avgExpr"] == pytest.approx(3.5 / 3.0)

    tissue_s1_pith = _find_expression_row(
        payload["tissueExpression"],
        scope="sample",
        sample="S1",
        group_id="Pith",
    )
    assert tissue_s1_pith["cellCount"] == 1
    assert tissue_s1_pith["expressingCount"] == 0
    assert tissue_s1_pith["pctExpr"] == 0.0
    assert tissue_s1_pith["avgExpr"] == 0.0

    tissue_dataset_leaf = _find_expression_row(
        payload["tissueExpression"],
        scope="dataset",
        group_id="Leaf",
    )
    assert tissue_dataset_leaf["cellCount"] == 3
    assert tissue_dataset_leaf["expressingCount"] == 3
    assert tissue_dataset_leaf["pctExpr"] == 100.0
    assert tissue_dataset_leaf["avgExpr"] == pytest.approx(6.5 / 3.0)
    assert tissue_dataset_leaf["sumExpr"] == 6.5


def test_spatial_skill_dotplot_writes_cluster_tsv_and_pdf(monkeypatch, tmp_path) -> None:
    module = _load_spatial_skill_script()
    calls = []

    def fake_request(base_url: str, dataset: str, gene: str, timeout: int) -> dict[str, object]:
        calls.append((base_url, dataset, gene, timeout))
        return _mock_agent_expression_payload(dataset, gene)

    monkeypatch.setattr(module, "request_expression", fake_request)
    outdir = tmp_path / "plots"
    code = module.main(
        [
            "dotplot",
            "GeneA",
            "--dataset",
            "toy",
            "--group",
            "cluster",
            "--outdir",
            str(outdir),
        ]
    )
    assert code == 0

    tsv_path = outdir / "GeneA_cluster_dotplot.tsv"
    pdf_path = outdir / "GeneA_cluster_dotplot.pdf"
    assert tsv_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF-1.4")
    lines = tsv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "\t".join(module.TSV_FIELDS)
    assert lines[1].split("\t") == [
        "toy",
        "GeneA",
        "cluster",
        "dataset",
        "",
        "0",
        "Cluster 0",
        "2",
        "1",
        "50.0",
        "1.75",
        "3.5",
        "3.5",
        "3.5",
    ]
    assert calls == [(module.DEFAULT_BASE_URL, "toy", "GeneA", module.TIMEOUT_SECONDS)]


def test_spatial_skill_dotplot_supports_tissue_group(monkeypatch, tmp_path) -> None:
    module = _load_spatial_skill_script()

    def fake_request(base_url: str, dataset: str, gene: str, timeout: int) -> dict[str, object]:
        return _mock_agent_expression_payload(dataset, gene)

    monkeypatch.setattr(module, "request_expression", fake_request)
    outdir = tmp_path / "plots"
    code = module.main(
        [
            "dotplot",
            "GeneA",
            "--dataset",
            "toy",
            "--group",
            "tissue",
            "--outdir",
            str(outdir),
        ]
    )
    assert code == 0

    tsv_path = outdir / "GeneA_tissue_dotplot.tsv"
    pdf_path = outdir / "GeneA_tissue_dotplot.pdf"
    assert tsv_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF-1.4")
    tsv_text = tsv_path.read_text(encoding="utf-8")
    assert "\ttissue\tsample\tS1\tPith\tPith\t1\t0\t0.0\t0.0" in tsv_text


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
    assert 'class="agent-return" href="/"' in spatial_index
    assert 'id="dotplotTooltip"' in spatial_index
    assert 'src="/static/spatial/app.js"' in spatial_index
    assert 'href="/style.css"' not in spatial_index
    assert 'src="/app.js"' not in spatial_index

    spatial_app = (REPO_ROOT / "interface/static/spatial/app.js").read_text(encoding="utf-8")
    assert 'const API_BASE = "/api/spatial";' in spatial_app
    assert 'fetch("/api/datasets")' not in spatial_app

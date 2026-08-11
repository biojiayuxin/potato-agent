from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "plot_wgcna_network",
    SCRIPT_DIR / "plot_wgcna_network.py",
)
assert SPEC is not None and SPEC.loader is not None
PLOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLOT)


PAYLOAD = {
    "query_genes": ["DM8.2_chr09G24280"],
    "warnings": [],
    "summary": {
        "networks": ["root", "tuberization"],
        "node_count": 3,
        "edge_count": 2,
        "tom_edge_count": 1,
        "module_overlap_count": 0,
    },
    "elements": {
        "nodes": [
            {
                "data": {
                    "id": "root:DM8.2_chr09G24280",
                    "gene_id": "DM8.2_chr09G24280",
                    "network_id": "root",
                    "is_query_gene": True,
                }
            },
            {
                "data": {
                    "id": "root:DM8.2_chr07G08200",
                    "gene_id": "DM8.2_chr07G08200",
                    "network_id": "root",
                    "is_query_gene": False,
                }
            },
            {
                "data": {
                    "id": "tuberization:DM8.2_chr09G24280",
                    "gene_id": "DM8.2_chr09G24280",
                    "network_id": "tuberization",
                    "is_query_gene": True,
                }
            },
        ],
        "edges": [
            {
                "data": {
                    "source": "root:DM8.2_chr09G24280",
                    "target": "root:DM8.2_chr07G08200",
                    "edge_type": "tom_edge",
                    "network_id": "root",
                    "rank": 1,
                    "shared_coexpression": True,
                }
            },
            {
                "data": {
                    "source": "root:DM8.2_chr09G24280",
                    "target": "tuberization:DM8.2_chr09G24280",
                    "edge_type": "same_gene",
                }
            },
        ],
    },
}
API_URL = (
    "https://potato-agent.ynnu.edu.cn/api/wgcna/coexpression?"
    "genes=DM8.2_chr09G24280&networks=root%2Ctuberization&top_n=50&"
    "same_module_only=true&include_neighbor_edges=true&include_cross_network=true&"
    "include_module_overlaps=false&include_shared_edges=true&max_total_edges=3000"
)
QUERY_RESULT = {"api_url": API_URL, "data": PAYLOAD}


class PlotWgcnaNetworkTest(unittest.TestCase):
    def test_cli_only_accepts_saved_data_and_rendering_options(self) -> None:
        help_text = PLOT.build_parser().format_help()
        for query_option in (
            "--network",
            "--top-n",
            "--tom-min",
            "--all-modules",
            "--no-neighbor-edges",
            "--no-cross-network",
            "--no-shared-edges",
            "--timeout",
        ):
            with self.subTest(query_option=query_option):
                self.assertNotIn(query_option, help_text)

        source = (SCRIPT_DIR / "plot_wgcna_network.py").read_text(encoding="utf-8")
        self.assertNotIn("from query_wgcna_network", source)
        self.assertNotIn("urllib.request", source)
        self.assertNotIn("request_json(", source)

    def test_loads_query_script_output_and_preserves_query_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_json = Path(directory) / "query.json"
            input_json.write_text(json.dumps(QUERY_RESULT), encoding="utf-8")
            api_url, payload = PLOT.load_query_result(input_json)

        self.assertEqual(api_url, API_URL)
        self.assertEqual(payload, PAYLOAD)
        self.assertEqual(
            PLOT.query_note_from_url(api_url),
            "root,tuberization | top 50 | any TOM | same module",
        )

    def test_rejects_raw_payload_or_non_production_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_json = Path(directory) / "query.json"
            input_json.write_text(json.dumps(PAYLOAD), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "api_url, data"):
                PLOT.load_query_result(input_json)

            invalid = dict(QUERY_RESULT)
            invalid["api_url"] = "https://example.invalid/api/wgcna/coexpression"
            input_json.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "production"):
                PLOT.load_query_result(input_json)

    def test_layout_compacts_selected_interface_network_facets(self) -> None:
        positions = PLOT.compute_positions(
            PAYLOAD["elements"]["nodes"],
            1200.0,
            600.0,
            ["root", "tuberization"],
        )
        root_query = positions["root:DM8.2_chr09G24280"]
        root_neighbor = positions["root:DM8.2_chr07G08200"]
        tuber_query = positions["tuberization:DM8.2_chr09G24280"]
        self.assertLess(root_query[0], 600.0)
        self.assertGreater(root_neighbor[0], root_query[0])
        self.assertGreater(tuber_query[0], 600.0)
        self.assertAlmostEqual(root_query[1], tuber_query[1])

    def test_svg_contains_vector_shapes_and_page_colors(self) -> None:
        svg = PLOT.render_svg(
            PAYLOAD,
            api_url=API_URL,
            width=1200.0,
            stage_height=620.0,
            label_mode="all",
            query_note="root,tuberization | top 25 | TOM >= 0.02 | same module",
        )
        self.assertTrue(svg.startswith("<svg "))
        self.assertIn("#7c3aed", svg)
        self.assertIn("#0ea5e9", svg)
        self.assertIn("<polygon", svg)
        self.assertIn("<circle", svg)
        self.assertIn('stroke-dasharray="6 5"', svg)
        self.assertNotIn("<image", svg)

    def test_pdf_is_vector_only(self) -> None:
        pdf = PLOT.render_pdf(
            PAYLOAD,
            api_url=API_URL,
            width=1200.0,
            stage_height=620.0,
            label_mode="query",
            query_note="root,tuberization | top 25 | any TOM | same module",
        )
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertNotIn(b"/Subtype /Image", pdf)
        self.assertIn(b"Potato Agent wgcna-network-query", pdf)

    def test_main_renders_saved_query_result_without_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_json = root / "query.json"
            output_svg = root / "network.svg"
            relative_output = os.path.relpath(output_svg, Path.cwd())
            input_json.write_text(json.dumps(QUERY_RESULT), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = PLOT.main(
                    [str(input_json), "--labels", "query", "--output", relative_output]
                )

            self.assertEqual(status, 0)
            self.assertTrue(output_svg.read_text(encoding="utf-8").startswith("<svg "))
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["input_json"], str(input_json.resolve()))
            self.assertEqual(result["output"], str(output_svg.resolve()))
            self.assertTrue(Path(result["output"]).is_absolute())
            self.assertEqual(result["api_url"], API_URL)


if __name__ == "__main__":
    unittest.main()

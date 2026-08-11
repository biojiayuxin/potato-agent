from __future__ import annotations

import importlib.util
import json
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "query_wgcna_network.py"
SPEC = importlib.util.spec_from_file_location("query_wgcna_network", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
QUERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUERY)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class QueryWgcnaNetworkTest(unittest.TestCase):
    def test_api_root_is_the_production_https_deployment(self) -> None:
        self.assertEqual(
            QUERY.API_ROOT,
            "https://potato-agent.ynnu.edu.cn/api/wgcna",
        )
        parser_help = QUERY.build_parser().format_help()
        self.assertNotIn("base-url", parser_help)
        self.assertNotIn("localhost", parser_help)

    def test_request_json_uses_the_fixed_api_root(self) -> None:
        response = FakeResponse({"configured": True})
        with mock.patch.object(QUERY.urllib.request, "urlopen", return_value=response) as urlopen:
            url, payload = QUERY.request_json(
                "genes",
                params={"q": "DM8.2_chr09G2428", "limit": 5},
                timeout=12,
            )

        self.assertEqual(payload, {"configured": True})
        self.assertEqual(url, urlopen.call_args.args[0].full_url)
        self.assertTrue(url.startswith(QUERY.API_ROOT + "/genes?"))
        parsed = urllib.parse.urlsplit(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "potato-agent.ynnu.edu.cn")
        self.assertEqual(
            urllib.parse.parse_qs(parsed.query),
            {"q": ["DM8.2_chr09G2428"], "limit": ["5"]},
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 12)

    def test_gene_ids_are_exact_and_deduplicated(self) -> None:
        self.assertEqual(
            QUERY.parse_gene_list(
                ["DM8.2_chr09G24280,DM8.2_chr07G08200", "DM8.2_chr09G24280"]
            ),
            ["DM8.2_chr09G24280", "DM8.2_chr07G08200"],
        )
        for invalid in ("DM8C09G24280", "dm8.2_chr09G24280", "Soltu.DM.09G024280"):
            with self.subTest(gene_id=invalid):
                with self.assertRaisesRegex(
                    QUERY.argparse.ArgumentTypeError,
                    "exact DMv8.2 gene ID",
                ):
                    QUERY.parse_gene_list([invalid])

    def test_coexpression_defaults_match_interface_page(self) -> None:
        args = QUERY.build_parser().parse_args(
            ["coexpression", "DM8.2_chr09G24280"]
        )
        with mock.patch.object(
            QUERY,
            "request_json",
            return_value=("https://example.invalid", {}),
        ) as request_json:
            QUERY.run_coexpression(args)

        self.assertEqual(
            request_json.call_args.kwargs["params"],
            {
                "genes": "DM8.2_chr09G24280",
                "networks": "all",
                "top_n": 50,
                "same_module_only": "true",
                "include_neighbor_edges": "true",
                "include_cross_network": "true",
                "include_module_overlaps": "false",
                "include_shared_edges": "true",
                "max_total_edges": 3000,
            },
        )

    def test_coexpression_maps_all_query_options(self) -> None:
        args = QUERY.build_parser().parse_args(
            [
                "coexpression",
                "DM8.2_chr09G24280",
                "DM8.2_chr07G08200",
                "--network",
                "root",
                "--network",
                "tuberization",
                "--top-n",
                "25",
                "--tom-min",
                "0.02",
                "--all-modules",
                "--no-neighbor-edges",
                "--no-cross-network",
                "--include-module-overlaps",
                "--no-shared-edges",
                "--max-total-edges",
                "200",
            ]
        )
        with mock.patch.object(
            QUERY,
            "request_json",
            return_value=("https://example.invalid", {}),
        ) as request_json:
            QUERY.run_coexpression(args)

        self.assertEqual(request_json.call_args.args[0], "coexpression")
        self.assertEqual(request_json.call_args.kwargs["timeout"], QUERY.DEFAULT_TIMEOUT)
        self.assertEqual(
            request_json.call_args.kwargs["params"],
            {
                "genes": "DM8.2_chr09G24280,DM8.2_chr07G08200",
                "networks": "root,tuberization",
                "top_n": 25,
                "same_module_only": "false",
                "include_neighbor_edges": "false",
                "include_cross_network": "false",
                "include_module_overlaps": "true",
                "include_shared_edges": "false",
                "max_total_edges": 200,
                "tom_min": 0.02,
            },
        )


if __name__ == "__main__":
    unittest.main()

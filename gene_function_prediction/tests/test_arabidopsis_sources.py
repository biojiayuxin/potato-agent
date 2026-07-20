from __future__ import annotations

import gzip
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from gene_function_prediction import run_pipeline


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://example.test/final",
    ) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers or {}
        self._url = url

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def read1(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def http_policy(
    *, retries: int = 0, max_response_bytes: int = 16 * 1024 * 1024
) -> run_pipeline.SourceHttpPolicy:
    return run_pipeline.SourceHttpPolicy.create(
        timeout=5,
        retries=retries,
        deadline=60,
        max_response_bytes=max_response_bytes,
    )


class ArabidopsisHttpTests(unittest.TestCase):
    def test_http_text_requests_and_decodes_gzip(self) -> None:
        compressed = gzip.compress(b"decoded PlantConnectome HTML")
        response = FakeResponse(
            compressed,
            headers={"Content-Encoding": "GZip, identity"},
        )
        with patch.object(
            run_pipeline.urllib.request,
            "urlopen",
            return_value=response,
        ) as urlopen:
            text, final_url = run_pipeline._http_text(
                "https://plant.connectome.tools/normal/CBF3",
                policy=http_policy(),
                referer="https://plant.connectome.tools/",
                accept_gzip=True,
            )

        self.assertEqual(text, "decoded PlantConnectome HTML")
        self.assertEqual(final_url, "https://example.test/final")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Accept-encoding"), "gzip")

    def test_transient_http_error_gets_three_retries(self) -> None:
        url = "https://example.test/transient"
        failures = [
            urllib.error.HTTPError(url, 503, "unavailable", {}, io.BytesIO())
            for _ in range(3)
        ]
        with (
            patch.object(
                run_pipeline.urllib.request,
                "urlopen",
                side_effect=[*failures, FakeResponse(b"ok")],
            ) as urlopen,
            patch.object(run_pipeline.time, "sleep") as sleep,
        ):
            text, _ = run_pipeline._http_text(
                url,
                policy=http_policy(retries=3),
                referer="https://example.test/",
            )

        self.assertEqual(text, "ok")
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(sleep.call_count, 3)

    def test_non_transient_404_is_not_retried(self) -> None:
        url = "https://example.test/missing"
        error = urllib.error.HTTPError(url, 404, "missing", {}, io.BytesIO())
        with (
            patch.object(
                run_pipeline.urllib.request,
                "urlopen",
                side_effect=error,
            ) as urlopen,
            patch.object(run_pipeline.time, "sleep") as sleep,
        ):
            with self.assertRaises(urllib.error.HTTPError):
                run_pipeline._http_text(
                    url,
                    policy=http_policy(retries=3),
                    referer="https://example.test/",
                )

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_decompressed_response_is_limited_while_streaming(self) -> None:
        compressed = gzip.compress(b"x" * 100)
        with patch.object(
            run_pipeline.urllib.request,
            "urlopen",
            return_value=FakeResponse(
                compressed, headers={"Content-Encoding": "gzip"}
            ),
        ):
            with self.assertRaises(run_pipeline.SourceResponseTooLarge):
                run_pipeline._http_text(
                    "https://example.test/large",
                    policy=http_policy(max_response_bytes=50),
                    referer="https://example.test/",
                    accept_gzip=True,
                )


class TairSourceTests(unittest.TestCase):
    def test_exact_agi_match_is_selected(self) -> None:
        response = {
            "total": 2,
            "docs": [
                {
                    "id": "exact",
                    "gene_name": ["AT4G25480"],
                    "gene_model_ids": ["AT4G25480.1"],
                    "other_names": ["CBF3", "DREB1A"],
                    "description": ["C-repeat binding factor 3"],
                },
                {
                    "id": "nearby",
                    "gene_name": ["AT4G25481"],
                    "gene_model_ids": ["AT4G25481.1"],
                },
            ],
        }
        with patch.object(
            run_pipeline,
            "_http_json",
            return_value=response,
        ) as request:
            result = run_pipeline.fetch_arabidopsis_tair(
                "AT4G25480",
                timeout=5,
                retries=3,
                deadline=60,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tair"]["selected"]["gene_id"], "AT4G25480")
        self.assertEqual(len(result["tair"]["exact_candidates"]), 1)
        self.assertEqual(
            request.call_args.kwargs["payload"],
            {"searchText": "AT4G25480"},
        )

    def test_single_non_exact_result_is_not_found(self) -> None:
        response = {
            "total": 1,
            "docs": [
                {
                    "id": "nearby",
                    "gene_name": ["AT4G25481"],
                    "gene_model_ids": ["AT4G25481.1"],
                }
            ],
        }
        with patch.object(run_pipeline, "_http_json", return_value=response):
            result = run_pipeline.fetch_arabidopsis_tair(
                "AT4G25480",
                timeout=5,
                retries=3,
                deadline=60,
            )

        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("selected", result["tair"])
        self.assertEqual(result["tair"]["exact_candidates"], [])


class PlantConnectomeSourceTests(unittest.TestCase):
    def test_explicit_no_hits_page_returns_empty_result(self) -> None:
        no_hits = """
        <html><body>
          <h2>No hits were found using the query: <strong>UNKNOWN</strong></h2>
        </body></html>
        """
        with patch.object(
            run_pipeline,
            "_http_text",
            return_value=(
                no_hits,
                "https://plant.connectome.tools/normal/UNKNOWN",
            ),
        ) as request:
            result = run_pipeline.fetch_plantconnectome(
                "UNKNOWN",
                max_entities=10,
                max_edges=5,
                timeout=5,
                retries=3,
                deadline=60,
            )

        self.assertEqual(result["status"], "not_found")
        plant = result["plantconnectome"]
        self.assertIsNone(plant["preview"]["unique_id"])
        self.assertEqual(plant["preview"]["rows"], [])
        self.assertEqual(plant["entities"], [])
        request.assert_called_once()

    def test_preview_without_unique_id_is_a_parse_error(self) -> None:
        malformed = """
        const allRowsData = cached ? cached.preview_results : [];
        /* build entityNodeMap */
        """
        with self.assertRaises(run_pipeline.SourceResponseParseError):
            run_pipeline._parse_plantconnectome_preview(malformed)

    def test_oversized_edge_payload_is_rejected_before_parsing(self) -> None:
        with (
            patch.object(run_pipeline, "PLANTCONNECTOME_MAX_EDGE_PAYLOAD_CHARS", 5),
            self.assertRaises(run_pipeline.SourceResponseTooLarge),
        ):
            run_pipeline._parse_plantconnectome_edges('const g = "[{}, {}]";\n')

    def test_entities_and_edges_are_truncated_to_ten_by_five(self) -> None:
        rows = [[f"ENTITY{index}", "gene name"] for index in range(12)]
        preview_html = (
            'const unique_id = "uid-10x5";\n'
            "const allRowsData = cached ? cached.preview_results : "
            f"{json.dumps(rows)};\n"
            "/* build entityNodeMap */"
        )
        edge_payload = [
            {
                "id": f"source-{index}",
                "target": f"target-{index}",
                "inter_type": "regulates",
                "publication": str(index),
            }
            for index in range(7)
        ]
        detail_html = f'const g = "{edge_payload!r}";\n'
        calls: list[str] = []

        def source_response(url: str, **_kwargs: object) -> tuple[str, str]:
            calls.append(url)
            if len(calls) == 1:
                return preview_html, "https://plant.connectome.tools/normal/CBF3"
            return detail_html, url

        with patch.object(
            run_pipeline,
            "_http_text",
            side_effect=source_response,
        ):
            result = run_pipeline.fetch_plantconnectome(
                "CBF3",
                max_entities=10,
                max_edges=5,
                timeout=5,
                retries=3,
                deadline=60,
            )

        self.assertEqual(result["status"], "ok")
        plant = result["plantconnectome"]
        self.assertEqual(plant["preview"]["row_count"], 12)
        self.assertEqual(len(plant["entities"]), 10)
        self.assertEqual(len(calls), 11)
        self.assertTrue(
            all(entity["edge_count_total"] == 7 for entity in plant["entities"])
        )
        self.assertTrue(all(len(entity["edges"]) == 5 for entity in plant["entities"]))
        self.assertEqual(
            plant["entities"][0]["edges"],
            edge_payload[:5],
        )


if __name__ == "__main__":
    unittest.main()

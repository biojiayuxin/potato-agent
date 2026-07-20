from __future__ import annotations

import contextlib
import gzip
import importlib.util
import io
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "query_arabidopsis_gene_search.py"
)
SPEC = importlib.util.spec_from_file_location("query_arabidopsis_gene_search", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, body: bytes, *, headers: dict[str, str] | None = None,
                 url: str = "https://example.test/final") -> None:
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

    def __exit__(self, *args: object) -> None:
        return None


def policy(*, retries: int = 0, max_bytes: int = 1024,
           deadline: float = 30) -> MODULE.HttpPolicy:
    return MODULE.HttpPolicy.create(
        timeout=5,
        retries=retries,
        retry_backoff=1,
        max_response_bytes=max_bytes,
        deadline=deadline,
    )


class HttpTests(unittest.TestCase):
    def test_gzip_is_requested_and_decoded_with_encoding_tokens(self) -> None:
        captured = []
        compressed = gzip.compress(b"decoded text")

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse(
                compressed,
                headers={"Content-Encoding": "GZip, identity"},
            )

        with mock.patch.object(MODULE.urllib.request, "urlopen", side_effect=fake_urlopen):
            text, final_url = MODULE.http_text(
                "https://plant.connectome.tools/normal/AT1G00010",
                accept_gzip=True,
                policy=policy(),
            )

        self.assertEqual(text, "decoded text")
        self.assertEqual(final_url, "https://example.test/final")
        self.assertEqual(captured[0][0].get_header("Accept-encoding"), "gzip")

    def test_gzip_request_accepts_an_uncompressed_response(self) -> None:
        with mock.patch.object(
            MODULE.urllib.request,
            "urlopen",
            return_value=FakeResponse(b"plain text"),
        ):
            text, _ = MODULE.http_text(
                "https://plant.connectome.tools/normal/AT1G00010",
                accept_gzip=True,
                policy=policy(),
            )
        self.assertEqual(text, "plain text")

    def test_transient_http_errors_get_three_retries(self) -> None:
        failures = [
            urllib.error.HTTPError(
                "https://example.test", code, "temporary", {}, io.BytesIO()
            )
            for code in (408, 429, 503)
        ]
        response = FakeResponse(b'{"ok": true}')
        with (
            mock.patch.object(
                MODULE.urllib.request,
                "urlopen",
                side_effect=[*failures, response],
            ) as urlopen,
            mock.patch.object(MODULE.time, "sleep") as sleep,
        ):
            result = MODULE.http_json(
                "https://example.test/data", policy=policy(retries=3)
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])

    def test_timeout_url_error_is_retried(self) -> None:
        with (
            mock.patch.object(
                MODULE.urllib.request,
                "urlopen",
                side_effect=[
                    urllib.error.URLError(socket.timeout("timed out")),
                    FakeResponse(b"ok"),
                ],
            ) as urlopen,
            mock.patch.object(MODULE.time, "sleep"),
        ):
            text, _ = MODULE.http_text(
                "https://example.test/data", policy=policy(retries=1)
            )
        self.assertEqual(text, "ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_non_transient_http_error_is_not_retried(self) -> None:
        error = urllib.error.HTTPError(
            "https://example.test", 404, "missing", {}, io.BytesIO()
        )
        with (
            mock.patch.object(
                MODULE.urllib.request, "urlopen", side_effect=error
            ) as urlopen,
            mock.patch.object(MODULE.time, "sleep") as sleep,
        ):
            with self.assertRaises(urllib.error.HTTPError):
                MODULE.http_text(
                    "https://example.test/data", policy=policy(retries=3)
                )
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_decompressed_response_size_is_limited(self) -> None:
        compressed = gzip.compress(b"x" * 100)
        self.assertLess(len(compressed), 50)
        with mock.patch.object(
            MODULE.urllib.request,
            "urlopen",
            return_value=FakeResponse(
                compressed, headers={"Content-Encoding": "gzip"}
            ),
        ):
            with self.assertRaises(MODULE.ResponseTooLarge):
                MODULE.http_text(
                    "https://example.test/data",
                    accept_gzip=True,
                    policy=policy(max_bytes=50),
                )

    def test_declared_response_size_is_limited_before_read(self) -> None:
        response = FakeResponse(b"small", headers={"Content-Length": "100"})
        with mock.patch.object(
            MODULE.urllib.request, "urlopen", return_value=response
        ):
            with self.assertRaises(MODULE.ResponseTooLarge):
                MODULE.http_text(
                    "https://example.test/data", policy=policy(max_bytes=10)
                )

    def test_expired_deadline_stops_before_opening_connection(self) -> None:
        expired = MODULE.HttpPolicy(
            timeout=5,
            retries=3,
            retry_backoff=1,
            max_response_bytes=1024,
            deadline_at=1,
        )
        with (
            mock.patch.object(MODULE.time, "monotonic", return_value=2),
            mock.patch.object(MODULE.urllib.request, "urlopen") as urlopen,
        ):
            with self.assertRaises(MODULE.QueryDeadlineExceeded):
                MODULE.http_text("https://example.test/data", policy=expired)
        urlopen.assert_not_called()

    def test_deadline_is_checked_while_streaming_response(self) -> None:
        deadline_policy = MODULE.HttpPolicy(
            timeout=5,
            retries=0,
            retry_backoff=1,
            max_response_bytes=1024,
            deadline_at=3,
        )
        with (
            mock.patch.object(MODULE.time, "monotonic", side_effect=[0, 0, 0, 4]),
            mock.patch.object(
                MODULE.urllib.request,
                "urlopen",
                return_value=FakeResponse(b"response chunk"),
            ),
        ):
            with self.assertRaises(MODULE.QueryDeadlineExceeded):
                MODULE.http_text(
                    "https://example.test/data", policy=deadline_policy
                )


class ParserTests(unittest.TestCase):
    def test_preview_recognizes_explicit_no_hits_page_as_empty(self) -> None:
        no_hits = """
        <div class="grid-container fluid">
          <h2>No hits were found using the query: <strong>UNLISTED_QUERY</strong></h2>
          <p>Please try another search term or check your spelling.</p>
        </div>
        """
        self.assertEqual(MODULE.parse_preview(no_hits), (None, []))

    def test_preview_distinguishes_valid_empty_result_from_parse_failure(self) -> None:
        valid_result = """
        const unique_id = "uid-1";
        const allRowsData = cached ? cached.preview_results : [["CBF3", "gene"]];
        /* build entityNodeMap */
        """
        valid_empty = """
        const unique_id = "uid-2";
        const allRowsData = cached ? cached.preview_results : [];
        /* build entityNodeMap */
        """
        self.assertEqual(
            MODULE.parse_preview(valid_result),
            ("uid-1", [["CBF3", "gene"]]),
        )
        self.assertEqual(MODULE.parse_preview(valid_empty), ("uid-2", []))
        with self.assertRaises(MODULE.ResponseParseError):
            MODULE.parse_preview("<html>site layout changed</html>")

    def test_edges_distinguish_valid_empty_result_from_parse_failure(self) -> None:
        self.assertEqual(MODULE.parse_kg_edges('const g = "[]";\n'), [])
        self.assertEqual(
            MODULE.parse_kg_edges("const g = \"[{'id': 'AT1G00010'}]\";\n"),
            [{"id": "AT1G00010"}],
        )
        with self.assertRaises(MODULE.ResponseParseError):
            MODULE.parse_kg_edges("<html>site layout changed</html>")

    def test_invalid_edge_payload_is_a_parse_error(self) -> None:
        with self.assertRaises(MODULE.ResponseParseError):
            MODULE.parse_kg_edges('const g = "not a list";\n')


class StatusTests(unittest.TestCase):
    def test_no_hits_response_becomes_a_valid_empty_result(self) -> None:
        no_hits = """
        <html><body>
          <h2>No hits were found using the query: <strong>UNLISTED_QUERY</strong></h2>
        </body></html>
        """
        with mock.patch.object(
            MODULE,
            "http_text",
            return_value=(
                no_hits,
                "https://plant.connectome.tools/normal/UNLISTED_QUERY",
            ),
        ):
            result = MODULE.plant_details("UNLISTED_QUERY", policy=policy())

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["preview"]["unique_id"], None)
        self.assertEqual(result["preview"]["rows"], [])
        self.assertEqual(result["entities"], [])

    def test_empty_preview_is_not_found(self) -> None:
        with mock.patch.object(
            MODULE,
            "plant_preview",
            return_value={"url": "preview", "unique_id": "uid", "rows": []},
        ):
            result = MODULE.plant_details("AT1G00010", policy=policy())
        self.assertEqual(result["status"], "not_found")

    def test_detail_failure_is_not_returned_as_partial_success(self) -> None:
        with (
            mock.patch.object(
                MODULE,
                "plant_preview",
                return_value={
                    "url": "preview",
                    "unique_id": "uid",
                    "rows": [["AT1G00010", "gene"]],
                },
            ),
            mock.patch.object(
                MODULE,
                "http_text",
                side_effect=urllib.error.URLError(socket.timeout("timed out")),
            ),
        ):
            with self.assertRaises(urllib.error.URLError):
                MODULE.plant_details("AT1G00010", policy=policy())

    def test_full_result_propagates_plant_not_found_status(self) -> None:
        tair_doc = {"gene_name": ["AT1G00010"], "description": ["test"]}
        with (
            mock.patch.object(
                MODULE,
                "tair_search",
                return_value={"total": 1, "docs": [tair_doc]},
            ),
            mock.patch.object(
                MODULE,
                "plant_details",
                return_value={"status": "not_found", "message": "no edges"},
            ),
        ):
            result = MODULE.build_result(
                "AT1G00010",
                mode="full",
                forced_gene_id=None,
                max_candidates=10,
                max_entities=1,
                max_edges=50,
                snippets=0,
                timeout=5,
                retries=0,
            )
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["message"], "no edges")

    def test_full_query_shares_one_deadline_policy_across_sources(self) -> None:
        tair_doc = {"gene_name": ["AT1G00010"], "description": ["test"]}
        seen_policies = []

        def fake_tair(*args, **kwargs):
            seen_policies.append(kwargs["policy"])
            return {"total": 1, "docs": [tair_doc]}

        def fake_plant(*args, **kwargs):
            seen_policies.append(kwargs["policy"])
            return {"status": "ok", "entities": []}

        with (
            mock.patch.object(MODULE, "tair_search", side_effect=fake_tair),
            mock.patch.object(MODULE, "plant_details", side_effect=fake_plant),
        ):
            result = MODULE.build_result(
                "AT1G00010",
                mode="full",
                forced_gene_id=None,
                max_candidates=10,
                max_entities=1,
                max_edges=50,
                snippets=0,
                timeout=5,
                retries=3,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(seen_policies), 2)
        self.assertIs(seen_policies[0], seen_policies[1])

    def test_cli_uses_nonzero_codes_for_ambiguous_and_not_found(self) -> None:
        for status, expected_code in (("ambiguous", 2), ("not_found", 3)):
            with self.subTest(status=status):
                with (
                    mock.patch.object(
                        MODULE, "build_result", return_value={"status": status}
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    code = MODULE.main(["tair", "query"])
                self.assertEqual(code, expected_code)

    def test_cli_defaults_to_three_retries_after_initial_attempt(self) -> None:
        with (
            mock.patch.object(
                MODULE, "build_result", return_value={"status": "ok"}
            ) as build,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(MODULE.main(["tair", "AT1G00010"]), 0)
        self.assertEqual(build.call_args.kwargs["retries"], 3)

    def test_cli_serializes_parse_errors_and_exits_one(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(
                MODULE,
                "build_result",
                side_effect=MODULE.ResponseParseError("bad preview"),
            ),
            contextlib.redirect_stdout(output),
        ):
            code = MODULE.main(["full", "AT1G00010"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import csv
import json
import math
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from gene_function_prediction.run_pipeline import (
    EVIDENCE_SCHEMA,
    Homolog,
    PREDICTION_SCHEMA,
    Pipeline,
    SourceError,
    _dedupe_rag_results,
    build_parser,
    compact_arabidopsis,
    extract_gene_names,
    fetch_ricedata,
    load_homologs,
    load_maize_index,
    read_gene_list,
    rice_gene_symbols,
    run_json_command,
    summarize_expression,
    validate_arabidopsis_source,
    validate_arabidopsis_gene_names,
    validate_evidence_summary,
    validate_prediction,
    write_outputs,
)


class PipelineTests(unittest.TestCase):
    def test_pubmed_limit_defaults_to_twenty(self):
        args = build_parser().parse_args(["--genes", "genes.txt"])
        self.assertEqual(args.pubmed_limit, 20)
        self.assertEqual(args.source_retries, 3)
        self.assertEqual(args.plantconnectome_max_entities, 10)
        self.assertEqual(args.plantconnectome_max_edges, 5)
        self.assertFalse(hasattr(args, "plantconnectome_llm_edges"))

    def test_json_command_can_accept_explicit_not_found_exit_code(self):
        completed = SimpleNamespace(
            returncode=3,
            stdout=json.dumps({"status": "not_found"}),
            stderr="",
        )
        with patch(
            "gene_function_prediction.run_pipeline.subprocess.run",
            return_value=completed,
        ):
            result = run_json_command(
                ["python3", "query.py", "plant"],
                timeout=1,
                attempts=1,
                accepted_returncodes={0, 3},
            )
        self.assertEqual(result["status"], "not_found")

    def test_gene_ids_are_kept_as_provided(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "genes.txt"
            path.write_text(
                "gene_id\nDM8.2_chr07G12120.14\nSoltu.DM.07G12120\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_gene_list(path),
                ["DM8.2_chr07G12120.14", "Soltu.DM.07G12120"],
            )

    def test_gene_name_split_suffix_removal_and_deduplication(self):
        self.assertEqual(
            extract_gene_names("WRKY1-a, wrky1; PYL8/PYL8-b | ABI5"),
            ["WRKY1", "PYL8", "ABI5"],
        )

    def test_homolog_gene_id_is_kept_as_provided(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "homolog.tsv"
            path.write_text(
                "# comment\nPotato_gene\tTarget_gene\tEvidence_level\n"
                "DM8.2_chr01G00020.3\tTARGET1\tL2\n",
                encoding="utf-8",
            )
            result = load_homologs(path, "Target_gene")
            self.assertEqual(result["DM8.2_chr01G00020.3"].target_gene_id, "TARGET1")
            self.assertEqual(result["DM8.2_chr01G00020.3"].level, "L2")

    def test_maize_column_mapping(self):
        fields = ["x"] * 14
        fields[1] = "Zm00001eb000080"
        fields[10] = "gbss2"
        fields[11] = "granule-bound starch synthase2"
        fields[12] = "starch glucosyltransferase"
        fields[13] = "GO:0019252=starch biosynthetic process"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maize.tsv"
            path.write_text("\t".join(fields) + "\n", encoding="utf-8")
            result = load_maize_index(path)["Zm00001eb000080"]
            self.assertEqual(result["gene_name"], "gbss2")
            self.assertEqual(result["description"], "starch glucosyltransferase")

    def test_rice_gene_symbols_deduplicate_all_exact_matches(self):
        self.assertEqual(
            rice_gene_symbols(
                {
                    "exact_matches": [
                        {"基因符号": "OsWRKY16", "MSU_Locus或其它": "LOC_Os01g47560"},
                        {"基因符号": "oswrky16", "MSU_Locus或其它": "LOC_Os01g47560"},
                        {"Gene Symbol": "OsWRKY72", "MSU_Locus或其它": "LOC_Os01g47560"},
                        {"基因符号": "", "MSU_Locus或其它": "LOC_Os01g47560"},
                    ]
                }
            ),
            ["OsWRKY16", "OsWRKY72"],
        )

    def test_fetch_ricedata_keeps_multiple_exact_matches(self):
        import pandas as pd

        rows = [
            {
                "GeneID": str(index),
                "基因名称或注释": "WRKY transcription factor",
                "基因符号": "OsWRKY16",
                "RAP_Locus": f"Os01g0665{index}00",
                "MSU_Locus或其它": "LOC_Os01g47560",
            }
            for index in range(6)
        ]
        unrelated = {
            "GeneID": "other",
            "基因名称或注释": "unrelated",
            "基因符号": "WrongSymbol",
            "RAP_Locus": "Os01g0000000",
            "MSU_Locus或其它": "LOC_Os01g00000",
        }
        rows.append(unrelated)
        response = Mock()
        response.read.return_value = b"<html></html>"
        context = MagicMock()
        context.__enter__.return_value = response
        with patch(
            "gene_function_prediction.run_pipeline.urllib.request.urlopen",
            return_value=context,
        ), patch("pandas.read_html", return_value=[pd.DataFrame(rows)]):
            result = fetch_ricedata("LOC_Os01g47560", 1)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(len(result["exact_matches"]), 6)
        self.assertNotIn(unrelated, result["exact_matches"])
        self.assertNotIn("selected", result)

    def test_fetch_ricedata_retries_three_failures_then_succeeds_by_default(self):
        import pandas as pd

        row = {
            "GeneID": "1",
            "基因名称或注释": "WRKY transcription factor",
            "基因符号": "OsWRKY16",
            "RAP_Locus": "Os01g0665500",
            "MSU_Locus或其它": "LOC_Os01g47560",
        }
        response = Mock()
        response.read.return_value = b"<html></html>"
        context = MagicMock()
        context.__enter__.return_value = response
        with patch(
            "gene_function_prediction.run_pipeline.urllib.request.urlopen",
            side_effect=[
                TimeoutError("first failure"),
                ConnectionError("second failure"),
                OSError("third failure"),
                context,
            ],
        ) as urlopen, patch(
            "gene_function_prediction.run_pipeline.time.sleep"
        ) as sleep, patch(
            "pandas.read_html", return_value=[pd.DataFrame([row])]
        ):
            result = fetch_ricedata("LOC_Os01g47560", 7)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(
            [request.kwargs["timeout"] for request in urlopen.call_args_list],
            [7, 7, 7, 7],
        )
        self.assertEqual(
            [request.args[0] for request in sleep.call_args_list],
            [1.0, 2.0, 3.0],
        )

    def test_process_rice_searches_each_deduplicated_symbol(self):
        target = "LOC_Os01g47560"
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.args = SimpleNamespace(source_timeout=1, source_retries=3)
        pipeline.source_call = Mock(
            return_value={
                "status": "matched",
                "exact_matches": [
                    {"基因符号": "OsWRKY16", "MSU_Locus或其它": target},
                    {"基因符号": "OsWRKY16", "MSU_Locus或其它": target},
                    {"基因符号": "OsWRKY72", "MSU_Locus或其它": target},
                ],
                "candidates": [],
            }
        )
        pipeline.pubmed = Mock(side_effect=lambda query: {"query": query, "papers": []})
        pipeline._species_summary = Mock(return_value={"status": "ok"})

        record = pipeline.process_rice(target)

        self.assertEqual(record["status"], "ok")
        self.assertEqual(
            [call.args[0] for call in pipeline.pubmed.call_args_list],
            [
                "(OsWRKY16 OR LOC_Os01g47560) AND (rice OR Oryza sativa)",
                "(OsWRKY72 OR LOC_Os01g47560) AND (rice OR Oryza sativa)",
            ],
        )
        payload = pipeline._species_summary.call_args.args[2]
        self.assertEqual(payload["official_gene_names"], ["OsWRKY16", "OsWRKY72"])
        self.assertEqual(
            [result["gene_symbol"] for result in payload["pubmed"]],
            ["OsWRKY16", "OsWRKY72"],
        )
        rice_source_call = pipeline.source_call.call_args
        self.assertEqual(rice_source_call.args[2]["retries"], 3)
        with patch(
            "gene_function_prediction.run_pipeline.fetch_ricedata",
            return_value={},
        ) as fetch:
            rice_source_call.args[3]()
        fetch.assert_called_once_with(target, 1, 3)

    def test_process_rice_skips_pubmed_without_symbols_or_exact_match(self):
        target = "LOC_Os02g56250"
        for database in (
            {
                "status": "matched",
                "exact_matches": [
                    {"基因符号": "", "MSU_Locus或其它": target},
                    {"基因符号": "", "MSU_Locus或其它": target},
                ],
                "candidates": [],
            },
            {
                "status": "not_found",
                "exact_matches": [],
                "candidates": [{"基因符号": "WrongSymbol"}],
            },
        ):
            with self.subTest(status=database["status"]):
                pipeline = Pipeline.__new__(Pipeline)
                pipeline.args = SimpleNamespace(source_timeout=1, source_retries=3)
                pipeline.source_call = Mock(return_value=database)
                pipeline.pubmed = Mock(
                    side_effect=AssertionError("PubMed must not be queried")
                )
                pipeline._species_summary = Mock(return_value={"status": "ok"})

                record = pipeline.process_rice(target)

                self.assertEqual(record["status"], "ok")
                pipeline.pubmed.assert_not_called()
                payload = pipeline._species_summary.call_args.args[2]
                self.assertEqual(payload["official_gene_names"], [])
                self.assertEqual(payload["pubmed"], [])

    def test_expression_mean_and_sample_sd_use_source_means(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.tsv"
            metadata.write_text(
                "sample_column\tsample_name\ttissue\n"
                "A_1\tA\tleaf\nA_2\tA\tleaf\nB_1\tB\tleaf\nC_1\tC\troot\n",
                encoding="utf-8",
            )
            matrix = root / "matrix.tsv"
            matrix.write_text(
                "transcript_id\tgene_id\tgene_name\tA_1\tA_2\tB_1\tC_1\n"
                "DM8.2_chr01G00020.1\tDM8.2_chr01G00020\t\t1\t3\t5\t9\n",
                encoding="utf-8",
            )
            result = summarize_expression(matrix, metadata, ["DM8.2_chr01G00020"])
            by_tissue = {row["tissue"]: row for row in result["DM8.2_chr01G00020"]}
            self.assertEqual(by_tissue["leaf"]["mean_tpm"], 3.5)
            self.assertTrue(math.isclose(by_tissue["leaf"]["sd_tpm"], 2.12132, rel_tol=1e-5))
            self.assertEqual(by_tissue["leaf"]["n_sources"], 2)
            self.assertEqual(by_tissue["root"]["sd_tpm"], 0.0)

            transcript_result = summarize_expression(
                matrix,
                metadata,
                ["DM8.2_chr01G00020.1"],
            )
            self.assertEqual(
                transcript_result["DM8.2_chr01G00020.1"],
                result["DM8.2_chr01G00020"],
            )

    def test_rag_deduplication_keeps_query_names(self):
        rows = [
            {"query_name": "A", "score": 0.8, "text": "same text", "title": "t", "doi": "d"},
            {"query_name": "B", "score": 0.7, "text": "Same  text", "title": "t", "doi": "d"},
        ]
        result = _dedupe_rag_results(rows, 10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["query_names"], ["A", "B"])

    def test_evidence_summary_omits_unused_model_fields(self):
        citations = [f"PMID:{index}" for index in range(13)] + ["PMID:0"]
        data = {
            "source_species": "Arabidopsis thaliana",
            "query_gene": "AT1G01010",
            "resolved_gene_name": "  <b>TEST1</b>\n" + ("n" * 350) + "  ",
            "function_summary": "  <b>Supported function.</b>\n" + ("x" * 1300) + "  ",
            "citations": citations,
        }
        result = validate_evidence_summary(
            data,
            "AT1G01010",
            {citation.casefold() for citation in citations},
        )
        self.assertEqual(result, data)
        self.assertNotIn("identity_status", EVIDENCE_SCHEMA["properties"])
        self.assertNotIn("identity_status", result)
        self.assertNotIn("limitations", EVIDENCE_SCHEMA["properties"])
        self.assertNotIn("limitations", result)

    def test_prediction_uses_one_integrated_function_field(self):
        predicted_function = (
            "  Literature-supported summary: <b>A homolog has a supported function.</b>\n"
            "Functional inference: The potato gene may have a related function. "
            + ("x" * 1300)
            + "  "
        )
        grade_reason = "  <b>Reason</b>\n" + ("y" * 1000) + "  "
        data = {
            "potato_gene_id": "DM8.2_chr01G00020.1",
            "predicted_function": predicted_function,
            "reliability_grade": "F3",
            "grade_reason": grade_reason,
        }
        result = validate_prediction(data, "DM8.2_chr01G00020.1")
        self.assertEqual(result, data)
        self.assertNotIn("known_potato_function", PREDICTION_SCHEMA["properties"])
        self.assertNotIn("known_potato_function", result)
        self.assertNotIn("limitations", PREDICTION_SCHEMA["properties"])
        self.assertNotIn("limitations", result)
        for field in ("supporting_evidence", "citations", "expression_context"):
            self.assertNotIn(field, PREDICTION_SCHEMA["properties"])
            self.assertNotIn(field, result)

    def test_tsv_contains_only_compact_prediction_fields(self):
        gene = "DM8.2_chr01G00020.1"
        records = {
            gene: {
                "status": "ok",
                "potato_gene_id": gene,
                "evidence": {
                    "potato_gene_names": ["TEST1", "ALIAS1"],
                    "homolog_function_evidence": {
                        "arabidopsis": {"target_gene_id": "AT1G01010"},
                    },
                    "expression_by_tissue": [
                        {"tissue": "leaf", "mean_tpm": 1.0, "sd_tpm": 0.2, "n_sources": 2},
                    ],
                },
                "prediction": {
                    "potato_gene_id": gene,
                    "predicted_function": "Integrated prediction.",
                    "reliability_grade": "F3",
                    "grade_reason": "Supported by homolog evidence.",
                    "supporting_evidence": ["legacy field"],
                    "citations": ["PMID:1"],
                    "expression_context": "legacy field",
                },
            }
        }
        expected_fields = [
            "potato_gene_id",
            "gene_names",
            "predicted_function",
            "reliability_grade",
            "grade_reason",
        ]
        with tempfile.TemporaryDirectory() as directory:
            jsonl_path, tsv_path = write_outputs(Path(directory), [gene], records)
            with tsv_path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                self.assertEqual(reader.fieldnames, expected_fields)
                self.assertEqual(
                    list(reader),
                    [{
                        "potato_gene_id": gene,
                        "gene_names": "TEST1;ALIAS1",
                        "predicted_function": "Integrated prediction.",
                        "reliability_grade": "F3",
                        "grade_reason": "Supported by homolog evidence.",
                    }],
                )
            jsonl_record = json.loads(jsonl_path.read_text(encoding="utf-8"))
            self.assertIn("homolog_function_evidence", jsonl_record["evidence"])
            self.assertIn("expression_by_tissue", jsonl_record["evidence"])

    def test_outputs_exclude_blocked_genes(self):
        completed = "DM8.2_chr01G00020.1"
        blocked = "DM8.2_chr01G00030.1"
        records = {
            completed: {
                "status": "ok",
                "evidence": {"potato_gene_names": ["TEST1"]},
                "prediction": {
                    "predicted_function": "Function.",
                    "reliability_grade": "F3",
                    "grade_reason": "Reason.",
                },
            },
            blocked: {
                "status": "blocked",
                "blocking_errors": [{"stage": "source:arabidopsis", "error": "timeout"}],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            jsonl_path, tsv_path = write_outputs(
                Path(directory), [completed, blocked], records
            )
            jsonl_rows = [
                json.loads(line)
                for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(jsonl_rows), 1)
            self.assertEqual(jsonl_rows[0]["status"], "ok")
            with tsv_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual([row["potato_gene_id"] for row in rows], [completed])

    def test_final_prediction_is_blocked_before_llm_when_evidence_failed(self):
        gene = "DM8.2_chr01G00020.1"
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.homologs = {
            "arabidopsis": {gene: Homolog("AT1G01010", "L1")},
            "rice": {},
            "maize": {},
        }
        pipeline.llm_call = Mock(side_effect=AssertionError("LLM must not be called"))
        with tempfile.TemporaryDirectory() as directory:
            pipeline.result_dir = Path(directory)
            record = pipeline.process_final(
                gene,
                {
                    "status": "ok",
                    "gene_names": ["TEST1"],
                    "summary": {"function_summary": "Potato evidence."},
                },
                {
                    ("arabidopsis", "AT1G01010"): {
                        "status": "failed",
                        "blocking_errors": [
                            {
                                "stage": "source:arabidopsis",
                                "key": "AT1G01010",
                                "error": "timeout",
                            }
                        ],
                    }
                },
                [],
            )
        self.assertEqual(record["status"], "blocked")
        self.assertNotIn("prediction", record)
        pipeline.llm_call.assert_not_called()

    def test_species_llm_failure_creates_failed_record_without_summary(self):
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.llm_call = Mock(side_effect=RuntimeError("provider unavailable"))
        pipeline.record_error = Mock()
        with tempfile.TemporaryDirectory() as directory:
            pipeline.evidence_dir = Path(directory)
            record = pipeline._species_summary(
                "arabidopsis",
                "AT1G01010",
                {"query_gene": "AT1G01010"},
            )
        self.assertEqual(record["status"], "failed")
        self.assertNotIn("summary", record)
        self.assertEqual(record["blocking_errors"][0]["stage"], "llm:arabidopsis")

    def test_final_llm_failure_does_not_create_fallback_prediction(self):
        gene = "DM8.2_chr01G00020.1"
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.homologs = {"arabidopsis": {}, "rice": {}, "maize": {}}
        pipeline.llm_call = Mock(side_effect=RuntimeError("provider unavailable"))
        pipeline.record_error = Mock()
        with tempfile.TemporaryDirectory() as directory:
            pipeline.result_dir = Path(directory)
            record = pipeline.process_final(
                gene,
                {
                    "status": "ok",
                    "gene_names": ["TEST1"],
                    "summary": {"function_summary": "Potato evidence."},
                },
                {},
                [],
            )
        self.assertEqual(record["status"], "failed")
        self.assertNotIn("prediction", record)
        self.assertEqual(record["blocking_errors"][0]["stage"], "llm:final")

    def test_pubmed_failure_blocks_species_summary_llm(self):
        target = "Zm00001eb000080"
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.maize_index = {target: {"gene_id": target, "gene_name": "TEST1"}}
        pipeline.pubmed = Mock(side_effect=SourceError("PubMed unavailable"))
        pipeline._species_summary = Mock(
            side_effect=AssertionError("species LLM must not be called")
        )
        pipeline.record_error = Mock()
        with tempfile.TemporaryDirectory() as directory:
            pipeline.evidence_dir = Path(directory)
            record = pipeline.process_maize(target)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["blocking_errors"][0]["stage"], "source:pubmed_maize")
        pipeline._species_summary.assert_not_called()

    @staticmethod
    def _arabidopsis_tair_result(target, names):
        return {
            "status": "ok",
            "tair": {
                "selected": {
                    "gene_id": target,
                    "other_names": names,
                },
                "exact_candidates": [],
            },
        }

    @staticmethod
    def _arabidopsis_plant_result(name, *, status="ok"):
        entities = []
        row_count = 0
        if status == "ok":
            row_count = 1
            entities = [
                {
                    "entity": name,
                    "entity_type": "gene name",
                    "url": f"https://example.test/{name}",
                    "edges": [
                        {
                            "id": name,
                            "target": f"{name}-response",
                            "species": "Arabidopsis thaliana",
                        }
                    ],
                }
            ]
        return {
            "status": status,
            "plantconnectome": {
                "status": status,
                "gene_id": name,
                "preview": {"row_count": row_count},
                "entities": entities,
            },
        }

    @staticmethod
    def _passthrough_source_call(_stage, _key, _specification, producer, validator=None):
        result = producer()
        if validator is not None:
            validator(result)
        return result

    @staticmethod
    def _arabidopsis_pipeline(script):
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.args = SimpleNamespace(
            source_timeout=1,
            source_retries=0,
            plantconnectome_max_entities=10,
            plantconnectome_max_edges=5,
            arabidopsis_script=script,
            force_sources=False,
        )
        pipeline._script_hashes = {str(script): "script-hash"}
        pipeline.source_call = Mock(side_effect=PipelineTests._passthrough_source_call)
        pipeline.llm_call = Mock(
            side_effect=AssertionError("gene-name filter LLM must not be called")
        )
        pipeline.pubmed = Mock(side_effect=AssertionError("PubMed must not be queried"))
        pipeline._species_summary = Mock(return_value={"status": "ok"})
        pipeline.record_error = Mock()
        return pipeline

    def test_arabidopsis_zero_names_skips_filter_plant_and_pubmed(self):
        target = "AT1G01010"
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = self._arabidopsis_pipeline(script)
        commands = []

        def command_result(command, **_kwargs):
            commands.append((command[2], command[3]))
            self.assertEqual(command[2], "tair")
            return self._arabidopsis_tair_result(target, [])

        with patch(
            "gene_function_prediction.run_pipeline.run_json_command",
            side_effect=command_result,
        ):
            record = pipeline.process_arabidopsis(target)

        self.assertEqual(record["status"], "ok")
        self.assertEqual(commands, [("tair", target)])
        pipeline.llm_call.assert_not_called()
        pipeline.pubmed.assert_not_called()
        payload = pipeline._species_summary.call_args.args[2]
        self.assertEqual(payload["retrieval_gene_names"], [])
        self.assertEqual(payload["database_evidence"]["plantconnectome_searches"], [])
        self.assertEqual(payload["pubmed"], [])

    def test_arabidopsis_one_name_skips_filter_and_queries_both_sources(self):
        target = "AT4G25480"
        name = "CBF3"
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = self._arabidopsis_pipeline(script)
        events = []

        def command_result(command, **_kwargs):
            mode, query_name = command[2], command[3]
            if mode == "tair":
                return self._arabidopsis_tair_result(target, [name])
            self.assertEqual(
                command[command.index("--max-entities") + 1],
                "10",
            )
            self.assertEqual(command[command.index("--max-edges") + 1], "5")
            events.append(f"plant:{query_name}")
            return self._arabidopsis_plant_result(query_name)

        def pubmed_result(query):
            events.append(f"pubmed:{name}")
            return {"query": query, "papers": []}

        pipeline.pubmed = Mock(side_effect=pubmed_result)
        with patch(
            "gene_function_prediction.run_pipeline.run_json_command",
            side_effect=command_result,
        ):
            record = pipeline.process_arabidopsis(target)

        self.assertEqual(record["status"], "ok")
        self.assertEqual(events, ["plant:CBF3", "pubmed:CBF3"])
        pipeline.llm_call.assert_not_called()
        self.assertEqual(
            pipeline.pubmed.call_args.args[0],
            "(CBF3 OR AT4G25480) AND (Arabidopsis thaliana)",
        )
        payload = pipeline._species_summary.call_args.args[2]
        self.assertEqual(payload["retrieval_gene_names"], ["CBF3"])
        searches = payload["database_evidence"]["plantconnectome_searches"]
        self.assertEqual([item["gene_name"] for item in searches], ["CBF3"])
        self.assertEqual([item["gene_name"] for item in payload["pubmed"]], ["CBF3"])

    def test_arabidopsis_filter_drives_sequential_queries_for_each_name(self):
        target = "AT4G25480"
        candidates = [
            "ATCBF3",
            "C-REPEAT BINDING FACTOR 3",
            "CBF3",
            "DEHYDRATION RESPONSE ELEMENT B1A",
            "DREB1A",
        ]
        selected_names = ["CBF3", "DREB1A"]
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = self._arabidopsis_pipeline(script)
        events = []

        def command_result(command, **_kwargs):
            mode, query_name = command[2], command[3]
            if mode == "tair":
                return self._arabidopsis_tair_result(target, candidates)
            events.append(f"plant:{query_name}")
            return self._arabidopsis_plant_result(query_name)

        def filter_names(stage, key, payload, prompt_name, schema_name, _schema, validator):
            events.append("filter")
            self.assertEqual(stage, "arabidopsis_gene_names")
            self.assertEqual(key, target)
            self.assertEqual(prompt_name, "arabidopsis_gene_names")
            self.assertEqual(schema_name, "arabidopsis_gene_name_filter")
            self.assertIn(candidates, payload.values())
            return validator({"gene_names": selected_names})

        def pubmed_result(query):
            name = next(
                candidate
                for candidate in selected_names
                if query.startswith(f"({candidate} OR ")
            )
            events.append(f"pubmed:{name}")
            return {"query": query, "papers": []}

        pipeline.llm_call = Mock(side_effect=filter_names)
        pipeline.pubmed = Mock(side_effect=pubmed_result)
        with patch(
            "gene_function_prediction.run_pipeline.run_json_command",
            side_effect=command_result,
        ):
            record = pipeline.process_arabidopsis(target)

        self.assertEqual(record["status"], "ok")
        self.assertEqual(
            events,
            [
                "filter",
                "plant:CBF3",
                "pubmed:CBF3",
                "plant:DREB1A",
                "pubmed:DREB1A",
            ],
        )
        self.assertEqual(
            [call.args[0] for call in pipeline.pubmed.call_args_list],
            [
                "(CBF3 OR AT4G25480) AND (Arabidopsis thaliana)",
                "(DREB1A OR AT4G25480) AND (Arabidopsis thaliana)",
            ],
        )
        payload = pipeline._species_summary.call_args.args[2]
        self.assertEqual(payload["retrieval_gene_names"], selected_names)
        searches = payload["database_evidence"]["plantconnectome_searches"]
        self.assertEqual([item["gene_name"] for item in searches], selected_names)
        self.assertEqual(
            [item["relationships"][0]["entity_2"] for item in searches],
            ["CBF3-response", "DREB1A-response"],
        )
        self.assertEqual(
            [item["gene_name"] for item in payload["pubmed"]], selected_names
        )

    def test_arabidopsis_empty_filter_result_skips_plant_and_pubmed(self):
        target = "AT4G25480"
        candidates = ["ATCBF3", "CBF3"]
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = self._arabidopsis_pipeline(script)
        commands = []

        def command_result(command, **_kwargs):
            commands.append((command[2], command[3]))
            self.assertEqual(command[2], "tair")
            return self._arabidopsis_tair_result(target, candidates)

        def filter_names(*args, **kwargs):
            validator = args[6] if len(args) > 6 else kwargs["validator"]
            return validator({"gene_names": []})

        pipeline.llm_call = Mock(side_effect=filter_names)
        with patch(
            "gene_function_prediction.run_pipeline.run_json_command",
            side_effect=command_result,
        ):
            record = pipeline.process_arabidopsis(target)

        self.assertEqual(record["status"], "ok")
        self.assertEqual(commands, [("tair", target)])
        pipeline.llm_call.assert_called_once()
        pipeline.pubmed.assert_not_called()
        payload = pipeline._species_summary.call_args.args[2]
        self.assertEqual(payload["retrieval_gene_names"], [])
        self.assertEqual(payload["database_evidence"]["plantconnectome_searches"], [])
        self.assertEqual(payload["pubmed"], [])

    def test_arabidopsis_gene_name_filter_rejects_names_outside_input(self):
        candidates = ["ATCBF3", "CBF3", "DREB1A"]
        with self.assertRaises(ValueError):
            validate_arabidopsis_gene_names(
                {"gene_names": ["CBF3", "NOT_FROM_TAIR"]}, candidates
            )

    def test_arabidopsis_gene_name_filter_failure_stops_downstream_queries(self):
        target = "AT4G25480"
        candidates = ["ATCBF3", "CBF3"]
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = self._arabidopsis_pipeline(script)

        def command_result(command, **_kwargs):
            self.assertEqual(command[2], "tair")
            return self._arabidopsis_tair_result(target, candidates)

        pipeline.llm_call = Mock(side_effect=RuntimeError("provider unavailable"))
        pipeline._species_summary = Mock(
            side_effect=AssertionError("species summary must not be called")
        )
        with tempfile.TemporaryDirectory() as directory:
            pipeline.evidence_dir = Path(directory)
            with patch(
                "gene_function_prediction.run_pipeline.run_json_command",
                side_effect=command_result,
            ):
                record = pipeline.process_arabidopsis(target)

        self.assertEqual(record["status"], "failed")
        self.assertEqual(
            record["blocking_errors"][0]["stage"], "llm:arabidopsis_gene_names"
        )
        pipeline.pubmed.assert_not_called()
        pipeline._species_summary.assert_not_called()

    def test_arabidopsis_plant_not_found_still_queries_pubmed(self):
        target = "AT4G25480"
        name = "CBF3"
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = self._arabidopsis_pipeline(script)
        events = []

        def command_result(command, **_kwargs):
            mode, query_name = command[2], command[3]
            if mode == "tair":
                return self._arabidopsis_tair_result(target, [name])
            events.append(f"plant:{query_name}")
            return self._arabidopsis_plant_result(query_name, status="not_found")

        def pubmed_result(query):
            events.append(f"pubmed:{name}")
            return {"query": query, "papers": []}

        pipeline.pubmed = Mock(side_effect=pubmed_result)
        with patch(
            "gene_function_prediction.run_pipeline.run_json_command",
            side_effect=command_result,
        ):
            record = pipeline.process_arabidopsis(target)

        self.assertEqual(record["status"], "ok")
        self.assertEqual(events, ["plant:CBF3", "pubmed:CBF3"])
        payload = pipeline._species_summary.call_args.args[2]
        search = payload["database_evidence"]["plantconnectome_searches"][0]
        self.assertEqual(search["gene_name"], name)
        self.assertEqual(search["relationships"], [])

    def test_arabidopsis_pubmed_failure_records_failed_query_name(self):
        target = "AT4G25480"
        name = "CBF3"
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = self._arabidopsis_pipeline(script)

        def command_result(command, **_kwargs):
            if command[2] == "tair":
                return self._arabidopsis_tair_result(target, [name])
            return self._arabidopsis_plant_result(command[3])

        pipeline.pubmed = Mock(side_effect=SourceError("PubMed unavailable"))
        pipeline._species_summary = Mock(
            side_effect=AssertionError("species summary must not be called")
        )
        with tempfile.TemporaryDirectory() as directory:
            pipeline.evidence_dir = Path(directory)
            with patch(
                "gene_function_prediction.run_pipeline.run_json_command",
                side_effect=command_result,
            ):
                record = pipeline.process_arabidopsis(target)

        self.assertEqual(record["status"], "failed")
        self.assertEqual(
            record["blocking_errors"][0]["stage"], "source:pubmed_arabidopsis"
        )
        self.assertEqual(record["input"]["failed_query_gene_name"], name)
        pipeline._species_summary.assert_not_called()

    def test_arabidopsis_source_accepts_complete_empty_graph(self):
        validate_arabidopsis_source(
            {
                "status": "ok",
                "tair": {"selected": {"gene_id": "AT1G01010"}},
                "plantconnectome_searches": [],
            },
            "AT1G01010",
        )

    def test_compact_arabidopsis_keeps_all_edges_as_four_field_relations(self):
        def entities(gene_name):
            return [
                {
                    "entity": f"{gene_name}-entity-{entity_index}",
                    "entity_type": "gene name",
                    "url": "https://example.test/entity",
                    "edges": [
                        {
                            "id": f"{gene_name}-source-{entity_index}-{edge_index}",
                            "entity1": f"{gene_name}-entity1-{entity_index}-{edge_index}",
                            "target": f"{gene_name}-target-{entity_index}-{edge_index}",
                            "entity2": f"{gene_name}-entity2-{entity_index}-{edge_index}",
                            "inter_type": "regulates",
                            "edge_disamb": "fallback relationship",
                            "publication": f"PMID:{entity_index}{edge_index}",
                            "species": (
                                "Zea mays"
                                if entity_index == 0 and edge_index == 0
                                else "Arabidopsis thaliana"
                            ),
                            "basis": "must not be sent to the LLM",
                            "source_extracted_definition": "must be omitted",
                            "target_extracted_definition": "must be omitted",
                        }
                        for edge_index in range(5)
                    ],
                }
                for entity_index in range(10)
            ]

        compact = compact_arabidopsis(
            {
                "status": "ok",
                "tair": {"selected": {"gene_id": "AT1G01010"}},
                "plantconnectome_searches": [
                    {
                        "gene_name": "CBF3",
                        "status": "ok",
                        "plantconnectome": {
                            "gene_id": "CBF3",
                            "entities": entities("CBF3"),
                        },
                    },
                    {
                        "gene_name": "DREB1A",
                        "status": "ok",
                        "plantconnectome": {
                            "gene_id": "DREB1A",
                            "entities": entities("DREB1A"),
                        },
                    },
                ],
            }
        )
        searches = compact["plantconnectome_searches"]
        self.assertEqual([item["gene_name"] for item in searches], ["CBF3", "DREB1A"])
        self.assertEqual(len(searches[0]["relationships"]), 50)
        self.assertEqual(len(searches[1]["relationships"]), 50)
        self.assertEqual(sum(len(item["relationships"]) for item in searches), 100)
        self.assertEqual(
            searches[0]["relationships"][0],
            {
                "entity_1": "CBF3-entity1-0-0",
                "relationship": "regulates",
                "entity_2": "CBF3-entity2-0-0",
                "citation": "PMID:00",
            },
        )
        self.assertEqual(
            set(searches[0]["relationships"][0]),
            {"entity_1", "relationship", "entity_2", "citation"},
        )

    def test_arabidopsis_retry_reuses_tair_cache_after_plant_failure(self):
        target = "AT1G01010"
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.args = SimpleNamespace(
            source_timeout=1,
            source_retries=0,
            plantconnectome_max_entities=10,
            plantconnectome_max_edges=5,
            arabidopsis_script=script,
            force_sources=False,
        )
        pipeline._script_hashes = {str(script): "script-hash"}
        pipeline._cache_lock_guard = threading.Lock()
        pipeline._cache_locks = {}
        pipeline.record_error = Mock()
        pipeline.pubmed = Mock(return_value={"query": "q", "papers": []})
        pipeline._species_summary = Mock(return_value={"status": "ok"})
        calls = []

        def source_result(command, **kwargs):
            mode = command[2]
            query_name = command[3]
            calls.append((mode, query_name))
            if mode == "tair":
                return {
                    "status": "ok",
                    "tair": {
                        "selected": {
                            "gene_id": target,
                            "other_names": ["TEST1"],
                        }
                    },
                }
            self.assertEqual(query_name, "TEST1")
            if sum(call_mode == "plant" for call_mode, _name in calls) == 1:
                raise SourceError("temporary PlantConnectome failure")
            return {
                "status": "ok",
                "plantconnectome": {
                    "status": "ok",
                    "gene_id": query_name,
                    "preview": {"row_count": 1},
                    "entities": [
                        {
                            "entity": query_name,
                            "entity_type": "gene name",
                            "edges": [
                                {
                                    "id": query_name,
                                    "target": "cold response",
                                    "species": "Arabidopsis thaliana",
                                }
                            ],
                        }
                    ],
                },
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline.cache_dir = root / "cache"
            pipeline.evidence_dir = root / "evidence"
            with patch(
                "gene_function_prediction.run_pipeline.run_json_command",
                side_effect=source_result,
            ):
                first = pipeline.process_arabidopsis(target)
                second = pipeline.process_arabidopsis(target)

        self.assertEqual(first["status"], "failed")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(
            calls,
            [("tair", target), ("plant", "TEST1"), ("plant", "TEST1")],
        )

    def test_arabidopsis_plant_cache_is_independent_for_each_filtered_name(self):
        target = "AT4G25480"
        candidates = ["ATCBF3", "CBF3", "DREB1A"]
        selected_names = ["CBF3", "DREB1A"]
        script = Path("/tmp/query_arabidopsis_gene_search.py")
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.args = SimpleNamespace(
            source_timeout=1,
            source_retries=0,
            plantconnectome_max_entities=10,
            plantconnectome_max_edges=5,
            arabidopsis_script=script,
            force_sources=False,
        )
        pipeline._script_hashes = {str(script): "script-hash"}
        pipeline._cache_lock_guard = threading.Lock()
        pipeline._cache_locks = {}
        pipeline.record_error = Mock()
        pipeline.pubmed = Mock(
            side_effect=lambda query: {"query": query, "papers": []}
        )
        pipeline._species_summary = Mock(return_value={"status": "ok"})
        pipeline.llm_call = Mock(
            side_effect=lambda *_args: _args[6]({"gene_names": selected_names})
        )
        commands = []

        def source_result(command, **_kwargs):
            mode, query_name = command[2], command[3]
            commands.append((mode, query_name))
            if mode == "tair":
                return self._arabidopsis_tair_result(target, candidates)
            return self._arabidopsis_plant_result(query_name)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline.cache_dir = root / "cache"
            pipeline.evidence_dir = root / "evidence"
            with patch(
                "gene_function_prediction.run_pipeline.run_json_command",
                side_effect=source_result,
            ):
                first = pipeline.process_arabidopsis(target)
                second = pipeline.process_arabidopsis(target)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(
            commands,
            [("tair", target), ("plant", "CBF3"), ("plant", "DREB1A")],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
import urllib.parse
from unittest.mock import patch

from gene_function_prediction import run_pipeline


class PotatoGeneAdapterTests(unittest.TestCase):
    def test_gene_search_truncates_results_but_preserves_total_count(self) -> None:
        response = {
            "total": 3,
            "results": [
                {"gene_id": "DM8C01G00001", "symbol": "GENE1"},
                {"gene_id": "DM8C01G00002", "symbol": "GENE2"},
                {"gene_id": "DM8C01G00003", "symbol": "GENE3"},
            ],
        }
        with patch.object(
            run_pipeline, "_http_json", return_value=response
        ) as request:
            result = run_pipeline.fetch_potato_gene_search(
                "  GENE 1  ",
                base_url="https://potato.example/api-root/",
                max_results=2,
                timeout=5,
                retries=0,
            )

        self.assertEqual(result["endpoint"], "gene_search")
        self.assertEqual(result["query"], "GENE 1")
        self.assertEqual(result["result_count"], 3)
        self.assertEqual(
            [item["symbol"] for item in result["results"]],
            ["GENE1", "GENE2"],
        )
        request_url = request.call_args.args[0]
        parsed = urllib.parse.urlsplit(request_url)
        self.assertEqual(parsed.path, "/api-root/api/gene_search")
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {"q": ["GENE 1"]})

    def test_explicit_null_results_are_rejected(self) -> None:
        with patch.object(
            run_pipeline, "_http_json", return_value={"results": None}
        ):
            with self.assertRaises(run_pipeline.SourceResponseParseError):
                run_pipeline.fetch_potato_gene_search(
                    "GENE1",
                    base_url="https://potato.example",
                    max_results=5,
                    timeout=5,
                    retries=0,
                )


class PotatoRagAdapterTests(unittest.TestCase):
    def test_rag_response_matches_existing_pipeline_wrapper(self) -> None:
        response = {
            "success": True,
            "query": "normalized server query",
            "results": [
                {"title": "First result", "text": "Evidence one"},
                {
                    "rank": 7,
                    "title": "Second result",
                    "text": "Evidence two",
                },
            ],
        }
        with patch.object(
            run_pipeline, "_http_json", return_value=response
        ) as request:
            result = run_pipeline.fetch_potato_rag(
                "  PYL8 potato gene function  ",
                base_url="https://rag.example/",
                top_k_retrieve=20,
                top_k_rerank=5,
                timeout=5,
                retries=0,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["query"], "PYL8 potato gene function")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            result["kg"],
            {"success": None, "skipped": True, "entities": []},
        )
        rag = result["rag"]
        self.assertTrue(rag["success"])
        self.assertEqual(rag["query"], "normalized server query")
        self.assertEqual(rag["top_k_retrieve"], 20)
        self.assertEqual(rag["top_k_rerank"], 5)
        self.assertEqual([item["rank"] for item in rag["results"]], [1, 7])

        self.assertEqual(request.call_args.args[0], "https://rag.example/api/rag/search")
        self.assertEqual(request.call_args.kwargs["method"], "POST")
        self.assertEqual(
            request.call_args.kwargs["payload"],
            {
                "query": "PYL8 potato gene function",
                "top_k_retrieve": 20,
                "top_k_rerank": 5,
            },
        )


class PubmedAdapterTests(unittest.TestCase):
    def test_empty_esearch_result_skips_efetch(self) -> None:
        search_response = {"esearchresult": {"count": "0", "idlist": []}}
        with (
            patch.object(
                run_pipeline, "_http_json", return_value=search_response
            ) as search,
            patch.object(run_pipeline, "_http_text") as fetch,
        ):
            result = run_pipeline.fetch_pubmed(
                "CBF3 Arabidopsis",
                base_url="https://pubmed.example/eutils/",
                limit=20,
                timeout=5,
                retries=0,
            )

        self.assertEqual(result, {"total": 0, "data": []})
        fetch.assert_not_called()
        search_url = search.call_args.args[0]
        parsed = urllib.parse.urlsplit(search_url)
        self.assertEqual(parsed.path, "/eutils/esearch.fcgi")
        self.assertEqual(
            urllib.parse.parse_qs(parsed.query),
            {
                "db": ["pubmed"],
                "term": ["CBF3 Arabidopsis"],
                "retmax": ["20"],
                "retmode": ["json"],
            },
        )

    def test_efetch_xml_is_parsed_into_compatible_paper_records(self) -> None:
        search_response = {
            "esearchresult": {"count": "2", "idlist": ["123", "456"]}
        }
        xml_response = """
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>123</PMID>
              <Article>
                <ArticleTitle>CBF3 controls freezing tolerance</ArticleTitle>
                <Abstract>
                  <AbstractText Label="BACKGROUND">Cold response.</AbstractText>
                  <AbstractText>Functional evidence.</AbstractText>
                </Abstract>
                <AuthorList>
                  <Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author>
                </AuthorList>
                <Journal>
                  <Title>Plant Journal</Title>
                  <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
                </Journal>
              </Article>
            </MedlineCitation>
            <PubmedData>
              <ArticleIdList><ArticleId IdType="doi">10.1000/cbf3</ArticleId></ArticleIdList>
            </PubmedData>
          </PubmedArticle>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>456</PMID>
              <Article>
                <ArticleTitle>DREB1A expression study</ArticleTitle>
                <ELocationID EIdType="doi">10.1000/dreb1a</ELocationID>
                <Journal>
                  <Title>Plant Reports</Title>
                  <JournalIssue><PubDate><MedlineDate>2021 Winter</MedlineDate></PubDate></JournalIssue>
                </Journal>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
        """
        with (
            patch.object(
                run_pipeline, "_http_json", return_value=search_response
            ),
            patch.object(
                run_pipeline,
                "_http_text",
                return_value=(xml_response, "https://pubmed.example/final"),
            ) as fetch,
        ):
            result = run_pipeline.fetch_pubmed(
                "CBF3 OR DREB1A",
                base_url="https://pubmed.example/eutils",
                limit=20,
                timeout=5,
                retries=0,
            )

        self.assertEqual(result["total"], 2)
        first, second = result["data"]
        self.assertEqual(
            first,
            {
                "id": "123",
                "pmid": "123",
                "doi": "10.1000/cbf3",
                "title": "CBF3 controls freezing tolerance",
                "year": 2024,
                "authors": ["Ada Lovelace"],
                "abstract": "BACKGROUND: Cold response. Functional evidence.",
                "venue": "Plant Journal",
                "source": "pubmed",
            },
        )
        self.assertEqual(second["pmid"], "456")
        self.assertEqual(second["doi"], "10.1000/dreb1a")
        self.assertEqual(second["year"], 2021)
        self.assertEqual(second["authors"], [])
        fetch_url = fetch.call_args.args[0]
        parsed = urllib.parse.urlsplit(fetch_url)
        self.assertEqual(parsed.path, "/eutils/efetch.fcgi")
        self.assertEqual(urllib.parse.parse_qs(parsed.query)["id"], ["123,456"])


if __name__ == "__main__":
    unittest.main()

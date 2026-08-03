from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from interface import daily_updates
from interface.daily_updates_job import (
    DailyUpdatesJobError,
    DailyUpdatesWorker,
    LLMCircuitOpenError,
    LLMError,
    LLMOutputError,
    MigrationError,
    ModelProxyLLMClient,
    Paper,
    PubMedClient,
    PubMedFetchResult,
    RetryingHTTPClient,
    STALE_RUN_AFTER,
    migrate_legacy_databases,
    parse_pubmed_xml,
)


PUBMED_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>123</PMID>
      <Article>
        <Journal><JournalIssue><PubDate><Year>2026</Year><Month>Aug</Month><Day>2</Day></PubDate></JournalIssue><Title>Plant Journal</Title></Journal>
        <ArticleTitle>Potato <i>gene</i> study</ArticleTitle>
        <Abstract><AbstractText Label="BACKGROUND">First <b>part</b>.</AbstractText><AbstractText>Second part.</AbstractText></Abstract>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <History><PubMedPubDate PubStatus="pubmed"><Year>2026</Year><Month>8</Month><Day>3</Day></PubMedPubDate></History>
      <ArticleIdList><ArticleId IdType="doi">10.1/potato</ArticleId></ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


def _response(request: httpx.Request, status: int, value, **headers) -> httpx.Response:
    if isinstance(value, (dict, list)):
        return httpx.Response(status, request=request, json=value, headers=headers)
    return httpx.Response(status, request=request, content=value, headers=headers)


def test_pubmed_search_pages_uses_exact_query_and_parses_nested_xml() -> None:
    requests: list[httpx.Request] = []
    clock = [0.0]
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        params = parse_qs(request.url.query.decode())
        if request.url.path.endswith("esearch.fcgi"):
            start = int(params["retstart"][0])
            ids = ["123"] if start == 0 else ["124"]
            return _response(
                request, 200, {"esearchresult": {"count": "2", "idlist": ids}}
            )
        assert params["id"] == ["123,124"]
        xml = PUBMED_XML.replace(b"123", b"124", 1)
        combined = PUBMED_XML.replace(
            b"</PubmedArticleSet>",
            xml.split(b"<PubmedArticleSet>", 1)[1],
        )
        return _response(request, 200, combined)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pubmed = PubMedClient(
        email="test@example.org",
        client=client,
        page_size=1,
        fetch_batch_size=100,
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    result = pubmed.search(date(2026, 8, 1), date(2026, 8, 3))
    papers = result.papers
    assert [paper.pmid for paper in papers] == ["123", "124"]
    assert result.failed_pmids == ()
    assert papers[0].title == "Potato gene study"
    assert papers[0].abstract == "First part. Second part."
    assert papers[0].publication_date == "2026-08-02"
    assert papers[0].pubmed_date == "2026-08-03"
    first_params = parse_qs(requests[0].url.query.decode())
    assert first_params["term"] == ["(potato[Title] OR potato[Abstract])"]
    assert first_params["datetype"] == ["edat"]
    assert sleeps == pytest.approx([1 / 3, 1 / 3])


def test_pubmed_retries_429_and_malformed_xml_is_failure() -> None:
    attempts = 0
    sleeps: list[float] = []
    clock = [0.0]

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _response(request, 429, b"slow", **{"Retry-After": "3"})
        return _response(
            request, 200, {"esearchresult": {"count": "0", "idlist": []}}
        )

    pubmed = PubMedClient(
        email="test@example.org",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    assert pubmed.search_ids(date(2026, 8, 1), date(2026, 8, 2)) == []
    assert attempts == 2
    assert sleeps == [3.0]
    with pytest.raises(Exception):
        parse_pubmed_xml(b"<broken>")


def test_retry_after_http_date_is_honored() -> None:
    attempts = 0
    sleeps: list[float] = []
    now = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _response(
                request,
                429,
                b"slow",
                **{"Retry-After": format_datetime(now + timedelta(seconds=7))},
            )
        return _response(request, 200, {"ok": True})

    client = RetryingHTTPClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=2,
        sleep=sleeps.append,
        now=lambda: now,
    )
    assert client.request("GET", "https://example.test/").status_code == 200
    assert sleeps == [7.0]


@pytest.mark.parametrize(
    ("api_key", "minimum_interval"),
    [(None, 1 / 3), ("ncbi-key", 1 / 10)],
)
def test_pubmed_proactively_limits_successful_requests(
    api_key: str | None,
    minimum_interval: float,
) -> None:
    clock = [0.0]
    request_times: list[float] = []

    def sleep(delay: float) -> None:
        clock[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        request_times.append(clock[0])
        return _response(
            request,
            200,
            {"esearchresult": {"count": "0", "idlist": []}},
        )

    pubmed = PubMedClient(
        email="test@example.org",
        api_key=api_key,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    pubmed.search_ids(date(2026, 8, 1), date(2026, 8, 2))
    pubmed.search_ids(date(2026, 8, 1), date(2026, 8, 2))
    assert request_times == pytest.approx([0.0, minimum_interval])


def test_pubmed_partial_fetch_returns_retryable_pmids() -> None:
    clock = [0.0]

    def sleep(delay: float) -> None:
        clock[0] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("esearch.fcgi"):
            return _response(
                request,
                200,
                {"esearchresult": {"count": "2", "idlist": ["123", "999"]}},
            )
        return _response(request, 200, PUBMED_XML)

    pubmed = PubMedClient(
        email="test@example.org",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    result = pubmed.search(date(2026, 8, 1), date(2026, 8, 3))
    assert [paper.pmid for paper in result.papers] == ["123"]
    assert result.failed_pmids == ("999",)


def test_model_proxy_resolves_only_model_and_validates_strict_json() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["authorization"] == "Bearer service-token"
        if request.url.path.endswith("/models"):
            return _response(request, 200, {"data": [{"id": "primary-route"}]})
        return _response(
            request,
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "is_relevant": True,
                                    "summary": "A potato study. It reports a finding.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    llm = ModelProxyLLMClient(
        base_url="http://proxy/v1",
        token="service-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    relevant, summary = llm.evaluate(
        Paper("123", "Title", "Abstract", "J", "2026", "2026", "")
    )
    assert relevant is True
    assert summary.startswith("A potato")
    assert calls == ["/v1/models", "/v1/chat/completions"]

    def fenced_handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            200,
            {"choices": [{"message": {"content": "```json\n{}\n```"}}]},
        )

    strict = ModelProxyLLMClient(
        base_url="http://proxy/v1",
        token="token",
        model="route",
        client=httpx.Client(transport=httpx.MockTransport(fenced_handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(LLMOutputError):
        strict.evaluate(Paper("123", "T", "A", "", "", "", ""))


class _FakePubMed:
    def __init__(
        self,
        papers: list[Paper],
        *,
        failed_pmids: tuple[str, ...] = (),
        retry_result: PubMedFetchResult | None = None,
    ) -> None:
        self.papers = papers
        self.failed_pmids = failed_pmids
        self.retry_result = retry_result or PubMedFetchResult((), ())
        self.fetch_calls: list[tuple[str, ...]] = []

    def search(self, start_date: date, end_date: date) -> PubMedFetchResult:
        return PubMedFetchResult(tuple(self.papers), self.failed_pmids)

    def fetch_papers(self, pmids: list[str]) -> PubMedFetchResult:
        self.fetch_calls.append(tuple(pmids))
        return self.retry_result


class _TranslationFails:
    def evaluate(self, paper: Paper) -> tuple[bool, str]:
        return True, "English summary. A second sentence."

    def translate(self, title: str, summary: str) -> tuple[str, str]:
        raise LLMOutputError("bad translation")


class _TranslationSucceeds(_TranslationFails):
    def evaluate(self, paper: Paper) -> tuple[bool, str]:
        raise AssertionError("relevance must not be repeated for a published paper")

    def translate(self, title: str, summary: str) -> tuple[str, str]:
        return "中文标题", "中文总结"


class _LLMSucceeds:
    def evaluate(self, paper: Paper) -> tuple[bool, str]:
        return True, f"Summary for {paper.pmid}. A second sentence."

    def translate(self, title: str, summary: str) -> tuple[str, str]:
        return f"中文 {title}", f"中文 {summary}"


class _AlwaysLLMFails:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, paper: Paper) -> tuple[bool, str]:
        self.calls += 1
        raise LLMError("proxy unavailable")

    def translate(self, title: str, summary: str) -> tuple[str, str]:
        raise AssertionError("translation must not run")


def test_worker_publishes_english_on_translation_failure_then_retries(tmp_path) -> None:
    db_path = tmp_path / "daily.sqlite"
    clock = [datetime(2026, 8, 3, 0, 0, tzinfo=UTC)]
    paper = Paper("123", "Title", "Abstract", "J", "2026-08-02", "2026-08-03", "")
    first = DailyUpdatesWorker(
        db_path=db_path,
        pubmed=_FakePubMed([paper]),  # type: ignore[arg-type]
        llm=_TranslationFails(),  # type: ignore[arg-type]
        now=lambda: clock[0],
    ).run(today=date(2026, 8, 3))
    assert first["status"] == "partial"
    payload = daily_updates.list_daily_updates(db_path=db_path)
    assert payload["items"][0]["summary"] == "English summary. A second sentence."
    assert payload["items"][0]["summaryZh"] == ""

    clock[0] += timedelta(hours=2)
    second = DailyUpdatesWorker(
        db_path=db_path,
        pubmed=_FakePubMed([]),  # type: ignore[arg-type]
        llm=_TranslationSucceeds(),  # type: ignore[arg-type]
        now=lambda: clock[0],
    ).run(today=date(2026, 8, 3))
    assert second["status"] == "success"
    assert daily_updates.list_daily_updates(db_path=db_path)["items"][0]["summaryZh"] == "中文总结"


def test_worker_persists_missing_metadata_and_advances_checkpoint(tmp_path) -> None:
    db_path = tmp_path / "daily.sqlite"
    clock = [datetime(2026, 8, 3, 0, 0, tzinfo=UTC)]
    paper = Paper("123", "Title", "Abstract", "J", "2026-08-02", "2026-08-03", "")
    pubmed = _FakePubMed([paper], failed_pmids=("999",))

    report = DailyUpdatesWorker(
        db_path=db_path,
        pubmed=pubmed,  # type: ignore[arg-type]
        llm=_LLMSucceeds(),  # type: ignore[arg-type]
        now=lambda: clock[0],
    ).run(today=date(2026, 8, 3))

    assert report["status"] == "partial"
    assert report["candidateCount"] == 2
    assert report["relevantCount"] == 1
    assert report["failedCount"] == 1
    assert daily_updates.list_daily_updates(db_path=db_path)["items"][0]["pmid"] == "123"
    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            """
            SELECT p.title, s.relevance_status, s.relevance_attempts,
                   s.next_retry_at, s.last_error_category
            FROM papers AS p JOIN processing_state AS s ON s.pmid = p.pmid
            WHERE p.pmid = '999'
            """
        ).fetchone()
        assert state[0:3] == ("", "retry", 1)
        assert state[3] > clock[0].isoformat().replace("+00:00", "Z")
        assert state[4] == "pubmed_metadata"
        assert conn.execute(
            "SELECT checkpoint_date FROM job_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == "2026-08-03"

    immediate = _FakePubMed([])
    second = DailyUpdatesWorker(
        db_path=db_path,
        pubmed=immediate,  # type: ignore[arg-type]
        llm=_LLMSucceeds(),  # type: ignore[arg-type]
        now=lambda: clock[0],
    ).run(today=date(2026, 8, 3))
    assert second["status"] == "success"
    assert immediate.fetch_calls == []

    clock[0] += timedelta(hours=2)
    recovered_paper = Paper(
        "999", "Recovered", "Abstract", "J", "2026-08-02", "2026-08-03", ""
    )
    recovery = _FakePubMed(
        [],
        retry_result=PubMedFetchResult((recovered_paper,), ()),
    )
    third = DailyUpdatesWorker(
        db_path=db_path,
        pubmed=recovery,  # type: ignore[arg-type]
        llm=_LLMSucceeds(),  # type: ignore[arg-type]
        now=lambda: clock[0],
    ).run(today=date(2026, 8, 3))
    assert third["status"] == "success"
    assert recovery.fetch_calls == [("999",)]
    assert {
        item["pmid"] for item in daily_updates.list_daily_updates(db_path=db_path)["items"]
    } == {"123", "999"}


def test_worker_opens_circuit_after_consecutive_llm_failures(tmp_path) -> None:
    db_path = tmp_path / "daily.sqlite"
    clock = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
    papers = [
        Paper(str(pmid), f"Title {pmid}", "Abstract", "J", "2026", "2026", "")
        for pmid in range(1, 6)
    ]
    llm = _AlwaysLLMFails()
    worker = DailyUpdatesWorker(
        db_path=db_path,
        pubmed=_FakePubMed(papers),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        now=lambda: clock,
    )

    with pytest.raises(LLMCircuitOpenError):
        worker.run(today=date(2026, 8, 3))

    assert llm.calls == 3
    with sqlite3.connect(db_path) as conn:
        counts = dict(
            conn.execute(
                "SELECT relevance_status, count(*) FROM processing_state GROUP BY relevance_status"
            )
        )
        assert counts == {"pending": 2, "retry": 3}
        run = conn.execute(
            """
            SELECT status, failed_count, checkpoint_date, error_category
            FROM job_runs ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        assert run == ("failed", 3, None, "llm_circuit_open")


def test_worker_reclaims_runs_older_than_systemd_timeout(tmp_path) -> None:
    db_path = tmp_path / "daily.sqlite"
    daily_updates.initialize_database(db_path)
    clock = datetime(2026, 8, 3, 3, 0, tzinfo=UTC)
    started_at = (clock - STALE_RUN_AFTER - timedelta(minutes=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO job_runs (
                trigger_date, search_start_date, search_end_date,
                status, started_at
            ) VALUES ('2026-08-03', '2026-08-01', '2026-08-03', 'running', ?)
            """,
            (started_at,),
        )
    worker = DailyUpdatesWorker(
        db_path=db_path,
        pubmed=_FakePubMed([]),  # type: ignore[arg-type]
        llm=_LLMSucceeds(),  # type: ignore[arg-type]
        now=lambda: clock,
    )

    new_run_id, _, _ = worker._start_run(date(2026, 8, 3))
    with sqlite3.connect(db_path) as conn:
        old_run = conn.execute(
            "SELECT status, error_category FROM job_runs WHERE id != ?",
            (new_run_id,),
        ).fetchone()
        assert old_run == ("failed", "stale_run")
    with pytest.raises(DailyUpdatesJobError, match="another daily updates job"):
        worker._start_run(date(2026, 8, 3))


def _write_legacy_databases(root: Path) -> tuple[Path, Path, Path]:
    repo = root / "pubmed_repo.db"
    english = root / "pubmed_results.db"
    chinese = root / "pubmed_results_CN.db"
    with sqlite3.connect(repo) as conn:
        conn.execute(
            "CREATE TABLE processed_papers (pmid TEXT PRIMARY KEY, processed_at TEXT, status TEXT)"
        )
        conn.executemany(
            "INSERT INTO processed_papers VALUES (?, '2026-08-03', ?)",
            [("1", "relevant"), ("2", "relevant"), ("3", "irrelevant"), ("4", "llm_failed")],
        )
    columns = "pmid TEXT PRIMARY KEY, title TEXT, journal TEXT, pub_date TEXT, abstract TEXT, summary TEXT, doi TEXT, url TEXT, pubmed_date TEXT"
    with sqlite3.connect(english) as conn:
        conn.execute(f"CREATE TABLE papers ({columns})")
        conn.executemany(
            "INSERT INTO papers VALUES (?, ?, 'J', '2026-08-02', 'A', ?, '', '', '2026-08-03')",
            [("1", "One", "Summary one"), ("2", "Two", "Summary two")],
        )
    with sqlite3.connect(chinese) as conn:
        conn.execute(
            "CREATE TABLE papers (pmid TEXT PRIMARY KEY, title TEXT, chinese_title TEXT, summary TEXT, chinese_summary TEXT)"
        )
        conn.execute("INSERT INTO papers VALUES ('1', 'One', '一', 'Summary one', '总结一')")
    return repo, english, chinese


def test_migration_dry_run_and_apply_preserve_retry_semantics(tmp_path) -> None:
    repo, english, chinese = _write_legacy_databases(tmp_path)
    target = tmp_path / "data" / "daily.sqlite"
    dry_run = migrate_legacy_databases(
        repo_db=repo,
        results_db=english,
        results_cn_db=chinese,
        target=target,
        apply=False,
    )
    assert dry_run["source"]["processed"] == 4
    assert dry_run["source"]["pendingTranslation"] == 1
    assert not target.exists()

    applied = migrate_legacy_databases(
        repo_db=repo,
        results_db=english,
        results_cn_db=chinese,
        target=target,
        apply=True,
    )
    assert applied["integrityCheck"] == "ok"
    assert applied["foreignKeyCheck"] == "ok"
    assert applied["targetCounts"] == {
        "processed": 4,
        "relevant": 2,
        "translated": 1,
        "pendingTranslation": 1,
        "retry": 1,
    }
    with sqlite3.connect(target) as conn:
        states = dict(conn.execute("SELECT pmid, relevance_status FROM processing_state"))
        assert states == {"1": "relevant", "2": "relevant", "3": "irrelevant", "4": "retry"}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.execute(
            """
            UPDATE processing_state SET relevance_status = 'irrelevant',
                translation_status = 'not_applicable', next_retry_at = NULL,
                last_error_category = NULL WHERE pmid = '4'
            """
        )

    repeated = migrate_legacy_databases(
        repo_db=repo,
        results_db=english,
        results_cn_db=chinese,
        target=target,
        apply=True,
    )
    assert repeated["targetCounts"]["retry"] == 0
    with sqlite3.connect(target) as conn:
        assert conn.execute(
            """
            SELECT relevance_status, translation_status, summary_zh,
                   next_retry_at, last_error_category
            FROM processing_state WHERE pmid = '4'
            """
        ).fetchone() == ("irrelevant", "not_applicable", "", None, None)


def test_migration_rejects_foreign_key_corruption(tmp_path) -> None:
    repo, english, chinese = _write_legacy_databases(tmp_path)
    target = tmp_path / "data" / "daily.sqlite"
    daily_updates.initialize_database(target)
    with sqlite3.connect(target) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO processing_state (pmid, relevance_status, updated_at)
            VALUES ('999', 'pending', '2026-08-03T00:00:00Z')
            """
        )

    with pytest.raises(MigrationError, match="foreign key check"):
        migrate_legacy_databases(
            repo_db=repo,
            results_db=english,
            results_cn_db=chinese,
            target=target,
            apply=True,
            backup_root=tmp_path / "backups",
        )

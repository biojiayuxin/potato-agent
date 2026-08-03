from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from interface import app as interface_app_mod
from interface import daily_updates


def _write_public_database(path: Path) -> Path:
    daily_updates.initialize_database(path)
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(path) as conn:
        for index in range(3):
            pmid = str(100 + index)
            pubmed_date = f"2026-08-0{index + 1}"
            conn.execute(
                """
                INSERT INTO papers (
                    pmid, title, title_zh, abstract, journal,
                    publication_date, pubmed_date, doi, url,
                    first_seen_at, updated_at
                ) VALUES (?, ?, ?, '', 'Journal', ?, ?, ?, ?, ?, ?)
                """,
                (
                    pmid,
                    f"Title {pmid}",
                    f"标题 {pmid}" if index != 1 else "",
                    pubmed_date,
                    pubmed_date,
                    f"10.1/{pmid}",
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO processing_state (
                    pmid, relevance_status, summary_en, translation_status,
                    summary_zh, updated_at
                ) VALUES (?, 'relevant', ?, ?, ?, ?)
                """,
                (
                    pmid,
                    f"Summary {pmid}",
                    "complete" if index != 1 else "retry",
                    f"总结 {pmid}" if index != 1 else "",
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO job_runs (
                trigger_date, search_start_date, search_end_date, status,
                started_at, completed_at, checkpoint_date
            ) VALUES ('2026-08-03', '2026-08-01', '2026-08-03',
                      'partial', ?, ?, '2026-08-03')
            """,
            (now, now),
        )
    return path


def test_daily_updates_cursor_api_and_public_fields(monkeypatch, tmp_path) -> None:
    db_path = _write_public_database(tmp_path / "updates.sqlite")
    monkeypatch.setattr(daily_updates, "get_database_path", lambda: db_path)
    client = TestClient(interface_app_mod.app)

    first = client.get("/api/daily-updates", params={"limit": 2})
    assert first.status_code == 200
    payload = first.json()
    assert [item["pmid"] for item in payload["items"]] == ["102", "101"]
    assert set(payload["items"][0]) == {
        "pmid", "title", "titleZh", "summary", "summaryZh", "journal",
        "publicationDate", "pubmedDate", "doi", "url",
    }
    assert payload["items"][1]["titleZh"] == ""
    assert payload["hasMore"] is True
    assert payload["lastRun"]["status"] == "partial"
    assert isinstance(payload["lastRun"]["completedAt"], str)

    second = client.get(
        "/api/daily-updates",
        params={"limit": 2, "cursor": payload["nextCursor"]},
    )
    assert second.status_code == 200
    assert [item["pmid"] for item in second.json()["items"]] == ["100"]
    assert second.json()["hasMore"] is False
    assert second.json()["nextCursor"] is None


def test_daily_updates_rejects_invalid_cursor_and_hides_missing_database(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        daily_updates, "get_database_path", lambda: tmp_path / "missing.sqlite"
    )
    client = TestClient(interface_app_mod.app)
    unavailable = client.get("/api/daily-updates")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": daily_updates.DATABASE_UNAVAILABLE_DETAIL}

    db_path = _write_public_database(tmp_path / "updates.sqlite")
    monkeypatch.setattr(daily_updates, "get_database_path", lambda: db_path)
    invalid = client.get("/api/daily-updates", params={"cursor": "not-base64!"})
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": daily_updates.INVALID_CURSOR_DETAIL}


def test_daily_updates_reports_latest_running_job(monkeypatch, tmp_path) -> None:
    db_path = _write_public_database(tmp_path / "updates.sqlite")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO job_runs (
                trigger_date, search_start_date, search_end_date,
                status, started_at
            ) VALUES ('2026-08-04', '2026-08-02', '2026-08-04',
                      'running', '2026-08-04T00:00:00Z')
            """
        )
    monkeypatch.setattr(daily_updates, "get_database_path", lambda: db_path)

    response = TestClient(interface_app_mod.app).get("/api/daily-updates")

    assert response.status_code == 200
    assert response.json()["lastRun"] == {
        "status": "running",
        "completedAt": None,
    }


def test_daily_updates_api_does_not_refresh_runtime_activity() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/daily-updates",
            "headers": [],
            "query_string": b"",
        }
    )
    assert interface_app_mod._should_refresh_activity_for_request(request) is False

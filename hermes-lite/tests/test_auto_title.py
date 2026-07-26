from __future__ import annotations

import contextvars
import threading
from types import SimpleNamespace

import pytest

from agent import title_generator
from agent.title_generator import AutoTitleOutcome, auto_title_session, maybe_auto_title
from hermes_state import SessionDB


def _compression_chain(db: SessionDB) -> tuple[str, str]:
    db.create_session("root", "tui")
    db.end_session("root", "compression")
    db.create_session("tip", "tui", parent_session_id="root")
    return "root", "tip"


def test_atomic_auto_title_claim_targets_tip_and_respects_lineage_titles(tmp_path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        root, tip = _compression_chain(db)
        assert db.claim_auto_session_title(root, "Generated Title") == (
            tip,
            "Generated Title",
        )
        assert db.get_session_title(root) is None
        assert db.get_session_title(tip) == "Generated Title"

        db.create_session("manual-root", "tui")
        db.end_session("manual-root", "compression")
        db.create_session("manual-tip", "tui", parent_session_id="manual-root")
        db.set_session_title("manual-root", "Manual Title")
        assert db.claim_auto_session_title("manual-tip", "Ignored Title") is None
        assert db.get_session_title("manual-tip") is None
    finally:
        db.close()


def test_atomic_auto_title_claim_adds_unique_suffix_and_rejects_missing_row(tmp_path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("existing", "tui")
        db.set_session_title("existing", "Repeated Title")
        db.create_session("target", "tui")

        assert db.claim_auto_session_title("target", "Repeated Title") == (
            "target",
            "Repeated Title #2",
        )
        assert db.claim_auto_session_title("missing", "Missing") is None
    finally:
        db.close()


def test_concurrent_auto_title_claims_commit_exactly_once(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    first = SessionDB(db_path=db_path)
    first.create_session("target", "tui")
    second = SessionDB(db_path=db_path)
    barrier = threading.Barrier(2)
    results: list[tuple[str, str] | None] = []

    def claim(db: SessionDB, title: str) -> None:
        barrier.wait(timeout=5)
        results.append(db.claim_auto_session_title("target", title))

    threads = [
        threading.Thread(target=claim, args=(first, "First Title")),
        threading.Thread(target=claim, args=(second, "Second Title")),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in threads)
        assert len([result for result in results if result is not None]) == 1
        assert first.get_session_title("target") in {"First Title", "Second Title"}
    finally:
        second.close()
        first.close()


def test_manual_rename_wins_generation_commit_race(tmp_path, monkeypatch) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("target", "tui")
    generation_started = threading.Event()
    release_generation = threading.Event()
    outcomes: list[AutoTitleOutcome] = []
    committed_titles: list[str] = []

    def delayed_generate(*_args, **_kwargs) -> str:
        generation_started.set()
        assert release_generation.wait(timeout=5)
        return "Generated Title"

    monkeypatch.setattr(title_generator, "generate_title", delayed_generate)
    worker = threading.Thread(
        target=auto_title_session,
        args=(db, "target", "question", "answer"),
        kwargs={
            "title_callback": committed_titles.append,
            "outcome_callback": outcomes.append,
        },
    )
    try:
        worker.start()
        assert generation_started.wait(timeout=5)
        assert db.set_session_title("target", "Manual Title") is True
        release_generation.set()
        worker.join(timeout=5)

        assert not worker.is_alive()
        assert db.get_session_title("target") == "Manual Title"
        assert committed_titles == []
        assert outcomes == [
            AutoTitleOutcome("skipped", reason="title_claim_lost")
        ]
    finally:
        release_generation.set()
        worker.join(timeout=5)
        db.close()


def test_worker_callbacks_are_terminal_once_and_only_publish_committed_title(
    tmp_path,
    monkeypatch,
) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("target", "tui")
    outcomes: list[AutoTitleOutcome] = []
    titles: list[str] = []
    monkeypatch.setattr(title_generator, "generate_title", lambda *_args, **_kwargs: "Title")
    try:
        result = auto_title_session(
            db,
            "target",
            "question",
            "answer",
            title_callback=titles.append,
            outcome_callback=outcomes.append,
        )
    finally:
        db.close()

    assert result == AutoTitleOutcome("updated", title="Title")
    assert titles == ["Title"]
    assert outcomes == [result]


def test_worker_failure_has_one_terminal_callback_with_session_reason(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("target", "tui")
    outcomes: list[AutoTitleOutcome] = []

    def fail(*_args, **_kwargs):
        callback = _kwargs["failure_callback"]
        callback("title generation", RuntimeError("provider unavailable"))
        return None

    monkeypatch.setattr(title_generator, "generate_title", fail)
    try:
        with caplog.at_level("WARNING"):
            result = auto_title_session(
                db,
                "target",
                "question",
                "answer",
                outcome_callback=outcomes.append,
            )
    finally:
        db.close()

    assert result.status == "failed"
    assert outcomes == [result]
    assert "session target" in caplog.text


def test_maybe_auto_title_inherits_context_and_reports_worker_start(monkeypatch) -> None:
    marker = contextvars.ContextVar("auto_title_marker", default="missing")
    marker.set("profile-home")
    called = threading.Event()
    seen: list[str] = []

    def observe(*_args, **_kwargs) -> AutoTitleOutcome:
        seen.append(marker.get())
        called.set()
        return AutoTitleOutcome("skipped", reason="test")

    monkeypatch.setattr(title_generator, "auto_title_session", observe)
    started = maybe_auto_title(
        object(),
        "target",
        "question",
        "answer",
        [{"role": "user", "content": "question"}],
    )

    assert started is True
    assert called.wait(timeout=5)
    assert seen == ["profile-home"]


@pytest.mark.parametrize("api_mode", ["codex_responses", "chat_completions"])
def test_generate_title_forwards_explicit_main_runtime(monkeypatch, api_mode) -> None:
    captured = {}
    runtime = {
        "model": "test-model",
        "provider": "custom",
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "api_mode": api_mode,
    }

    def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Runtime Title"))]
        )

    monkeypatch.setattr(title_generator, "call_llm", fake_call_llm)

    assert title_generator.generate_title("question", "answer", main_runtime=runtime) == (
        "Runtime Title"
    )
    assert captured["main_runtime"] == runtime

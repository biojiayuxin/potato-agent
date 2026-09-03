from __future__ import annotations

import json
import threading

import pytest

from hermes_state import SessionDB


def _turns(*pairs: tuple[str, str]) -> list[dict[str, object]]:
    return [
        {
            "user": user,
            "assistant": assistant,
            "assistant_message_index": (index * 2) + 1,
        }
        for index, (user, assistant) in enumerate(pairs)
    ]


def _visible(*pairs: tuple[str, str]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    for user, assistant in pairs:
        messages.extend(
            [
                {"role": "user", "content": user, "timestamp": 1},
                {"role": "assistant", "content": assistant, "timestamp": 2},
            ]
        )
    return messages


def test_trusted_raw_fork_preserves_model_fields_and_resets_delivery_state(
    tmp_path,
) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(
            "source",
            "tui",
            model="model-a",
            model_config={"temperature": 0.2},
            system_prompt="system text",
            cwd="/workspace",
        )
        db.set_session_title("source", "Research")
        db.append_message("source", "system", "system text")
        db.append_message("source", "user", "question", platform_message_id="user-pm")
        db.append_message(
            "source",
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
            reasoning="working",
        )
        db.append_message(
            "source",
            "tool",
            "tool output",
            tool_name="read_file",
            tool_call_id="call-1",
        )
        boundary = db.append_message(
            "source",
            "assistant",
            "answer",
            finish_reason="stop",
            reasoning="private reasoning",
            reasoning_content="reasoning content",
            reasoning_details=[{"type": "summary_text", "text": "detail"}],
            codex_reasoning_items=[{"type": "reasoning"}],
            codex_message_items=[{"type": "message"}],
            platform_message_id="assistant-pm",
            observed=True,
        )
        db.update_token_counts("source", input_tokens=12, output_tokens=7)

        result = db.fork_session(
            target_session_id="fork-1",
            source_session_id="source",
            request_id="request-1",
            fork_cursor="assistant-display-1",
            source_title="Research",
            visible_history=_visible(("question", "answer")),
            display_turns=_turns(("question", "answer")),
            raw_boundary={
                "physical_session_id": "source",
                "active_message_head": boundary,
            },
        )

        session = db.get_session("fork-1")
        messages = db.get_messages("fork-1")
    finally:
        db.close()

    assert result["created"] is True
    assert result["context_mode"] == "raw"
    assert result["title"] == "Research #2"
    assert session is not None
    assert session["source"] == "tui"
    assert session["parent_session_id"] is None
    assert session["model"] == "model-a"
    assert session["system_prompt"] == "system text"
    assert session["cwd"] == "/workspace"
    assert session["ended_at"] is None
    assert session["archived"] == 0
    assert session["input_tokens"] == 0
    assert session["output_tokens"] == 0
    marker = json.loads(session["model_config"])["_potato_fork"]
    assert marker["m"] == "raw"
    assert marker["r"] == "request-1"
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[-1]["reasoning"] == "private reasoning"
    assert messages[-1]["reasoning_content"] == "reasoning content"
    assert messages[-1]["reasoning_details"] == json.dumps(
        [{"type": "summary_text", "text": "detail"}]
    )
    assert messages[-1]["codex_reasoning_items"] == json.dumps(
        [{"type": "reasoning"}]
    )
    assert messages[-1]["codex_message_items"] == json.dumps(
        [{"type": "message"}]
    )
    assert all(message["platform_message_id"] is None for message in messages)
    assert all(message["observed"] == 0 for message in messages)
    assert all(message["active"] == 1 for message in messages)


def test_fork_retry_does_not_overwrite_follow_up_messages(tmp_path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("source", "tui")
        db.set_session_title("source", "Chat")
        db.append_message("source", "user", "question")
        boundary = db.append_message("source", "assistant", "answer")
        kwargs = {
            "target_session_id": "fork-1",
            "source_session_id": "source",
            "request_id": "request-1",
            "fork_cursor": "assistant-1",
            "source_title": "Chat",
            "visible_history": _visible(("question", "answer")),
            "display_turns": _turns(("question", "answer")),
            "raw_boundary": {
                "physical_session_id": "source",
                "active_message_head": boundary,
            },
        }
        first = db.fork_session(**kwargs)
        db.append_message("fork-1", "user", "follow up")
        second = db.fork_session(**kwargs)
        messages = db.get_messages("fork-1")
    finally:
        db.close()

    assert first["created"] is True
    assert second["created"] is False
    assert messages[-1]["content"] == "follow up"
    assert len(messages) == 3


def test_existing_target_without_matching_marker_is_a_conflict(tmp_path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("source", "tui")
        db.append_message("source", "user", "question")
        db.append_message("source", "assistant", "answer")
        db.create_session("fork-1", "tui")
        with pytest.raises(ValueError, match="marker conflict"):
            db.fork_session(
                target_session_id="fork-1",
                source_session_id="source",
                request_id="request-1",
                fork_cursor="assistant-1",
                source_title="Chat",
                visible_history=_visible(("question", "answer")),
                display_turns=_turns(("question", "answer")),
            )
    finally:
        db.close()


def test_raw_fork_copies_only_the_selected_compression_segment(tmp_path) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("root", "tui", model="old-model")
        db.append_message("root", "user", "old question")
        root_boundary = db.append_message("root", "assistant", "old answer")
        db.end_session("root", "compression")
        db.create_session(
            "tip",
            "tui",
            parent_session_id="root",
            model="new-model",
        )
        db.append_message("tip", "system", "compressed summary")
        db.append_message("tip", "user", "new question")
        tip_boundary = db.append_message("tip", "assistant", "new answer")

        root_result = db.fork_session(
            target_session_id="fork-root",
            source_session_id="root",
            request_id="request-root",
            fork_cursor="old-assistant",
            source_title="Chat",
            visible_history=_visible(("old question", "old answer")),
            display_turns=_turns(("old question", "old answer")),
            raw_boundary={
                "physical_session_id": "root",
                "active_message_head": root_boundary,
            },
        )
        tip_result = db.fork_session(
            target_session_id="fork-tip",
            source_session_id="root",
            request_id="request-tip",
            fork_cursor="new-assistant",
            source_title="Chat",
            visible_history=_visible(
                ("old question", "old answer"),
                ("new question", "new answer"),
            ),
            display_turns=_turns(
                ("old question", "old answer"),
                ("new question", "new answer"),
            ),
            raw_boundary={
                "physical_session_id": "tip",
                "active_message_head": tip_boundary,
            },
        )
        root_messages = db.get_messages("fork-root")
        tip_messages = db.get_messages("fork-tip")
    finally:
        db.close()

    assert root_result["context_mode"] == "raw"
    assert tip_result["context_mode"] == "raw"
    assert [message["content"] for message in root_messages] == [
        "old question",
        "old answer",
    ]
    assert [message["content"] for message in tip_messages] == [
        "compressed summary",
        "new question",
        "new answer",
    ]


def test_legacy_alignment_uses_unique_longest_match_and_ambiguity_is_visible(
    tmp_path,
) -> None:
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("source", "tui")
        db.append_message("source", "user", "first")
        db.append_message("source", "assistant", "one")
        db.append_message("source", "user", "second")
        db.append_message("source", "assistant", "two")
        unique = db.fork_session(
            target_session_id="fork-unique",
            source_session_id="source",
            request_id="request-unique",
            fork_cursor="assistant-2",
            source_title="Chat",
            visible_history=_visible(("first", "one"), ("second", "two")),
            display_turns=_turns(("first", "one"), ("second", "two")),
        )

        db.create_session("ambiguous", "tui")
        for _ in range(2):
            db.append_message("ambiguous", "user", "same")
            db.append_message("ambiguous", "assistant", "reply")
        ambiguous = db.fork_session(
            target_session_id="fork-visible",
            source_session_id="ambiguous",
            request_id="request-visible",
            fork_cursor="assistant-visible",
            source_title="Chat",
            visible_history=_visible(("same", "reply")),
            display_turns=_turns(("same", "reply")),
        )
        visible_messages = db.get_messages("fork-visible")
    finally:
        db.close()

    assert unique["context_mode"] == "raw"
    assert ambiguous["context_mode"] == "visible"
    assert [(message["role"], message["content"]) for message in visible_messages] == [
        ("user", "same"),
        ("assistant", "reply"),
    ]


def test_concurrent_forks_allocate_distinct_numbered_titles(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    initializer = SessionDB(db_path=db_path)
    initializer.create_session("source", "tui")
    initializer.set_session_title("source", "Chat")
    initializer.append_message("source", "user", "question")
    initializer.append_message("source", "assistant", "answer")
    initializer.close()
    barrier = threading.Barrier(2)
    titles: list[str] = []

    def create(index: int) -> None:
        db = SessionDB(db_path=db_path)
        try:
            barrier.wait(timeout=5)
            result = db.fork_session(
                target_session_id=f"fork-{index}",
                source_session_id="source",
                request_id=f"request-{index}",
                fork_cursor="assistant-1",
                source_title="Chat",
                visible_history=_visible(("question", "answer")),
                display_turns=_turns(("question", "answer")),
            )
            titles.append(str(result["title"]))
        finally:
            db.close()

    workers = [threading.Thread(target=create, args=(index,)) for index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert set(titles) == {"Chat #2", "Chat #3"}

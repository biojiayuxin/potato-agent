from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from interface.tui_bridge_trace import JsonlTraceWriter, TraceSubscriber


def test_trace_writer_creates_private_file_and_appends(tmp_path) -> None:
    trace_path = tmp_path / "trace" / "events.jsonl"

    writer = JsonlTraceWriter(trace_path)
    writer.write("event", text="private prompt")
    writer.close()

    writer = JsonlTraceWriter(trace_path)
    writer.write("summary", status="complete")
    writer.close()

    assert stat.S_IMODE(trace_path.stat().st_mode) == 0o600
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [record["kind"] for record in records] == ["event", "summary"]


def test_trace_writer_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("unchanged\n", encoding="utf-8")
    link = tmp_path / "trace.jsonl"
    link.symlink_to(target)

    with pytest.raises(OSError):
        JsonlTraceWriter(link)

    assert target.read_text(encoding="utf-8") == "unchanged\n"


def test_trace_writer_rejects_non_private_parent(tmp_path: Path) -> None:
    public_parent = tmp_path / "public"
    public_parent.mkdir(mode=0o755)
    public_parent.chmod(0o755)

    with pytest.raises(PermissionError, match="private"):
        JsonlTraceWriter(public_parent / "events.jsonl")


def test_trace_writer_rejects_existing_hard_link(tmp_path: Path) -> None:
    trace_parent = tmp_path / "trace"
    trace_parent.mkdir(mode=0o700)
    target = trace_parent / "target.jsonl"
    target.write_text("unchanged\n", encoding="utf-8")
    target.chmod(0o600)
    linked = trace_parent / "events.jsonl"
    os.link(target, linked)

    with pytest.raises(PermissionError, match="hard link"):
        JsonlTraceWriter(linked)

    assert target.read_text(encoding="utf-8") == "unchanged\n"


def test_trace_defaults_to_metadata_only(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace" / "events.jsonl"
    writer = JsonlTraceWriter(trace_path)
    subscriber = TraceSubscriber(writer, include_content=False)
    sentinel = "chat-content-must-not-be-persisted"

    asyncio.run(
        subscriber.send_text(
            json.dumps(
                {
                    "type": "message.delta",
                    "session_id": "session-id",
                    "payload": {"text": sentinel},
                }
            )
        )
    )
    writer.close()

    body = trace_path.read_text(encoding="utf-8")
    assert sentinel not in body
    record = json.loads(body)
    assert record["delta_chars"] == len(sentinel)
    assert "text" not in record


def test_trace_content_requires_explicit_opt_in(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace" / "events.jsonl"
    writer = JsonlTraceWriter(trace_path)
    subscriber = TraceSubscriber(writer, include_content=True)
    sentinel = "explicit-diagnostic-content"

    asyncio.run(
        subscriber.send_text(
            json.dumps(
                {
                    "type": "message.delta",
                    "session_id": "session-id",
                    "payload": {"text": sentinel},
                }
            )
        )
    )
    writer.close()

    assert json.loads(trace_path.read_text(encoding="utf-8"))["text"] == sentinel

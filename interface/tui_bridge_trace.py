from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.cli_prompt import add_prompt_source_arguments, read_prompt
from interface.mapping import DEFAULT_MAPPING_PATH, MappingStore
from interface.tui_gateway_bridge import TuiGatewayBridge, TuiGatewayBridgeError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class JsonlTraceWriter:
    def __init__(self, path: Path) -> None:
        expanded = path.expanduser()
        if not expanded.name:
            raise ValueError("Trace path must name a file.")
        parent = expanded.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = parent / expanded.name

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(parent, directory_flags)
        try:
            directory_stat = os.fstat(directory_fd)
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise OSError("Trace parent must be a directory.")
            if directory_stat.st_uid != os.geteuid():
                raise PermissionError("Trace parent must be owned by the caller.")
            if stat.S_IMODE(directory_stat.st_mode) & 0o077:
                raise PermissionError("Trace parent must be private (0700 or stricter).")

            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_APPEND
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            descriptor = os.open(
                expanded.name,
                flags,
                0o600,
                dir_fd=directory_fd,
            )
        finally:
            os.close(directory_fd)

        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise OSError("Trace target must be a regular file.")
            if file_stat.st_uid != os.geteuid():
                raise PermissionError("Trace target must be owned by the caller.")
            if file_stat.st_nlink != 1:
                raise PermissionError("Trace target must have exactly one hard link.")
            os.fchmod(descriptor, 0o600)
            self._fh = os.fdopen(descriptor, "a", encoding="utf-8")
            descriptor = -1
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def write(self, kind: str, **payload: Any) -> None:
        record = {
            "ts": _utc_now_iso(),
            "monotonic": round(time.monotonic(), 6),
            "kind": kind,
            **payload,
        }
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        print(self._format_console(record), flush=True)

    def close(self) -> None:
        self._fh.close()

    def _format_console(self, record: dict[str, Any]) -> str:
        kind = str(record.get("kind") or "")
        if kind == "rpc.request":
            method = str(record.get("method") or "")
            return f"[rpc.request] {method}"
        if kind == "rpc.result":
            method = str(record.get("method") or "")
            return f"[rpc.result] {method}"
        if kind == "event":
            event_type = str(record.get("event_type") or "")
            if event_type == "message.delta":
                return f"[event] message.delta +{int(record.get('delta_chars') or 0)} chars"
            if event_type == "message.complete":
                payload = record.get("payload") or {}
                status = ""
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "")
                return f"[event] message.complete status={status or 'unknown'}"
            if event_type == "gateway.stderr":
                return f"[event] gateway.stderr chars={int(record.get('line_chars') or 0)}"
            return f"[event] {event_type}"
        if kind == "timeout":
            return f"[timeout] {record.get('message')}"
        if kind == "summary":
            status = str(record.get("status") or "")
            return f"[summary] status={status} log={self.path}"
        return f"[{kind}]"


class TraceSubscriber:
    def __init__(self, trace: JsonlTraceWriter, *, include_content: bool) -> None:
        self.trace = trace
        self.include_content = include_content
        self.done = asyncio.Event()
        self.final_event: dict[str, Any] | None = None
        self.delta_chars = 0

    async def send_text(self, payload: str) -> None:
        message = json.loads(payload)
        event_type = str(message.get("type") or "")
        session_id = str(message.get("session_id") or "")
        event_payload = (
            message.get("payload") if isinstance(message.get("payload"), dict) else {}
        )

        if event_type == "gateway.stderr":
            line = str(event_payload.get("line") or "")
            event_record: dict[str, Any] = {
                "event_type": event_type,
                "session_id": session_id,
                "line_chars": len(line),
            }
            if self.include_content:
                event_record["line"] = line
            self.trace.write("event", **event_record)
        elif event_type == "message.delta":
            text = str(event_payload.get("text") or "")
            self.delta_chars += len(text)
            event_record = {
                "event_type": event_type,
                "session_id": session_id,
                "delta_chars": len(text),
                "total_delta_chars": self.delta_chars,
            }
            if self.include_content:
                event_record["text"] = text
            self.trace.write("event", **event_record)
        else:
            event_record = {
                "event_type": event_type,
                "session_id": session_id,
                "payload_keys": sorted(str(key) for key in event_payload),
            }
            if self.include_content:
                event_record["payload"] = event_payload
            elif event_type == "message.complete":
                event_record["payload"] = {
                    "status": str(event_payload.get("status") or "")
                }
            self.trace.write("event", **event_record)

        if event_type in {"message.complete", "error", "gateway.exit"}:
            self.final_event = message
            self.done.set()


async def _invoke_rpc(
    bridge: TuiGatewayBridge,
    trace: JsonlTraceWriter,
    method: str,
    params: dict[str, Any],
    *,
    include_content: bool,
) -> dict[str, Any]:
    request_record: dict[str, Any] = {
        "method": method,
        "param_keys": sorted(str(key) for key in params),
    }
    if include_content:
        request_record["params"] = params
    trace.write("rpc.request", **request_record)
    result = await bridge.rpc(method, params)
    result_record: dict[str, Any] = {
        "method": method,
        "result_keys": sorted(str(key) for key in result),
    }
    if include_content:
        result_record["result"] = result
    trace.write("rpc.result", **result_record)
    return result


async def main_async(args: argparse.Namespace) -> int:
    store = MappingStore(Path(args.mapping_path))
    target = store.get_target_by_username(args.username)
    if target is None:
        raise SystemExit(f"User not found in mapping: {args.username}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_username = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.username)[:64] or "user"
    default_log = Path("trace_logs") / f"tui_bridge_{safe_username}_{timestamp}.jsonl"
    log_path = Path(args.log_file) if args.log_file else default_log
    trace = JsonlTraceWriter(log_path)
    bridge = TuiGatewayBridge(user_id=f"trace-{args.username}", target=target)
    subscriber = TraceSubscriber(trace, include_content=args.include_content)

    start_record: dict[str, Any] = {
        "username": args.username,
        "mapping_path": str(args.mapping_path),
        "log_path": str(log_path),
        "resume_session_id": str(args.session_id or ""),
        "prompt_chars": len(args.prompt),
        "timeout_seconds": args.timeout,
        "include_content": args.include_content,
    }
    if args.include_content:
        start_record["prompt"] = args.prompt
    trace.write("start", **start_record)

    exit_code = 1
    try:
        await bridge.ensure_started()
        await bridge.add_subscriber(subscriber)  # type: ignore[arg-type]

        if args.session_id:
            session_result = await _invoke_rpc(
                bridge,
                trace,
                "session.resume",
                {"session_id": args.session_id, "cols": args.cols},
                include_content=args.include_content,
            )
        else:
            session_result = await _invoke_rpc(
                bridge,
                trace,
                "session.create",
                {"cols": args.cols},
                include_content=args.include_content,
            )

        live_session_id = str(session_result.get("session_id") or "").strip()
        if not live_session_id:
            raise TuiGatewayBridgeError("no live session_id returned")

        await _invoke_rpc(
            bridge,
            trace,
            "prompt.submit",
            {
                "session_id": live_session_id,
                "text": args.prompt,
            },
            include_content=args.include_content,
        )

        try:
            await asyncio.wait_for(subscriber.done.wait(), timeout=args.timeout)
        except TimeoutError:
            trace.write(
                "timeout",
                session_id=live_session_id,
                message="did not receive message.complete, error, or gateway.exit before timeout",
            )
            exit_code = 3
        else:
            final_type = str((subscriber.final_event or {}).get("type") or "")
            final_payload = (
                (subscriber.final_event or {}).get("payload")
                if isinstance((subscriber.final_event or {}).get("payload"), dict)
                else {}
            )
            if final_type == "message.complete":
                summary_record: dict[str, Any] = {
                    "status": str(final_payload.get("status") or "complete"),
                    "final_type": final_type,
                    "final_text_chars": len(str(final_payload.get("text") or "")),
                    "live_session_id": live_session_id,
                }
                if args.include_content:
                    summary_record["final_text"] = str(final_payload.get("text") or "")
                trace.write("summary", **summary_record)
                exit_code = 0
            else:
                summary_record = {
                    "status": "failed",
                    "final_type": final_type or "unknown",
                    "final_payload_keys": sorted(str(key) for key in final_payload),
                    "live_session_id": live_session_id,
                }
                if args.include_content:
                    summary_record["final_payload"] = final_payload
                trace.write("summary", **summary_record)
                exit_code = 2
    finally:
        bridge.remove_subscriber(subscriber)  # type: ignore[arg-type]
        await bridge.close()
        trace.close()

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace a TUI gateway prompt end-to-end and persist events as JSONL."
    )
    parser.add_argument("username", help="mapping username to test")
    add_prompt_source_arguments(parser)
    parser.add_argument(
        "--session-id",
        default="",
        help="resume an existing persistent session instead of creating a new one",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds to wait for message.complete/error/gateway.exit",
    )
    parser.add_argument(
        "--cols",
        type=int,
        default=100,
        help="terminal width to use for session.create/session.resume",
    )
    parser.add_argument(
        "--log-file",
        default="",
        help="path in a caller-owned private directory (default: ./trace_logs/...)",
    )
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Persist full prompt, model output, stderr, and RPC payloads in the private trace.",
    )
    parser.add_argument(
        "--mapping-path",
        default=str(DEFAULT_MAPPING_PATH),
        help="users_mapping.yaml path",
    )
    args = parser.parse_args()
    args.prompt = read_prompt(
        args,
        default="Reply with exactly: TUI bridge trace ok",
    )
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())

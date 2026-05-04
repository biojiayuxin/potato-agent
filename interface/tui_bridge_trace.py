from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from interface.mapping import DEFAULT_MAPPING_PATH, MappingStore
from interface.tui_gateway_bridge import TuiGatewayBridge, TuiGatewayBridgeError


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _preview(text: str, limit: int = 120) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


class JsonlTraceWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

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
            return f"[rpc.result] {method} {_preview(json.dumps(record.get('result') or {}, ensure_ascii=False))}"
        if kind == "event":
            event_type = str(record.get("event_type") or "")
            if event_type == "message.delta":
                text = str(record.get("text") or "")
                return f"[event] message.delta +{len(text)} chars {_preview(text)}"
            if event_type == "message.complete":
                payload = record.get("payload") or {}
                status = ""
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "")
                text = ""
                if isinstance(payload, dict):
                    text = str(payload.get("text") or "")
                return f"[event] message.complete status={status or 'unknown'} {_preview(text)}"
            if event_type == "gateway.stderr":
                return f"[event] gateway.stderr {_preview(str(record.get('line') or ''))}"
            return f"[event] {event_type}"
        if kind == "timeout":
            return f"[timeout] {record.get('message')}"
        if kind == "summary":
            status = str(record.get("status") or "")
            return f"[summary] status={status} log={self.path}"
        return f"[{kind}] {_preview(json.dumps(record, ensure_ascii=False))}"


class TraceSubscriber:
    def __init__(self, trace: JsonlTraceWriter) -> None:
        self.trace = trace
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
            self.trace.write(
                "event",
                event_type=event_type,
                session_id=session_id,
                line=str(event_payload.get("line") or ""),
            )
        elif event_type == "message.delta":
            text = str(event_payload.get("text") or "")
            self.delta_chars += len(text)
            self.trace.write(
                "event",
                event_type=event_type,
                session_id=session_id,
                text=text,
                delta_chars=len(text),
                total_delta_chars=self.delta_chars,
            )
        else:
            self.trace.write(
                "event",
                event_type=event_type,
                session_id=session_id,
                payload=event_payload,
            )

        if event_type in {"message.complete", "error", "gateway.exit"}:
            self.final_event = message
            self.done.set()


async def _invoke_rpc(
    bridge: TuiGatewayBridge,
    trace: JsonlTraceWriter,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    trace.write("rpc.request", method=method, params=params)
    result = await bridge.rpc(method, params)
    trace.write("rpc.result", method=method, result=result)
    return result


async def main_async(args: argparse.Namespace) -> int:
    store = MappingStore(Path(args.mapping_path))
    target = store.get_target_by_username(args.username)
    if target is None:
        raise SystemExit(f"User not found in mapping: {args.username}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_log = Path("trace_logs") / f"tui_bridge_{args.username}_{timestamp}.jsonl"
    log_path = Path(args.log_file) if args.log_file else default_log
    trace = JsonlTraceWriter(log_path)
    bridge = TuiGatewayBridge(user_id=f"trace-{args.username}", target=target)
    subscriber = TraceSubscriber(trace)

    trace.write(
        "start",
        username=args.username,
        mapping_path=str(args.mapping_path),
        log_path=str(log_path),
        resume_session_id=str(args.session_id or ""),
        prompt=args.prompt,
        timeout_seconds=args.timeout,
    )

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
            )
        else:
            session_result = await _invoke_rpc(
                bridge,
                trace,
                "session.create",
                {"cols": args.cols},
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
                trace.write(
                    "summary",
                    status=str(final_payload.get("status") or "complete"),
                    final_type=final_type,
                    final_text=str(final_payload.get("text") or ""),
                    live_session_id=live_session_id,
                )
                exit_code = 0
            else:
                trace.write(
                    "summary",
                    status="failed",
                    final_type=final_type or "unknown",
                    final_payload=final_payload,
                    live_session_id=live_session_id,
                )
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
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: TUI bridge trace ok",
        help="prompt to submit through the bridge",
    )
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
        help="path to the JSONL trace file (default: ./trace_logs/...)",
    )
    parser.add_argument(
        "--mapping-path",
        default=str(DEFAULT_MAPPING_PATH),
        help="users_mapping.yaml path",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())

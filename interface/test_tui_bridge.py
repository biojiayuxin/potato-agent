from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from interface.cli_prompt import add_prompt_source_arguments, read_prompt
from interface.mapping import MappingStore, DEFAULT_MAPPING_PATH
from interface.tui_gateway_bridge import TuiGatewayBridge, TuiGatewayBridgeError


async def _collect_events(bridge: TuiGatewayBridge, duration_seconds: float) -> list[dict]:
    events: list[dict] = []

    class _Subscriber:
        async def send_text(self, payload: str) -> None:
            events.append(json.loads(payload))

    subscriber = _Subscriber()
    await bridge.add_subscriber(subscriber)  # type: ignore[arg-type]
    try:
        await asyncio.sleep(duration_seconds)
    finally:
        bridge.remove_subscriber(subscriber)  # type: ignore[arg-type]
    return events


async def main_async(username: str, prompt: str, mapping_path: Path) -> int:
    store = MappingStore(mapping_path)
    target = store.get_target_by_username(username)
    if target is None:
        raise SystemExit(f"User not found in mapping: {username}")

    bridge = TuiGatewayBridge(user_id=f"test-{username}", target=target)
    try:
        await bridge.ensure_started()
        print("[ok] bridge started")

        create_result = await bridge.rpc("session.create", {"cols": 100})
        print("[ok] session.create keys=", sorted(create_result))
        session_id = str(create_result.get("session_id") or "")
        if not session_id:
            raise TuiGatewayBridgeError("session.create returned no session_id")

        collector = asyncio.create_task(_collect_events(bridge, duration_seconds=20.0))
        submit_result = await bridge.rpc(
            "prompt.submit",
            {
                "session_id": session_id,
                "text": prompt,
            },
        )
        print("[ok] prompt.submit status=", str(submit_result.get("status") or ""))

        events = await collector
        print(f"[ok] collected {len(events)} bridge event(s)")
        for event in events:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            print(
                json.dumps(
                    {
                        "type": str(event.get("type") or ""),
                        "session_id": str(event.get("session_id") or ""),
                        "payload_keys": sorted(str(key) for key in payload),
                    },
                    ensure_ascii=False,
                )
            )

        has_complete = any(event.get("type") == "message.complete" for event in events)
        if not has_complete:
            raise TuiGatewayBridgeError("did not receive message.complete within timeout")
        return 0
    finally:
        await bridge.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal tui_gateway bridge probe")
    parser.add_argument("username", help="mapping username to test")
    add_prompt_source_arguments(parser)
    parser.add_argument(
        "--mapping-path",
        default=str(DEFAULT_MAPPING_PATH),
        help="users_mapping.yaml path",
    )
    args = parser.parse_args()
    prompt = read_prompt(args, default="Reply with exactly: TUI bridge probe ok")
    return asyncio.run(main_async(args.username, prompt, Path(args.mapping_path)))


if __name__ == "__main__":
    raise SystemExit(main())

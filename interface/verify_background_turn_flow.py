from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


async def main_async(username: str, prompt: str, timeout_seconds: float) -> int:
    from interface.app import mapping_store
    from interface.auth_db import get_user_by_login
    from interface.display_store import (
        get_display_messages,
        get_live_session_state,
    )
    from interface.session_run_manager import SessionRunManager
    from interface.tui_gateway_bridge import TuiGatewayBridgeRegistry

    user = get_user_by_login(username)
    if user is None:
        raise SystemExit(f"user not found: {username}")
    target = mapping_store.resolve_target(
        mapping_username=user.mapping_username,
        username=user.username,
        email=user.email,
    )
    if target is None:
        raise SystemExit(f"target not found for user: {username}")

    registry = TuiGatewayBridgeRegistry()
    manager = SessionRunManager()
    bridge = await registry.get_or_create(user.id, target)
    await manager.attach_bridge(bridge)

    live_session_id = ""
    session_id = ""
    try:
        created = await bridge.rpc("session.create", {"cols": 100})
        live_session_id = str(created.get("session_id") or "").strip()
        if not live_session_id:
            raise RuntimeError("session.create returned no live session id")

        title_info = await bridge.rpc("session.title", {"session_id": live_session_id})
        session_id = str(title_info.get("session_key") or live_session_id).strip()
        if not session_id:
            raise RuntimeError("session.title returned no persistent session id")

        result = await manager.submit_turn(
            bridge=bridge,
            user_id=user.id,
            session_id=session_id,
            live_session_id=live_session_id,
            prompt=prompt,
            attachments=[],
            existing_messages=[],
            draft_title=prompt[:10] or "verify",
        )
        print(json.dumps({"submitted": result["session_id"], "run_id": result["run_id"]}, ensure_ascii=False))

        deadline = time.monotonic() + timeout_seconds
        final_live = None
        while time.monotonic() < deadline:
            final_live = get_live_session_state(user.id, session_id)
            status = str((final_live or {}).get("status") or "")
            if status in {"completed", "failed", "interrupted"}:
                break
            await asyncio.sleep(1.0)
        else:
            raise RuntimeError(f"timed out waiting for completion: {final_live}")

        messages = get_display_messages(user.id, session_id) or []
        print(
            json.dumps(
                {
                    "session_id": session_id,
                    "live": final_live,
                    "message_count": len(messages),
                    "last_message": messages[-1] if messages else None,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await manager.shutdown()
        await registry.close_all()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify server-owned background turn flow.")
    parser.add_argument("--username", default="potato_agent")
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: background verification ok",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    return asyncio.run(main_async(args.username, args.prompt, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())

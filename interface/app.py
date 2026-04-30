from __future__ import annotations

import contextlib
import asyncio
import json
import os
import pwd
import re
import secrets
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import httpx
import jwt
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from interface.auth_db import (
    activate_signup_user,
    create_signup_job,
    ensure_auth_db,
    email_exists,
    get_next_pending_signup_job,
    get_signup_job,
    get_user_by_id,
    get_user_with_password_by_login,
    list_users,
    set_signup_job_status,
    username_exists,
    verify_password,
)
from interface.archive_store import (
    archive_session_record,
    count_archived_sessions,
    ensure_archive_db,
    finish_archive_run,
    list_archive_runs,
    start_archive_run,
)
from interface.background_jobs import has_active_background_processes
from interface.display_store import (
    delete_display_messages,
    ensure_display_store,
    get_display_session_meta,
    get_display_messages,
    save_display_messages,
    set_display_draft_title,
)
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore
from interface.hermes_service import (
    ensure_service_ready,
    install_user_files,
    is_service_active,
    remove_linux_user,
    service_operation_lock,
    stop_service,
    stop_and_remove_service,
)
from interface.runtime_state import (
    FOREGROUND_CHAT_LEASE,
    cleanup_expired_runtime_leases,
    clear_session_revocation,
    create_runtime_lease,
    ensure_runtime_state_store,
    get_runtime_state,
    has_active_runtime_leases,
    heartbeat_runtime_lease,
    list_idle_runtime_candidates,
    mark_background_activity,
    mark_runtime_started,
    mark_user_message_activity,
    release_runtime_lease,
    revoke_runtime_session,
)
from interface.mapping import (
    load_mapping,
    upsert_user_mapping_entry,
    write_mapping,
    remove_user_mapping_entry,
)


ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
STATIC_DIR = ROOT_DIR / "static"
LITE_DIR = STATIC_DIR / "lite"
FAVICON_PATH = STATIC_DIR / "favicon.png"
SESSION_COOKIE_NAME = "potato_interface_token"
SESSION_SECRET = os.getenv("INTERFACE_SESSION_SECRET") or secrets.token_urlsafe(32)
SESSION_TTL_SECONDS = int(
    os.getenv("INTERFACE_SESSION_TTL_SECONDS", str(7 * 24 * 3600))
)
MAX_UPLOAD_SIZE_BYTES = int(
    os.getenv("INTERFACE_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
)
FILE_BROWSER_MODE = (
    os.getenv("INTERFACE_FILE_BROWSER_MODE", "home_only").strip().lower()
)
UPLOAD_DIR_NAME = os.getenv("INTERFACE_UPLOAD_DIR_NAME", ".potato-interface-uploads")
ARCHIVE_RETENTION_DAYS = int(os.getenv("INTERFACE_ARCHIVE_RETENTION_DAYS", "7"))
ARCHIVE_SCHEDULE_HOUR = int(os.getenv("INTERFACE_ARCHIVE_SCHEDULE_HOUR", "3"))
RUNTIME_IDLE_TIMEOUT_SECONDS = int(
    os.getenv("INTERFACE_RUNTIME_IDLE_TIMEOUT_SECONDS", str(30 * 60))
)
RUNTIME_IDLE_CHECK_INTERVAL_SECONDS = int(
    os.getenv("INTERFACE_RUNTIME_IDLE_CHECK_INTERVAL_SECONDS", "60")
)
FOREGROUND_CHAT_LEASE_TTL_SECONDS = int(
    os.getenv("INTERFACE_FOREGROUND_CHAT_LEASE_TTL_SECONDS", "90")
)
FOREGROUND_CHAT_HEARTBEAT_INTERVAL_SECONDS = int(
    os.getenv("INTERFACE_FOREGROUND_CHAT_HEARTBEAT_INTERVAL_SECONDS", "15")
)
FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
CONTEXT_COMPACTION_PREFIXES = (
    "[CONTEXT COMPACTION",
    "[CONTEXT SUMMARY]:",
)
CONTEXT_COMPACTION_NOTICE = (
    "Context was compressed in the background because the conversation got too long. "
    "I'll continue from the compacted context."
)

HERMES_SRC = REPO_ROOT / "hermes-agent"
if str(HERMES_SRC) not in sys.path:
    sys.path.insert(0, str(HERMES_SRC))

mapping_store = MappingStore(DEFAULT_MAPPING_PATH)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    username: str
    name: str
    role: str
    mapping_username: str
    target: HermesTarget


class SigninRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str
    display_name: str = ""


class ApprovalDecisionRequest(BaseModel):
    choice: str


def _normalized_file_browser_mode() -> str:
    if FILE_BROWSER_MODE in {"home_only", "user_readable"}:
        return FILE_BROWSER_MODE
    return "home_only"


def _now_seconds() -> int:
    return int(datetime.now(UTC).timestamp())


def _validate_signup_payload(payload: SignupRequest) -> tuple[str, str, str, str]:
    username = payload.username.strip()
    email = payload.email.strip().lower()
    password = payload.password
    display_name = payload.display_name.strip() or username

    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-32 characters and contain only letters, numbers, or underscores.",
        )
    if "@" not in email or len(email) > 254:
        raise HTTPException(status_code=400, detail="Invalid email address.")
    if len(password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters."
        )
    if username_exists(username):
        raise HTTPException(status_code=409, detail="Username is already in use.")
    if email_exists(email):
        raise HTTPException(status_code=409, detail="Email is already in use.")
    return username, email, password, display_name


async def _signup_worker_loop() -> None:
    while True:
        job = get_next_pending_signup_job()
        if job is None:
            await asyncio.sleep(2)
            continue

        job_id = str(job["job_id"])
        username = str(job["username"])
        email = str(job["email"])
        display_name = str(job["display_name"])
        set_signup_job_status(job_id, status="provisioning")

        target = None
        try:
            config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=False)
            upsert_user_mapping_entry(
                config,
                username=username,
                email=email,
                display_name=display_name,
            )
            write_mapping(DEFAULT_MAPPING_PATH, config)
            resolved_config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=True)
            mapping_store._mtime_ns = None
            mapping_store._targets = []
            target = mapping_store.get_target_by_username(username)
            if target is None:
                raise RuntimeError("Failed to resolve newly created mapping target.")

            install_user_files(resolved_config, target)
            activate_signup_user(job_id, mapping_username=username)
            set_signup_job_status(job_id, status="completed")
        except Exception as exc:
            try:
                config = load_mapping(DEFAULT_MAPPING_PATH, resolve_env=False)
                remove_user_mapping_entry(config, username)
                write_mapping(DEFAULT_MAPPING_PATH, config)
                mapping_store._mtime_ns = None
                mapping_store._targets = []
            except Exception:
                pass

            if target is not None:
                with contextlib.suppress(Exception):
                    stop_and_remove_service(target.systemd_service)
                with contextlib.suppress(Exception):
                    remove_linux_user(target.linux_user, delete_home=True)

            set_signup_job_status(job_id, status="failed", error_message=str(exc))


def _create_session_token(user_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=SESSION_TTL_SECONDS)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm="HS256")


def _decode_session_token(token: str) -> dict[str, Any] | None:
    try:
        decoded = jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _extract_request_token(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        return token

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def _serialize_user(user: CurrentUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "name": user.name,
        "role": user.role,
        "mapping_username": user.mapping_username,
        "workspace_root": str(_get_user_workspace_root(user)),
    }


def _revocation_message(reason: str) -> str:
    if reason == "idle_timeout":
        return "Workspace slept after 30 minutes of inactivity. Please sign in again."
    return "Session expired"


def _session_revocation_payload(reason: str) -> dict[str, Any]:
    return {
        "authenticated": False,
        "reason": reason,
        "message": _revocation_message(reason),
    }


def _resolve_current_user(request: Request) -> tuple[CurrentUser | None, str | None]:
    token = _extract_request_token(request)
    if not token:
        return None, None

    decoded = _decode_session_token(token)
    if not isinstance(decoded, dict):
        return None, None

    user_id = str(decoded.get("sub") or "").strip()
    if not user_id:
        return None, None

    issued_at = int(decoded.get("iat") or 0)
    record = get_user_by_id(user_id)
    if record is None or not record.active:
        return None, None

    runtime_state = get_runtime_state(record.id)
    revoked_after = int((runtime_state or {}).get("session_revoked_after") or 0)
    revoked_reason = str((runtime_state or {}).get("last_sleep_reason") or "").strip()
    if revoked_after and issued_at <= revoked_after:
        return None, revoked_reason or "idle_timeout"

    target = mapping_store.resolve_target(
        mapping_username=record.mapping_username,
        email=record.email,
        username=record.username,
    )
    if target is None:
        return None, None

    return (
        CurrentUser(
            id=record.id,
            email=record.email,
            username=record.username,
            name=record.name,
            role=record.role,
            mapping_username=record.mapping_username,
            target=target,
        ),
        None,
    )


async def get_current_user(request: Request) -> CurrentUser:
    user, revoked_reason = _resolve_current_user(request)
    if user is None and revoked_reason:
        raise HTTPException(status_code=401, detail=_revocation_message(revoked_reason))
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _normalize_relative_browser_path(path: str | None) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw or raw == "/":
        return ""

    parts: list[str] = []
    for part in PurePosixPath(raw.lstrip("/")).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise HTTPException(status_code=400, detail="Invalid path")
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _normalize_logical_absolute_path(path: Path) -> Path:
    parts: list[str] = []
    for part in PurePosixPath(path.as_posix()).parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return Path("/").joinpath(*parts) if parts else Path("/")


def _resolve_file_browser_target(root: Path, path: str | None) -> tuple[str, Path]:
    relative = _normalize_relative_browser_path(path)
    target = (root / relative).resolve()
    return relative, target


def _expand_user_directory_input(user: CurrentUser, path: str | None) -> Path:
    home = _normalize_logical_absolute_path(user.target.home_dir.resolve())
    raw = str(path or "").strip()
    if not raw or raw == "~":
        return home
    if raw.startswith("~/"):
        return _normalize_logical_absolute_path(home / raw[2:])
    if raw.startswith("/"):
        return _normalize_logical_absolute_path(Path(raw))
    return _normalize_logical_absolute_path(home / raw)


def _resolve_file_browser_root(user: CurrentUser, requested_root: str | None) -> Path:
    home = _normalize_logical_absolute_path(user.target.home_dir.resolve())
    if not requested_root:
        return home

    root = _expand_user_directory_input(user, requested_root)
    if _normalized_file_browser_mode() == "home_only":
        try:
            root.relative_to(home)
        except ValueError as exc:
            raise HTTPException(
                status_code=403,
                detail="Opening directories outside ~/ is disabled on this deployment",
            ) from exc
    return root


def _probe_path_as_user(path: Path, *, linux_user: str) -> dict[str, Any]:
    script = (
        "import json, os, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[1]).resolve()\n"
        "payload = {'exists': target.exists()}\n"
        "if payload['exists']:\n"
        "    payload['is_dir'] = target.is_dir()\n"
        "    payload['is_file'] = target.is_file()\n"
        "    payload['readable'] = os.access(target, os.R_OK)\n"
        "    payload['enterable'] = os.access(target, os.X_OK)\n"
        "else:\n"
        "    payload['is_dir'] = False\n"
        "    payload['is_file'] = False\n"
        "    payload['readable'] = False\n"
        "    payload['enterable'] = False\n"
        "print(json.dumps(payload))\n"
    )
    result = subprocess.run(
        ["runuser", "-u", linux_user, "--", "python3", "-c", script, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "path probe failed"
        raise HTTPException(status_code=500, detail=detail)
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Invalid path probe response") from exc
    return payload if isinstance(payload, dict) else {}


def _assert_user_can_open_directory(path: Path, *, linux_user: str) -> None:
    payload = _probe_path_as_user(path, linux_user=linux_user)
    if not payload.get("exists"):
        raise HTTPException(status_code=404, detail="Requested path does not exist")
    if not payload.get("is_dir"):
        raise HTTPException(status_code=400, detail="Requested path is not a directory")
    if not (payload.get("readable") and payload.get("enterable")):
        raise HTTPException(status_code=403, detail="Permission denied for this directory")


def _assert_user_can_read_file(path: Path, *, linux_user: str) -> None:
    payload = _probe_path_as_user(path, linux_user=linux_user)
    if not payload.get("exists"):
        raise HTTPException(status_code=404, detail="Requested file does not exist")
    if not payload.get("is_file"):
        raise HTTPException(status_code=400, detail="Requested path is not a file")
    if not payload.get("readable"):
        raise HTTPException(status_code=403, detail="Permission denied for this file")


def _list_directory_as_user(
    path: Path,
    *,
    relative_path: str,
    linux_user: str,
) -> list[dict[str, Any]]:
    script = (
        "import json, os, pathlib, sys\n"
        "target = pathlib.Path(sys.argv[1]).resolve()\n"
        "logical_base = pathlib.PurePosixPath(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else pathlib.PurePosixPath()\n"
        "if not target.exists():\n"
        "    print(json.dumps({'error': 'not_found'}))\n"
        "    raise SystemExit(0)\n"
        "if not target.is_dir():\n"
        "    print(json.dumps({'error': 'not_directory'}))\n"
        "    raise SystemExit(0)\n"
        "if not os.access(target, os.R_OK | os.X_OK):\n"
        "    print(json.dumps({'error': 'permission_denied'}))\n"
        "    raise SystemExit(0)\n"
        "entries = []\n"
        "for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):\n"
        "    if child.name.startswith('.'):\n"
        "        continue\n"
        "    try:\n"
        "        child_stat = child.stat()\n"
        "    except PermissionError:\n"
        "        continue\n"
        "    entries.append({\n"
        "        'name': child.name,\n"
        "        'path': (logical_base / child.name).as_posix(),\n"
        "        'type': 'directory' if child.is_dir() else 'file',\n"
        "        'size': int(child_stat.st_size),\n"
        "        'modified': int(child_stat.st_mtime),\n"
        "    })\n"
        "print(json.dumps({'entries': entries}))\n"
    )
    result = subprocess.run(
        [
            "runuser",
            "-u",
            linux_user,
            "--",
            "python3",
            "-c",
            script,
            str(path),
            relative_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "directory access failed"
        raise HTTPException(status_code=500, detail=detail)

    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Invalid directory access response") from exc

    error = str(payload.get("error") or "").strip()
    if error == "not_found":
        raise HTTPException(status_code=404, detail="Requested path does not exist")
    if error == "not_directory":
        raise HTTPException(status_code=400, detail="Requested path is not a directory")
    if error == "permission_denied":
        raise HTTPException(status_code=403, detail="Permission denied for this directory")
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    return [item for item in entries if isinstance(item, dict)]


def _get_user_workspace_root(user: CurrentUser) -> Path:
    if user.target.home_dir:
        return user.target.home_dir
    return user.target.workdir


def _load_session_db(spec: HermesTarget):
    try:
        from hermes_state import SessionDB  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to import Hermes session DB: {exc}"
        ) from exc
    return SessionDB(db_path=spec.state_db_path)


@contextlib.contextmanager
def _open_session_db(spec: HermesTarget) -> Iterator[Any]:
    db = _load_session_db(spec)
    try:
        yield db
    finally:
        db.close()


def _normalize_session_row(session: dict[str, Any]) -> dict[str, Any]:
    started_at = int(session.get("started_at") or 0)
    last_active = int(session.get("last_active") or session.get("started_at") or 0)
    preview = str(session.get("preview") or "")
    title = str(session.get("title") or "").strip() or preview or "New chat"
    return {
        "id": str(session.get("id") or ""),
        "source": str(session.get("source") or ""),
        "model": str(session.get("model") or ""),
        "title": title,
        "preview": preview,
        "started_at": started_at,
        "last_active": last_active,
        "message_count": int(session.get("message_count") or 0),
        "tool_call_count": int(session.get("tool_call_count") or 0),
    }


def _derive_draft_title_from_user_message(message: dict[str, Any]) -> str:
    text = str(message.get("content") or "").strip()
    if text:
        return text[:10] or "New chat"

    files = message.get("files") if isinstance(message.get("files"), list) else []
    first_file = files[0] if files else None
    if isinstance(first_file, dict) and first_file.get("name"):
        return str(first_file.get("name"))[:10]

    return "New chat"


def _should_set_initial_draft_title(
    session: dict[str, Any] | None, display_meta: dict[str, Any] | None
) -> bool:
    if not isinstance(session, dict):
        return False

    if int(session.get("message_count") or 0) > 0:
        return False

    hermes_title = str(session.get("title") or "").strip()
    if hermes_title and hermes_title != "New chat":
        return False

    existing_draft_title = str((display_meta or {}).get("draft_title") or "").strip()
    return not existing_draft_title


def _apply_session_title_fallback(
    session: dict[str, Any], display_meta: dict[str, Any] | None
) -> dict[str, Any]:
    normalized = _normalize_session_row(session)
    hermes_title = str(session.get("title") or "").strip()
    draft_title = str((display_meta or {}).get("draft_title") or "").strip()
    if hermes_title:
        normalized["title"] = hermes_title
    elif draft_title:
        normalized["title"] = draft_title
    return normalized


def _logical_session_id_from_row(session: dict[str, Any] | None) -> str:
    if not isinstance(session, dict):
        return ""
    return str(session.get("_lineage_root_id") or session.get("id") or "").strip()


def _is_context_compaction_message(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "").strip()
    return bool(content) and content.startswith(CONTEXT_COMPACTION_PREFIXES)


def _is_compression_continuation(
    parent_session: dict[str, Any] | None,
    child_session: dict[str, Any] | None,
) -> bool:
    if not isinstance(parent_session, dict) or not isinstance(child_session, dict):
        return False

    parent_id = str(parent_session.get("id") or "").strip()
    if not parent_id:
        return False

    if str(child_session.get("parent_session_id") or "").strip() != parent_id:
        return False

    if str(parent_session.get("end_reason") or "").strip() != "compression":
        return False

    ended_at = float(parent_session.get("ended_at") or 0)
    started_at = float(child_session.get("started_at") or 0)
    return ended_at > 0 and started_at >= ended_at


def _find_logical_session_root_id(db: Any, session_id: str) -> str:
    current_id = str(session_id or "").strip()
    if not current_id:
        return ""

    current_session = db.get_session(current_id)
    if not current_session:
        return current_id

    for _ in range(100):
        parent_id = str(current_session.get("parent_session_id") or "").strip()
        if not parent_id:
            return current_id

        parent_session = db.get_session(parent_id)
        if not _is_compression_continuation(parent_session, current_session):
            return current_id

        current_id = parent_id
        current_session = parent_session

    return current_id


def _get_logical_session_tip_id(db: Any, logical_session_id: str) -> str:
    logical_id = str(logical_session_id or "").strip()
    if not logical_id:
        return ""
    return str(db.get_compression_tip(logical_id) or logical_id).strip()


def _get_projected_logical_session_row(
    db: Any, logical_session_id: str
) -> dict[str, Any] | None:
    for session in db.list_sessions_rich(
        source="api_server",
        limit=100000,
        offset=0,
    ):
        if _logical_session_id_from_row(session) == logical_session_id:
            return session
    return None


def _normalize_logical_session_row(
    session: dict[str, Any],
    *,
    logical_session_id: str,
    logical_session: dict[str, Any] | None,
    display_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = _normalize_session_row(session)
    normalized["id"] = logical_session_id

    root_title = str((logical_session or {}).get("title") or "").strip()
    draft_title = str((display_meta or {}).get("draft_title") or "").strip()
    if root_title:
        normalized["title"] = root_title
    elif draft_title:
        normalized["title"] = draft_title

    return normalized


def _generate_interface_session_id() -> str:
    timestamp_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    return f"{timestamp_str}_{short_uuid}"


def _normalize_message_row(message: dict[str, Any]) -> dict[str, Any]:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        tool_calls = []
    return {
        "id": int(message.get("id") or 0),
        "role": str(message.get("role") or ""),
        "content": message.get("content") or "",
        "timestamp": int(message.get("timestamp") or 0),
        "tool_name": message.get("tool_name") or "",
        "tool_call_id": message.get("tool_call_id") or "",
        "tool_calls": tool_calls,
        "finish_reason": message.get("finish_reason") or "",
        "reasoning": message.get("reasoning") or "",
    }


def _normalize_display_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(message.get("id") or uuid.uuid4().hex),
        "role": str(message.get("role") or "assistant"),
        "content": str(message.get("content") or ""),
        "reasoningContent": str(message.get("reasoningContent") or ""),
        "toolCalls": message.get("toolCalls")
        if isinstance(message.get("toolCalls"), list)
        else [],
        "progressLines": message.get("progressLines")
        if isinstance(message.get("progressLines"), list)
        else [],
        "files": message.get("files") if isinstance(message.get("files"), list) else [],
        "timestamp": int(message.get("timestamp") or 0),
        "done": bool(message.get("done", True)),
        "source": str(message.get("source") or "display_store"),
    }


def _normalize_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = (
        tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    )
    normalized = {
        "id": str(tool_call.get("id") or ""),
        "index": int(tool_call.get("index") or 0),
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": str(function.get("arguments") or ""),
        },
    }
    if tool_call.get("type"):
        normalized["type"] = str(tool_call.get("type"))
    return normalized


def _merge_tool_call_deltas(
    existing_tool_calls: list[dict[str, Any]], delta_tool_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = [_normalize_tool_call(item) for item in existing_tool_calls]

    for delta_tool_call in delta_tool_calls:
        index = int(delta_tool_call.get("index") or len(merged))
        while len(merged) <= index:
            merged.append(_normalize_tool_call({"index": len(merged)}))

        current = merged[index]
        if delta_tool_call.get("id"):
            current["id"] = str(delta_tool_call["id"])
        if delta_tool_call.get("type"):
            current["type"] = str(delta_tool_call["type"])

        function_delta = (
            delta_tool_call.get("function")
            if isinstance(delta_tool_call.get("function"), dict)
            else {}
        )
        if function_delta.get("name"):
            current["function"]["name"] = str(function_delta["name"])
        if function_delta.get("arguments"):
            current["function"]["arguments"] += str(function_delta["arguments"])

    return merged


def _append_progress_entries(message: dict[str, Any], entries: list[str]) -> None:
    progress_lines = message.setdefault("progressLines", [])
    if not isinstance(progress_lines, list):
        progress_lines = []
        message["progressLines"] = progress_lines
    for entry in entries:
        if not entry:
            continue
        if progress_lines and progress_lines[-1] == entry:
            continue
        progress_lines.append(entry)


def _build_user_display_message(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    user_payload = messages[-1] if messages and isinstance(messages[-1], dict) else {}
    raw_content = str(user_payload.get("content") or "")
    visible_content = raw_content
    files: list[dict[str, Any]] = []

    attachment_start = raw_content.find("<potato-files>")
    attachment_end = raw_content.find("</potato-files>")
    if attachment_start == 0 and attachment_end != -1:
        json_text = raw_content[len("<potato-files>") : attachment_end].strip()
        remainder = raw_content[attachment_end + len("</potato-files>") :].lstrip()
        hint_line = (
            "Use the attachment local paths above if you need to inspect the files."
        )
        if remainder.startswith(hint_line):
            remainder = remainder[len(hint_line) :].lstrip()
        visible_content = remainder
        try:
            parsed_files = json.loads(json_text)
        except json.JSONDecodeError:
            parsed_files = []
        if isinstance(parsed_files, list):
            files = parsed_files

    return _normalize_display_message(
        {
            "id": f"user-{uuid.uuid4().hex}",
            "role": "user",
            "content": visible_content,
            "reasoningContent": "",
            "toolCalls": [],
            "progressLines": [],
            "files": files,
            "timestamp": _now_seconds(),
            "done": True,
            "source": "display_store",
        }
    )


def _build_assistant_display_message() -> dict[str, Any]:
    return _normalize_display_message(
        {
            "id": f"assistant-{uuid.uuid4().hex}",
            "role": "assistant",
            "content": "",
            "reasoningContent": "",
            "toolCalls": [],
            "progressLines": [],
            "files": [],
            "timestamp": _now_seconds(),
            "done": False,
            "source": "display_store",
        }
    )


def _get_or_create_display_transcript(
    user_id: str,
    session_id: str,
    *,
    user_message: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    existing = get_display_messages(user_id, session_id)
    transcript = (
        [_normalize_display_message(item) for item in existing]
        if isinstance(existing, list)
        else []
    )
    if user_message is not None:
        transcript.append(_normalize_display_message(user_message))
    return transcript


def _extract_progress_lines(text: str) -> list[str]:
    return re.findall(r"`(?:💻|🔍|🧠|📁|🌐|📝|⚙️|🛠️)[^`]*`", text or "")


def _build_fallback_display_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    pending_assistant: dict[str, Any] | None = None

    def has_payload(message: dict[str, Any]) -> bool:
        return bool(
            str(message.get("content") or "").strip()
            or str(message.get("reasoningContent") or "").strip()
            or message.get("toolCalls")
            or message.get("progressLines")
            or message.get("files")
        )

    def flush_pending() -> None:
        nonlocal pending_assistant
        if pending_assistant and has_payload(pending_assistant):
            normalized.append(pending_assistant)
        pending_assistant = None

    filtered_messages = [
        raw_message
        for raw_message in messages
        if not _is_context_compaction_message(raw_message)
    ]

    for raw_message in filtered_messages:
        role = str(raw_message.get("role") or "")
        if role == "tool":
            continue

        if role == "user":
            flush_pending()
            content = str(raw_message.get("content") or "")
            normalized.append(
                {
                    "id": f"fallback-{raw_message.get('id') or uuid.uuid4().hex}",
                    "role": "user",
                    "content": content,
                    "reasoningContent": "",
                    "toolCalls": [],
                    "progressLines": [],
                    "files": [],
                    "timestamp": int(raw_message.get("timestamp") or 0),
                    "done": True,
                    "source": "fallback",
                }
            )
            continue

        if role != "assistant":
            continue

        display_message = {
            "id": f"fallback-{raw_message.get('id') or uuid.uuid4().hex}",
            "role": "assistant",
            "content": str(raw_message.get("content") or ""),
            "reasoningContent": str(raw_message.get("reasoning") or ""),
            "toolCalls": raw_message.get("tool_calls")
            if isinstance(raw_message.get("tool_calls"), list)
            else [],
            "progressLines": _extract_progress_lines(
                str(raw_message.get("content") or "")
            ),
            "files": [],
            "timestamp": int(raw_message.get("timestamp") or 0),
            "done": True,
            "source": "fallback",
        }

        has_text = bool(
            display_message["content"].strip()
            or display_message["reasoningContent"].strip()
        )
        has_tool_context = bool(
            display_message["toolCalls"] or display_message["progressLines"]
        )

        if pending_assistant is None:
            if has_tool_context and not has_text:
                pending_assistant = display_message
            elif has_payload(display_message):
                normalized.append(display_message)
            continue

        if display_message["content"].strip():
            pending_assistant["content"] = (
                f"{pending_assistant['content']}\n\n{display_message['content']}"
                if pending_assistant["content"].strip()
                else display_message["content"]
            )
        if display_message["reasoningContent"].strip():
            pending_assistant["reasoningContent"] = (
                f"{pending_assistant['reasoningContent']}\n\n{display_message['reasoningContent']}"
                if pending_assistant["reasoningContent"].strip()
                else display_message["reasoningContent"]
            )
        if display_message["toolCalls"]:
            pending_assistant["toolCalls"].extend(display_message["toolCalls"])
        if display_message["progressLines"]:
            pending_assistant["progressLines"].extend(display_message["progressLines"])
        pending_assistant["timestamp"] = max(
            int(pending_assistant.get("timestamp") or 0),
            int(display_message.get("timestamp") or 0),
        )

        if has_text:
            flush_pending()

    flush_pending()
    return normalized


def _build_context_compaction_notice_message(
    *,
    timestamp: int | None = None,
) -> dict[str, Any]:
    return _normalize_display_message(
        {
            "id": f"context-compaction-{uuid.uuid4().hex}",
            "role": "assistant",
            "content": CONTEXT_COMPACTION_NOTICE,
            "reasoningContent": "",
            "toolCalls": [],
            "progressLines": [],
            "files": [],
            "timestamp": int(timestamp or _now_seconds()),
            "done": True,
            "source": "context_compaction_notice",
        }
    )


def _collect_compression_lineage_session_ids(db: Any, logical_session_id: str) -> list[str]:
    logical_id = str(logical_session_id or "").strip()
    if not logical_id:
        return []

    all_sessions = db.list_sessions_rich(
        source="api_server",
        include_children=True,
        project_compression_tips=False,
        limit=100000,
        offset=0,
    )
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for session in all_sessions:
        parent_id = str(session.get("parent_session_id") or "").strip()
        if not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(session)

    session_ids: list[str] = []
    current_session = db.get_session(logical_id)
    while isinstance(current_session, dict):
        current_id = str(current_session.get("id") or "").strip()
        if not current_id or current_id in session_ids:
            break
        session_ids.append(current_id)

        children = sorted(
            children_by_parent.get(current_id, []),
            key=lambda item: float(item.get("started_at") or 0),
        )
        next_session = None
        for child in children:
            if _is_compression_continuation(current_session, child):
                next_session = child
                break
        current_session = next_session

    return session_ids


def _resolve_logical_session_context(
    db: Any, session_id: str
) -> tuple[str, dict[str, Any] | None, str, dict[str, Any] | None]:
    resolved = db.resolve_session_id(session_id)
    if not resolved:
        return "", None, "", None

    logical_session_id = _find_logical_session_root_id(db, resolved)
    logical_session = db.get_session(logical_session_id)
    tip_session_id = _get_logical_session_tip_id(db, logical_session_id)
    projected_session = (
        _get_projected_logical_session_row(db, logical_session_id)
        or logical_session
    )
    return logical_session_id, logical_session, tip_session_id, projected_session


async def _archive_expired_sessions_once() -> None:
    run_id = start_archive_run()
    archived_count = 0
    try:
        cutoff = time.time() - (ARCHIVE_RETENTION_DAYS * 86400)
        auth_users = {user.mapping_username: user for user in list_users()}

        for target in mapping_store.load_targets():
            auth_user = auth_users.get(target.username)
            with _open_session_db(target) as db:
                sessions = db.list_sessions_rich(
                    source="api_server", limit=100000, offset=0
                )
                for session in sessions:
                    session_id = _logical_session_id_from_row(session)
                    last_active = float(
                        session.get("last_active") or session.get("started_at") or 0
                    )
                    if not session_id or last_active >= cutoff:
                        continue

                    tip_session_id = _get_logical_session_tip_id(db, session_id)
                    raw_messages = db.get_messages(tip_session_id)
                    display_meta = (
                        get_display_session_meta(auth_user.id, session_id)
                        if auth_user is not None
                        else None
                    )
                    display_messages = (
                        display_meta.get("messages")
                        if display_meta
                        and isinstance(display_meta.get("messages"), list)
                        else _build_fallback_display_messages(raw_messages)
                    )
                    draft_title = (
                        str(display_meta.get("draft_title") or "")
                        if display_meta
                        else ""
                    )

                    archived = archive_session_record(
                        mapping_username=target.username,
                        email_snapshot=target.email,
                        session=session,
                        messages=raw_messages,
                        display_messages=display_messages,
                        draft_title=draft_title,
                    )
                    if not archived:
                        continue

                    for lineage_session_id in reversed(
                        _collect_compression_lineage_session_ids(db, session_id)
                    ):
                        db.delete_session(lineage_session_id)
                    if auth_user is not None:
                        delete_display_messages(auth_user.id, session_id)
                    archived_count += 1

        finish_archive_run(run_id, status="success", archived_count=archived_count)
    except Exception as exc:
        finish_archive_run(
            run_id,
            status="error",
            archived_count=archived_count,
            error_message=str(exc),
        )


async def _archive_scheduler_loop() -> None:
    while True:
        now = datetime.now()
        scheduled_for = now.replace(
            hour=ARCHIVE_SCHEDULE_HOUR, minute=0, second=0, microsecond=0
        )
        if scheduled_for <= now:
            scheduled_for = scheduled_for + timedelta(days=1)
        await asyncio.sleep(max((scheduled_for - now).total_seconds(), 60.0))
        try:
            await _archive_expired_sessions_once()
        except Exception:
            pass


async def _foreground_chat_lease_heartbeat(lease_id: str) -> None:
    try:
        while True:
            await asyncio.sleep(max(FOREGROUND_CHAT_HEARTBEAT_INTERVAL_SECONDS, 1))
            renewed = await asyncio.to_thread(
                heartbeat_runtime_lease,
                lease_id,
                ttl_seconds=FOREGROUND_CHAT_LEASE_TTL_SECONDS,
            )
            if not renewed:
                return
    except asyncio.CancelledError:
        raise


async def _runtime_idle_scheduler_loop() -> None:
    while True:
        await asyncio.sleep(max(RUNTIME_IDLE_CHECK_INTERVAL_SECONDS, 5))
        try:
            await asyncio.to_thread(cleanup_expired_runtime_leases)
            candidates = await asyncio.to_thread(
                list_idle_runtime_candidates,
                idle_timeout_seconds=RUNTIME_IDLE_TIMEOUT_SECONDS,
            )
            if not candidates:
                continue

            users_by_id = {user.id: user for user in list_users()}
            for candidate in candidates:
                auth_user = users_by_id.get(str(candidate.get("user_id") or ""))
                if auth_user is None:
                    continue
                target = mapping_store.resolve_target(
                    mapping_username=auth_user.mapping_username,
                    email=auth_user.email,
                    username=auth_user.username,
                )
                if target is None:
                    continue

                def _stop_if_idle() -> None:
                    with service_operation_lock(target.systemd_service):
                        cleanup_expired_runtime_leases()
                        if has_active_runtime_leases(auth_user.id):
                            return
                        try:
                            if has_active_background_processes(target):
                                mark_background_activity(auth_user.id)
                                return
                        except Exception:
                            # Fail open: if we cannot read or validate Hermes' process
                            # registry, keep the runtime alive rather than risk killing
                            # a long-running background task.
                            return
                        if not target.systemd_service or not os.path.exists(
                            f"/etc/systemd/system/{target.systemd_service}"
                        ):
                            return
                        if not target.systemd_service or not target.username:
                            return
                        if not is_service_active(target.systemd_service):
                            return
                        stop_service(target.systemd_service)
                        revoke_runtime_session(
                            auth_user.id,
                            reason="idle_timeout",
                        )

                await asyncio.to_thread(_stop_if_idle)
        except Exception:
            pass


def _sanitize_filename(filename: str) -> str:
    base = Path(filename or "upload.bin").name
    cleaned = FILENAME_SANITIZE_RE.sub("_", base).strip("._")
    return cleaned or "upload.bin"


def _apply_file_permissions(path: Path, *, linux_user: str, is_dir: bool) -> None:
    try:
        pw = pwd.getpwnam(linux_user)
        os.chown(path, pw.pw_uid, pw.pw_gid)
        os.chmod(path, 0o700 if is_dir else 0o600)
    except PermissionError:
        os.chmod(path, 0o755 if is_dir else 0o644)
    except KeyError:
        os.chmod(path, 0o755 if is_dir else 0o644)


def _ensure_upload_root(user: CurrentUser) -> Path:
    upload_dir_name = Path(UPLOAD_DIR_NAME)
    if upload_dir_name.is_absolute():
        upload_root = (upload_dir_name / user.target.username).resolve()
    else:
        upload_root = (_get_user_workspace_root(user) / upload_dir_name).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    _apply_file_permissions(upload_root, linux_user=user.target.linux_user, is_dir=True)
    return upload_root


app = FastAPI(title="Potato Interface")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def on_startup() -> None:
    ensure_auth_db()
    ensure_display_store()
    ensure_archive_db()
    ensure_runtime_state_store()
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
    app.state.archive_scheduler_task = asyncio.create_task(_archive_scheduler_loop())
    app.state.signup_worker_task = asyncio.create_task(_signup_worker_loop())
    app.state.runtime_idle_scheduler_task = asyncio.create_task(
        _runtime_idle_scheduler_loop()
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    archive_scheduler_task = getattr(app.state, "archive_scheduler_task", None)
    if archive_scheduler_task is not None:
        archive_scheduler_task.cancel()
    signup_worker_task = getattr(app.state, "signup_worker_task", None)
    if signup_worker_task is not None:
        signup_worker_task.cancel()
    runtime_idle_scheduler_task = getattr(
        app.state, "runtime_idle_scheduler_task", None
    )
    if runtime_idle_scheduler_task is not None:
        runtime_idle_scheduler_task.cancel()
    client: httpx.AsyncClient | None = getattr(app.state, "http", None)
    if client is not None:
        await client.aclose()


@app.get("/health")
async def healthcheck() -> dict[str, Any]:
    return {"status": True}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    if not FAVICON_PATH.is_file():
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(FAVICON_PATH)


@app.get("/api/auth/session")
async def auth_session(request: Request) -> dict[str, Any]:
    user, revoked_reason = _resolve_current_user(request)
    if user is None and revoked_reason:
        return _session_revocation_payload(revoked_reason)
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "user": _serialize_user(user)}


@app.post("/api/runtime/start")
async def start_runtime(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        runtime = await asyncio.to_thread(ensure_service_ready, user.target)
        await asyncio.to_thread(mark_runtime_started, user.id)
        await asyncio.to_thread(clear_session_revocation, user.id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "runtime": runtime,
        "user": _serialize_user(user),
    }


@app.get("/api/status")
async def api_status(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "status": True,
        "user": _serialize_user(user),
        "mapping_path": str(DEFAULT_MAPPING_PATH),
        "hermes_api_base": user.target.api_base_url,
        "state_db_path": str(user.target.state_db_path),
        "archived_session_count": count_archived_sessions(),
    }


@app.get("/api/archive/status")
async def archive_status(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return {
        "status": True,
        "retention_days": ARCHIVE_RETENTION_DAYS,
        "schedule_hour": ARCHIVE_SCHEDULE_HOUR,
        "archived_session_count": count_archived_sessions(),
        "runs": list_archive_runs(limit=10),
    }


@app.get("/", include_in_schema=False)
@app.get("/lite", include_in_schema=False)
async def serve_lite_index() -> FileResponse:
    file_path = LITE_DIR / "index.html"
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Lite frontend not found")
    return FileResponse(file_path)


@app.post("/api/auth/signin")
async def signin(payload: SigninRequest, response: Response) -> dict[str, Any]:
    login = payload.email.strip()
    if not login or not payload.password:
        raise HTTPException(
            status_code=400, detail="Email/username and password are required"
        )

    record, password_hash = get_user_with_password_by_login(login)
    if record is None or not record.active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    target = mapping_store.resolve_target(
        mapping_username=record.mapping_username,
        email=record.email,
        username=record.username,
    )
    if target is None:
        raise HTTPException(
            status_code=403, detail="No Hermes runtime is mapped to this user"
        )

    user = CurrentUser(
        id=record.id,
        email=record.email,
        username=record.username,
        name=record.name,
        role=record.role,
        mapping_username=record.mapping_username,
        target=target,
    )
    _set_session_cookie(response, _create_session_token(user.id))
    return _serialize_user(user)


@app.post("/api/auth/signup")
async def signup(payload: SignupRequest) -> dict[str, Any]:
    username, email, password, display_name = _validate_signup_payload(payload)
    try:
        job_id = create_signup_job(
            username=username,
            email=email,
            password=password,
            display_name=display_name,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to create signup job: {exc}"
        ) from exc

    return {"ok": True, "job_id": job_id, "status": "pending"}


@app.get("/api/auth/signup/{job_id}")
async def signup_status(job_id: str) -> dict[str, Any]:
    job = get_signup_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Signup job not found")
    return {"ok": True, "job": job}


@app.get("/api/auth/me")
async def auth_me(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return _serialize_user(user)


@app.post("/api/auth/signout")
async def signout(response: Response) -> dict[str, Any]:
    _clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/models")
async def get_models(user: CurrentUser = Depends(get_current_user)) -> Response:
    client: httpx.AsyncClient = app.state.http
    upstream = await client.get(
        f"{user.target.api_base_url}/v1/models",
        headers={"Authorization": f"Bearer {user.target.api_key}"},
    )
    return JSONResponse(status_code=upstream.status_code, content=upstream.json())


@app.get("/api/sessions")
async def get_sessions(
    limit: int = 50, offset: int = 0, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    with _open_session_db(user.target) as db:
        sessions = db.list_sessions_rich(
            source="api_server", limit=limit, offset=offset
        )
        normalized = []
        for item in sessions:
            logical_session_id = _logical_session_id_from_row(item)
            logical_session = db.get_session(logical_session_id)
            normalized.append(
                _normalize_logical_session_row(
                    item,
                    logical_session_id=logical_session_id,
                    logical_session=logical_session,
                    display_meta=get_display_session_meta(user.id, logical_session_id),
                )
            )
    normalized.sort(
        key=lambda item: (item["last_active"], item["started_at"]), reverse=True
    )
    return {"sessions": normalized, "limit": limit, "offset": offset}


@app.post("/api/sessions")
async def create_session(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    session_id = _generate_interface_session_id()
    with _open_session_db(user.target) as db:
        db.create_session(
            session_id=session_id,
            source="api_server",
        )
        session = db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=500, detail="Failed to create session")

    return {
        "session": _normalize_logical_session_row(
            session,
            logical_session_id=session_id,
            logical_session=session,
            display_meta=get_display_session_meta(user.id, session_id),
        )
    }


@app.get("/api/sessions/{session_id}")
async def get_session_detail(
    session_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    with _open_session_db(user.target) as db:
        logical_session_id, logical_session, tip_session_id, projected_session = (
            _resolve_logical_session_context(db, session_id)
        )
        if not logical_session or logical_session.get("source") != "api_server":
            raise HTTPException(status_code=404, detail="Session not found")
        raw_messages = db.get_messages(tip_session_id)

    display_messages = get_display_messages(user.id, logical_session_id)
    if display_messages is None:
        display_messages = _build_fallback_display_messages(raw_messages)

    return {
        "session": _normalize_logical_session_row(
            projected_session,
            logical_session_id=logical_session_id,
            logical_session=logical_session,
            display_meta=get_display_session_meta(user.id, logical_session_id),
        ),
        "messages": [_normalize_display_message(item) for item in display_messages],
    }


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    with _open_session_db(user.target) as db:
        logical_session_id, logical_session, _, _ = _resolve_logical_session_context(
            db, session_id
        )
        if not logical_session or logical_session.get("source") != "api_server":
            raise HTTPException(status_code=404, detail="Session not found")
        for lineage_session_id in reversed(
            _collect_compression_lineage_session_ids(db, logical_session_id)
        ):
            db.delete_session(lineage_session_id)
    delete_display_messages(user.id, logical_session_id)
    return {"ok": True}


@app.get("/api/files/tree")
async def files_tree(
    path: str | None = None,
    root: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    browser_root = _resolve_file_browser_root(user, root)
    relative_path, target = _resolve_file_browser_target(browser_root, path)
    if not browser_root.exists():
        raise HTTPException(status_code=404, detail="Workspace root does not exist")
    _assert_user_can_open_directory(target, linux_user=user.target.linux_user)
    entries = _list_directory_as_user(
        target,
        relative_path=relative_path,
        linux_user=user.target.linux_user,
    )

    return {
        "root": str(browser_root),
        "path": relative_path,
        "entries": entries,
    }


@app.get("/api/files/config")
async def files_config(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    root = _get_user_workspace_root(user)
    return {
        "mode": _normalized_file_browser_mode(),
        "home": str(user.target.home_dir),
        "root": str(root),
    }


@app.get("/api/files/open")
async def open_directory(
    path: str,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    mode = _normalized_file_browser_mode()
    target = _resolve_file_browser_root(user, path)
    _assert_user_can_open_directory(target, linux_user=user.target.linux_user)
    entries = _list_directory_as_user(
        target,
        relative_path="",
        linux_user=user.target.linux_user,
    )
    return {
        "mode": mode,
        "root": str(target),
        "path": "",
        "opened_path": str(target),
        "entries": entries,
    }


@app.get("/api/files/download")
async def files_download(
    path: str,
    root: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> FileResponse:
    browser_root = _resolve_file_browser_root(user, root)
    _, target = _resolve_file_browser_target(browser_root, path)
    _assert_user_can_read_file(target, linux_user=user.target.linux_user)
    return FileResponse(target, filename=target.name)


@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...), user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    upload_root = _ensure_upload_root(user)
    safe_name = _sanitize_filename(file.filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    file_id = uuid.uuid4().hex
    destination = upload_root / f"{file_id}_{timestamp}_{safe_name}"

    total_size = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload file too large (> {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB).",
                    )
                handle.write(chunk)
    except HTTPException:
        with contextlib.suppress(FileNotFoundError):
            destination.unlink()
        raise
    finally:
        await file.close()

    _apply_file_permissions(
        destination, linux_user=user.target.linux_user, is_dir=False
    )
    return {
        "id": file_id,
        "name": safe_name,
        "size": total_size,
        "content_type": file.content_type or "application/octet-stream",
        "path": str(destination),
    }


@app.post("/api/chat/approvals/{approval_id}")
async def resolve_chat_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    choice = str(payload.choice or "").strip().lower()
    if choice not in {"once", "session", "always", "deny"}:
        raise HTTPException(
            status_code=400,
            detail="choice must be one of once, session, always, deny",
        )

    client: httpx.AsyncClient = app.state.http
    upstream = await client.post(
        f"{user.target.api_base_url}/v1/approvals/{approval_id}",
        headers={"Authorization": f"Bearer {user.target.api_key}"},
        json={"choice": choice},
    )

    try:
        content = upstream.json()
        return JSONResponse(status_code=upstream.status_code, content=content)
    except Exception:
        return Response(
            content=upstream.text,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )


@app.post("/api/chat/completions")
async def chat_completions(
    request: Request, user: CurrentUser = Depends(get_current_user)
) -> Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid request body")

    await asyncio.to_thread(mark_user_message_activity, user.id)
    lease_id = await asyncio.to_thread(
        create_runtime_lease,
        user.id,
        lease_type=FOREGROUND_CHAT_LEASE,
        ttl_seconds=FOREGROUND_CHAT_LEASE_TTL_SECONDS,
        resource_id=str(uuid.uuid4()),
        meta={"username": user.username, "mapping_username": user.mapping_username},
    )
    lease_heartbeat_task = asyncio.create_task(_foreground_chat_lease_heartbeat(lease_id))

    async def _release_chat_lease() -> None:
        lease_heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lease_heartbeat_task
        await asyncio.to_thread(release_runtime_lease, lease_id)

    user_display_message = _build_user_display_message(body)
    assistant_display_message = _build_assistant_display_message()
    draft_title = _derive_draft_title_from_user_message(user_display_message)

    session_id = str(body.pop("session_id", "") or "").strip()
    logical_session_id = session_id
    runtime_session_id = session_id
    start_tip_session_id = ""
    context_compaction_notice_message: dict[str, Any] | None = None
    display_meta = None
    should_set_initial_draft_title = False
    display_transcript: list[dict[str, Any]] = []

    if session_id:
        with _open_session_db(user.target) as db:
            (
                resolved_logical_session_id,
                logical_session,
                tip_session_id,
                _,
            ) = _resolve_logical_session_context(db, session_id)
        if not logical_session or logical_session.get("source") != "api_server":
            raise HTTPException(status_code=404, detail="Session not found")
        logical_session_id = resolved_logical_session_id
        runtime_session_id = tip_session_id or logical_session_id
        start_tip_session_id = runtime_session_id
        display_meta = get_display_session_meta(user.id, logical_session_id)
        should_set_initial_draft_title = _should_set_initial_draft_title(
            logical_session, display_meta
        )
        if should_set_initial_draft_title:
            set_display_draft_title(user.id, logical_session_id, draft_title)
            display_meta = get_display_session_meta(user.id, logical_session_id)
        display_transcript = _get_or_create_display_transcript(
            user.id,
            logical_session_id,
            user_message=user_display_message,
        )

    upstream_headers = {
        "Authorization": f"Bearer {user.target.api_key}",
        "Content-Type": "application/json",
    }
    if runtime_session_id:
        upstream_headers["X-Hermes-Session-Id"] = runtime_session_id

    client: httpx.AsyncClient = app.state.http
    upstream = await client.send(
        client.build_request(
            "POST",
            f"{user.target.api_base_url}/v1/chat/completions",
            headers=upstream_headers,
            content=json.dumps(body).encode("utf-8"),
        ),
        stream=True,
    )

    if upstream.status_code >= 400:
        payload = await upstream.aread()
        await upstream.aclose()
        await _release_chat_lease()
        return Response(
            content=payload,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    response_headers: dict[str, str] = {}
    if upstream.headers.get("cache-control"):
        response_headers["Cache-Control"] = upstream.headers["cache-control"]
    if upstream.headers.get("x-accel-buffering"):
        response_headers["X-Accel-Buffering"] = upstream.headers["x-accel-buffering"]

    resolved_runtime_session_id = (
        runtime_session_id or str(upstream.headers.get("x-hermes-session-id") or "")
    ).strip()
    if logical_session_id:
        response_headers["X-Hermes-Session-Id"] = logical_session_id

    def persist_transcript() -> None:
        if not logical_session_id:
            return
        payload = [*display_transcript, assistant_display_message]
        if context_compaction_notice_message is not None:
            payload.append(context_compaction_notice_message)
        save_display_messages(
            user.id,
            logical_session_id,
            payload,
            draft_title=draft_title if should_set_initial_draft_title else None,
        )

    def finalize_logical_session_context() -> dict[str, Any] | None:
        nonlocal resolved_runtime_session_id, context_compaction_notice_message
        if not logical_session_id:
            return None
        with _open_session_db(user.target) as db:
            end_tip_session_id = _get_logical_session_tip_id(db, logical_session_id)
        if end_tip_session_id:
            resolved_runtime_session_id = end_tip_session_id
        if (
            logical_session_id
            and start_tip_session_id
            and end_tip_session_id
            and end_tip_session_id != start_tip_session_id
        ):
            context_compaction_notice_message = _build_context_compaction_notice_message(
                timestamp=assistant_display_message.get("timestamp")
            )
        return context_compaction_notice_message

    if body.get("stream") is False:
        payload = await upstream.aread()
        try:
            response_json = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response_json = {}

        assistant_content = ""
        if isinstance(response_json.get("choices"), list) and response_json["choices"]:
            first_choice = response_json["choices"][0]
            if isinstance(first_choice, dict):
                message = (
                    first_choice.get("message")
                    if isinstance(first_choice.get("message"), dict)
                    else {}
                )
                assistant_content = str(message.get("content") or "")

        if logical_session_id:
            if not display_transcript:
                display_transcript = _get_or_create_display_transcript(
                    user.id, logical_session_id, user_message=user_display_message
                )
            assistant_display_message["content"] = assistant_content
            assistant_display_message["done"] = True
            assistant_display_message["timestamp"] = _now_seconds()
            notice_message = finalize_logical_session_context()
            persist_transcript()
            if notice_message is not None:
                response_json.setdefault("choices", [{}])
                first_choice = response_json["choices"][0]
                if isinstance(first_choice, dict):
                    message = first_choice.get("message")
                    if isinstance(message, dict):
                        existing_content = str(message.get("content") or "").strip()
                        message["content"] = (
                            f"{existing_content}\n\n{notice_message['content']}"
                            if existing_content
                            else notice_message["content"]
                        )
                        payload = json.dumps(response_json).encode("utf-8")

        await upstream.aclose()
        await _release_chat_lease()
        return Response(
            content=payload,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
            headers=response_headers,
        )

    async def stream_upstream() -> Iterator[bytes]:
        nonlocal display_transcript
        try:
            async for chunk in upstream.aiter_bytes():
                if chunk:
                    if logical_session_id and not display_transcript:
                        display_transcript = _get_or_create_display_transcript(
                            user.id,
                            logical_session_id,
                            user_message=user_display_message,
                        )

                    text = chunk.decode("utf-8", errors="ignore")
                    for block in text.split("\n\n"):
                        if not block.strip():
                            continue

                        lines = block.split("\n")
                        event_name = ""
                        data_lines: list[str] = []
                        for line in lines:
                            if line.startswith("event: "):
                                event_name = line[7:].strip()
                            elif line.startswith("data: "):
                                data_lines.append(line[6:])

                        data = "\n".join(data_lines).strip()
                        if not data or data == "[DONE]":
                            continue

                        try:
                            event_payload = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        if event_name == "hermes.tool.progress":
                            emoji = str(event_payload.get("emoji") or "🛠️")
                            label = str(
                                event_payload.get("label")
                                or event_payload.get("tool")
                                or "tool"
                            )
                            _append_progress_entries(
                                assistant_display_message, [f"{emoji} {label}"]
                            )
                            assistant_display_message["timestamp"] = _now_seconds()
                            persist_transcript()
                            continue

                        delta = {}
                        if (
                            isinstance(event_payload.get("choices"), list)
                            and event_payload["choices"]
                        ):
                            first_choice = event_payload["choices"][0]
                            if isinstance(first_choice, dict):
                                delta = (
                                    first_choice.get("delta")
                                    if isinstance(first_choice.get("delta"), dict)
                                    else {}
                                )

                        if delta.get("reasoning_content"):
                            assistant_display_message["reasoningContent"] += str(
                                delta["reasoning_content"]
                            )
                        if (
                            isinstance(delta.get("tool_calls"), list)
                            and delta["tool_calls"]
                        ):
                            assistant_display_message["toolCalls"] = (
                                _merge_tool_call_deltas(
                                    assistant_display_message.get("toolCalls", []),
                                    delta["tool_calls"],
                                )
                            )
                        if delta.get("content"):
                            content_delta = str(delta["content"])
                            assistant_display_message["content"] += content_delta
                            progress_lines = _extract_progress_lines(content_delta)
                            if progress_lines:
                                _append_progress_entries(
                                    assistant_display_message, progress_lines
                                )

                        if delta:
                            assistant_display_message["timestamp"] = _now_seconds()
                            persist_transcript()

                    yield chunk
        finally:
            assistant_display_message["done"] = True
            assistant_display_message["timestamp"] = _now_seconds()
            notice_message = finalize_logical_session_context()
            persist_transcript()
            if notice_message is not None:
                notice_event = {
                    "content": str(notice_message.get("content") or ""),
                    "timestamp": int(notice_message.get("timestamp") or _now_seconds()),
                }
                yield (
                    f"event: hermes.context.compaction\n"
                    f"data: {json.dumps(notice_event)}\n\n"
                ).encode("utf-8")
            await upstream.aclose()
            await _release_chat_lease()

    return StreamingResponse(
        stream_upstream(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
        headers=response_headers,
    )

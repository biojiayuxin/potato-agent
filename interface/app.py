from __future__ import annotations

import contextlib
import json
import os
import pwd
import re
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    ensure_auth_db,
    get_user_by_id,
    get_user_with_password_by_login,
    verify_password,
)
from interface.mapping import DEFAULT_MAPPING_PATH, HermesTarget, MappingStore


ROOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = ROOT_DIR.parent
STATIC_DIR = ROOT_DIR / "static"
LITE_DIR = STATIC_DIR / "lite"
FAVICON_PATH = STATIC_DIR / "favicon.ico"
SESSION_COOKIE_NAME = "potato_interface_token"
SESSION_SECRET = os.getenv("INTERFACE_SESSION_SECRET") or secrets.token_urlsafe(32)
SESSION_TTL_SECONDS = int(
    os.getenv("INTERFACE_SESSION_TTL_SECONDS", str(7 * 24 * 3600))
)
MAX_UPLOAD_SIZE_BYTES = int(
    os.getenv("INTERFACE_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
)
UPLOAD_DIR_NAME = os.getenv("INTERFACE_UPLOAD_DIR_NAME", ".potato-interface-uploads")
FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")

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


class UpdateSessionTitleRequest(BaseModel):
    title: str = ""


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


async def get_current_user(request: Request) -> CurrentUser:
    token = _extract_request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    decoded = _decode_session_token(token)
    user_id = str((decoded or {}).get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session")

    record = get_user_by_id(user_id)
    if record is None or not record.active:
        raise HTTPException(status_code=401, detail="Session expired")

    target = mapping_store.resolve_target(
        mapping_username=record.mapping_username,
        email=record.email,
        username=record.username,
    )
    if target is None:
        raise HTTPException(
            status_code=403, detail="No Hermes runtime is mapped to this user"
        )

    return CurrentUser(
        id=record.id,
        email=record.email,
        username=record.username,
        name=record.name,
        role=record.role,
        mapping_username=record.mapping_username,
        target=target,
    )


def _ensure_resolved_within_root(root: Path, path: str | None) -> Path:
    root_resolved = root.resolve()
    relative = str(path or "").strip().lstrip("/")
    target = (root_resolved / relative).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc
    return target


def _get_user_workspace_root(user: CurrentUser) -> Path:
    if user.target.home_dir:
        return user.target.home_dir
    return user.target.workdir


def _relative_path(root: Path, target: Path) -> str:
    rel = target.resolve().relative_to(root.resolve())
    text = rel.as_posix()
    return "" if text == "." else text


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
        upload_root = (user.target.workdir / upload_dir_name).resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    _apply_file_permissions(upload_root, linux_user=user.target.linux_user, is_dir=True)
    return upload_root


app = FastAPI(title="Potato Interface")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def on_startup() -> None:
    ensure_auth_db()
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))


@app.on_event("shutdown")
async def on_shutdown() -> None:
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
    token = _extract_request_token(request)
    if not token:
        return {"authenticated": False}

    decoded = _decode_session_token(token)
    user_id = str((decoded or {}).get("sub") or "").strip()
    if not user_id:
        return {"authenticated": False}

    record = get_user_by_id(user_id)
    if record is None or not record.active:
        return {"authenticated": False}

    target = mapping_store.resolve_target(
        mapping_username=record.mapping_username,
        email=record.email,
        username=record.username,
    )
    if target is None:
        return {"authenticated": False}

    user = CurrentUser(
        id=record.id,
        email=record.email,
        username=record.username,
        name=record.name,
        role=record.role,
        mapping_username=record.mapping_username,
        target=target,
    )
    return {"authenticated": True, "user": _serialize_user(user)}


@app.get("/api/status")
async def api_status(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "status": True,
        "user": _serialize_user(user),
        "mapping_path": str(DEFAULT_MAPPING_PATH),
        "hermes_api_base": user.target.api_base_url,
        "state_db_path": str(user.target.state_db_path),
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
    normalized = [_normalize_session_row(item) for item in sessions]
    normalized.sort(
        key=lambda item: (item["last_active"], item["started_at"]), reverse=True
    )
    return {"sessions": normalized, "limit": limit, "offset": offset}


@app.get("/api/sessions/{session_id}")
async def get_session_detail(
    session_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    with _open_session_db(user.target) as db:
        resolved = db.resolve_session_id(session_id)
        if not resolved:
            raise HTTPException(status_code=404, detail="Session not found")
        session = db.get_session(resolved)
        if not session or session.get("source") != "api_server":
            raise HTTPException(status_code=404, detail="Session not found")
        messages = db.get_messages(resolved)
    return {
        "session": _normalize_session_row(session),
        "messages": [_normalize_message_row(item) for item in messages],
    }


@app.patch("/api/sessions/{session_id}")
async def update_session_title(
    session_id: str,
    payload: UpdateSessionTitleRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    with _open_session_db(user.target) as db:
        resolved = db.resolve_session_id(session_id)
        if not resolved:
            raise HTTPException(status_code=404, detail="Session not found")

        title = payload.title.strip()
        if title:
            try:
                db.set_session_title(resolved, title)
            except ValueError:
                db.set_session_title(resolved, db.get_next_title_in_lineage(title))
        else:
            db.set_session_title(resolved, "")

        session = db.get_session(resolved)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    return {"session": _normalize_session_row(session)}


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    with _open_session_db(user.target) as db:
        resolved = db.resolve_session_id(session_id)
        if not resolved:
            raise HTTPException(status_code=404, detail="Session not found")
        session = db.get_session(resolved)
        if not session or session.get("source") != "api_server":
            raise HTTPException(status_code=404, detail="Session not found")
        db.delete_session(resolved)
    return {"ok": True}


@app.get("/api/files/tree")
async def files_tree(
    path: str | None = None, user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    root = _get_user_workspace_root(user)
    target = _ensure_resolved_within_root(root, path)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Workspace root does not exist")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Requested path does not exist")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Requested path is not a directory")

    entries: list[dict[str, Any]] = []
    for child in sorted(
        target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
    ):
        if child.name.startswith("."):
            continue
        stat = child.stat()
        entries.append(
            {
                "name": child.name,
                "path": _relative_path(root, child),
                "type": "directory" if child.is_dir() else "file",
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
            }
        )

    return {"root": str(root), "path": _relative_path(root, target), "entries": entries}


@app.get("/api/files/download")
async def files_download(
    path: str, user: CurrentUser = Depends(get_current_user)
) -> FileResponse:
    root = _get_user_workspace_root(user)
    target = _ensure_resolved_within_root(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Requested file does not exist")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Requested path is not a file")
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

    session_id = str(body.pop("session_id", "") or "").strip()
    upstream_headers = {
        "Authorization": f"Bearer {user.target.api_key}",
        "Content-Type": "application/json",
    }
    if session_id:
        upstream_headers["X-Hermes-Session-Id"] = session_id

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
        return Response(
            content=payload,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    response_headers: dict[str, str] = {}
    if upstream.headers.get("x-hermes-session-id"):
        response_headers["X-Hermes-Session-Id"] = upstream.headers[
            "x-hermes-session-id"
        ]
    if upstream.headers.get("cache-control"):
        response_headers["Cache-Control"] = upstream.headers["cache-control"]
    if upstream.headers.get("x-accel-buffering"):
        response_headers["X-Accel-Buffering"] = upstream.headers["x-accel-buffering"]

    async def stream_upstream() -> Iterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream_upstream(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
        headers=response_headers,
    )

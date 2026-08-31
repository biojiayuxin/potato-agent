from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from interface import admin_store
from interface.admin_store import list_overview_entities
from interface.admin_usage_client import AdminUsageUnavailable, fetch_usage_aggregate
from interface.auth_db import (
    DEFAULT_AUTH_DB_PATH,
    InterfaceUser,
    get_user_by_id,
    get_user_with_password_by_login,
    is_temporary_user,
    verify_password,
)
from interface.runtime_state import list_active_runtime_user_ids
from interface.secret_config import load_session_cookie_secure, load_session_secret


ADMIN_COOKIE_NAME = "potato_admin_token"
ADMIN_COOKIE_PATH = "/admin"
ADMIN_AUDIENCE = "potato-admin"
ADMIN_SESSION_TTL_SECONDS = 4 * 3600
ADMIN_SESSION_SECRET = load_session_secret()
ADMIN_COOKIE_SECURE = load_session_cookie_secure()
ADMIN_AUTH_DB_PATH = DEFAULT_AUTH_DB_PATH
ADMIN_DUMMY_PASSWORD_HASH = "$2b$12$niguf.onTxAI39wN5IxuaOZJtea0nKCdXoc2TNVcUY5PuSb0asjj2"
ADMIN_STATIC_DIR = Path(__file__).resolve().parent / "static" / "admin"
WINDOW_SECONDS = {"24h": 24 * 3600, "7d": 7 * 24 * 3600, "30d": 30 * 24 * 3600}
VALID_USER_TYPES = frozenset({"all", "formal", "temporary", "retired"})
VALID_OVERVIEW_SORTS = frozenset(
    {
        "default",
        "user",
        "runtime_active",
        "created_at",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "total_tokens",
        "request_count",
        "storage_bytes",
    }
)
VALID_SORT_DIRECTIONS = frozenset({"asc", "desc"})
OVERVIEW_PAGE_SIZE = 25


class AdminSigninRequest(BaseModel):
    email: str = ""
    login: str = ""
    password: str


router = APIRouter()


def _iso_timestamp(value: float | int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat().replace("+00:00", "Z")


def _create_admin_token(user: InterfaceUser) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "aud": ADMIN_AUDIENCE,
            "sub": user.id,
            "sv": int(user.auth_session_version),
            "iat": now,
            "exp": now + ADMIN_SESSION_TTL_SECONDS,
        },
        ADMIN_SESSION_SECRET,
        algorithm="HS256",
    )


def _decode_admin_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token,
            ADMIN_SESSION_SECRET,
            algorithms=["HS256"],
            audience=ADMIN_AUDIENCE,
            options={"require": ["aud", "sub", "sv", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        return None
    return payload if isinstance(payload, dict) else None


def _set_admin_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        secure=ADMIN_COOKIE_SECURE,
        samesite="strict",
        path=ADMIN_COOKIE_PATH,
    )


def _clear_admin_cookie(response: Response) -> None:
    response.delete_cookie(
        ADMIN_COOKIE_NAME,
        httponly=True,
        secure=ADMIN_COOKIE_SECURE,
        samesite="strict",
        path=ADMIN_COOKIE_PATH,
    )


def _resolve_admin(request: Request) -> InterfaceUser | None:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        return None
    payload = _decode_admin_token(token)
    if payload is None:
        return None
    user_id = str(payload.get("sub") or "")
    try:
        session_version = int(payload.get("sv"))
    except (TypeError, ValueError):
        return None
    user = get_user_by_id(user_id, db_path=ADMIN_AUTH_DB_PATH)
    if (
        user is None
        or not user.active
        or user.role != "admin"
        or user.auth_session_version != session_version
        or is_temporary_user(user.id, db_path=ADMIN_AUTH_DB_PATH)
    ):
        return None
    return user


async def require_admin(request: Request) -> InterfaceUser:
    user = _resolve_admin(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Administrator authentication required")
    return user


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw_value in (os.getenv("INTERFACE_ADMIN_TRUSTED_PROXIES") or "").split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted_proxy(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_loopback:
        return True
    return any(address in network for network in _trusted_proxy_networks())


def trusted_client_ip(request: Request) -> str:
    peer_host = str(request.client.host if request.client else "")
    try:
        peer = ipaddress.ip_address(peer_host)
    except ValueError:
        return "unknown"
    if not _is_trusted_proxy(peer):
        return peer.compressed
    forwarded: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    raw_forwarded = request.headers.get("x-forwarded-for", "")
    for value in raw_forwarded.split(","):
        try:
            forwarded.append(ipaddress.ip_address(value.strip()))
        except ValueError:
            continue
    if not forwarded:
        raw_real_ip = request.headers.get("x-real-ip", "").strip()
        if raw_real_ip:
            try:
                forwarded.append(ipaddress.ip_address(raw_real_ip))
            except ValueError:
                pass
    for address in reversed(forwarded):
        if not _is_trusted_proxy(address):
            return address.compressed
    return forwarded[0].compressed if forwarded else peer.compressed


def _rate_limit_keys(login: str, client_ip: str) -> tuple[str, str]:
    normalized_login = login.strip().lower()

    def digest(scope: str, value: str) -> str:
        return hmac.new(
            ADMIN_SESSION_SECRET.encode("utf-8"),
            f"admin-signin\0{scope}\0{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    return (
        digest("login_ip", f"{client_ip}\0{normalized_login}"),
        digest("ip", client_ip),
    )


def _request_origin(request: Request) -> str:
    peer_host = str(request.client.host if request.client else "")
    scheme = request.url.scheme
    try:
        peer = ipaddress.ip_address(peer_host)
    except ValueError:
        peer = None
    if peer is not None and _is_trusted_proxy(peer):
        forwarded_scheme = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        if forwarded_scheme in {"http", "https"}:
            scheme = forwarded_scheme
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}".lower()


def validate_same_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").strip()
    if origin:
        parsed = urlsplit(origin)
        normalized = f"{parsed.scheme}://{parsed.netloc}".lower()
        if parsed.path not in {"", "/"} or normalized != _request_origin(request):
            raise HTTPException(status_code=403, detail="Cross-origin request rejected")
        return
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")


def _admin_user_payload(user: InterfaceUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "role": user.role,
    }


def _empty_usage(*, available: bool) -> dict[str, Any]:
    fields = {
        "request_count": 0,
        "usage_request_count": 0,
        "missing_usage_request_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
    }
    if not available:
        fields = {key: None for key in fields}
    return {"status": "available" if available else "unavailable", **fields}


def _normalize_usage(item: dict[str, Any]) -> dict[str, Any]:
    usage = _empty_usage(available=True)
    try:
        for field in tuple(usage):
            if field == "status":
                continue
            value = item.get(field)
            usage[field] = max(0, int(value or 0))
    except (TypeError, ValueError) as exc:
        raise AdminUsageUnavailable("usage source returned invalid totals") from exc
    return usage


def _entity_row(
    entity: dict[str, Any],
    *,
    usage: dict[str, Any],
    runtime_active: bool,
) -> dict[str, Any]:
    retired = bool(entity["is_retired"])
    snapshot = entity.get("storage") if not retired else None
    storage: dict[str, Any] | None = None
    if isinstance(snapshot, dict):
        storage = {
            "status": str(snapshot.get("status") or "error"),
            "allocated_bytes": snapshot.get("allocated_bytes"),
            "sampled_at": _iso_timestamp(snapshot.get("sampled_at")),
            "error_code": str(snapshot.get("error_code") or ""),
        }
    return {
        "id": str(entity.get("user_id") or entity["mapping_username"]),
        "username": str(entity["mapping_username"] if retired else entity["username"]),
        "email": None if retired else str(entity["email"]),
        "name": "Retired temporary user" if retired else str(entity["name"]),
        "role": None if retired else str(entity["role"]),
        "mapping_username": str(entity["mapping_username"]),
        "account_type": str(entity["account_type"]),
        "lifecycle": "retired" if retired else "current",
        "active": bool(entity["active"]),
        "runtime_active": bool(runtime_active and not retired and entity["active"]),
        "created_at": _iso_timestamp(entity.get("created_at")),
        "retired_at": _iso_timestamp(entity.get("retired_at")),
        "usage": usage,
        "storage": storage,
    }


def _overview_user_sort_value(row: dict[str, Any]) -> str:
    if row.get("lifecycle") == "retired":
        return str(row.get("mapping_username") or "").casefold()
    return str(row.get("name") or row.get("username") or "").casefold()


def _overview_created_at_sort_value(row: dict[str, Any]) -> float | None:
    value = str(row.get("created_at") or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _overview_group_rank(row: dict[str, Any]) -> int:
    if row.get("lifecycle") == "retired":
        return 2
    return 1 if row.get("account_type") == "temporary" else 0


def _overview_column_sort_value(row: dict[str, Any], sort_by: str) -> str | int | float | None:
    if sort_by == "user":
        return _overview_user_sort_value(row)
    if sort_by == "runtime_active":
        return int(bool(row.get("runtime_active")))
    if sort_by == "created_at":
        return _overview_created_at_sort_value(row)
    if sort_by == "storage_bytes":
        storage = row.get("storage")
        if not isinstance(storage, dict) or storage.get("status") != "ok":
            return None
        value = storage.get("allocated_bytes")
    else:
        usage = row.get("usage")
        if not isinstance(usage, dict) or usage.get("status") != "available":
            return None
        value = usage.get(sort_by)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sort_overview_rows(
    rows: list[dict[str, Any]], *, sort_by: str, direction: str
) -> None:
    tie_breaker = lambda row: (
        _overview_user_sort_value(row),
        str(row.get("mapping_username") or ""),
    )
    if sort_by == "default":
        rows.sort(
            key=lambda row: (
                _overview_group_rank(row),
                -created_at
                if (created_at := _overview_created_at_sort_value(row)) is not None
                else math.inf,
                *tie_breaker(row),
            )
        )
        return

    available: list[tuple[str | int | float, dict[str, Any]]] = []
    unavailable: list[dict[str, Any]] = []
    for row in rows:
        value = _overview_column_sort_value(row, sort_by)
        if value is None:
            unavailable.append(row)
        else:
            available.append((value, row))
    available.sort(
        key=lambda item: (item[0], *tie_breaker(item[1])),
        reverse=direction == "desc",
    )
    unavailable.sort(key=tie_breaker)
    rows[:] = [row for _value, row in available] + unavailable


@router.get("/admin", include_in_schema=False)
@router.get("/admin/", include_in_schema=False)
async def admin_index() -> FileResponse:
    index_path = ADMIN_STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Admin interface not found")
    return FileResponse(index_path)


@router.post("/admin/api/auth/signin")
async def admin_signin(
    payload: AdminSigninRequest,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    validate_same_origin(request)
    login = (payload.login or payload.email).strip()
    client_ip = trusted_client_ip(request)
    login_ip_key, ip_key = _rate_limit_keys(login, client_ip)
    retry_after = admin_store.signin_retry_after(
        login_ip_key=login_ip_key,
        ip_key=ip_key,
        db_path=ADMIN_AUTH_DB_PATH,
    )
    if retry_after > 0:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts",
            headers={"Retry-After": str(retry_after)},
        )

    record, password_hash = get_user_with_password_by_login(
        login, db_path=ADMIN_AUTH_DB_PATH
    )
    candidate_hash = password_hash if record is not None else ADMIN_DUMMY_PASSWORD_HASH
    try:
        password_matches = verify_password(payload.password, candidate_hash)
    except ValueError:
        password_matches = False
    accepted = (
        bool(login)
        and bool(payload.password)
        and record is not None
        and password_matches
        and record.active
        and record.role == "admin"
        and not is_temporary_user(record.id, db_path=ADMIN_AUTH_DB_PATH)
    )
    if not accepted or record is None:
        admin_store.record_signin_failure(
            login_ip_key=login_ip_key,
            ip_key=ip_key,
            db_path=ADMIN_AUTH_DB_PATH,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    admin_store.clear_login_signin_failures(
        login_ip_key,
        db_path=ADMIN_AUTH_DB_PATH,
    )
    _set_admin_cookie(response, _create_admin_token(record))
    return {"authenticated": True, "user": _admin_user_payload(record)}


@router.get("/admin/api/auth/session")
async def admin_session(request: Request) -> dict[str, Any]:
    user = _resolve_admin(request)
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "user": _admin_user_payload(user)}


@router.post("/admin/api/auth/signout")
async def admin_signout(request: Request, response: Response) -> dict[str, bool]:
    validate_same_origin(request)
    _clear_admin_cookie(response)
    return {"ok": True}


@router.get("/admin/api/overview")
async def admin_overview(
    window: str = Query(default="24h"),
    type: str = Query(default="all"),
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    sort: str = Query(default="default"),
    direction: str = Query(default="desc"),
    _admin: InterfaceUser = Depends(require_admin),
) -> dict[str, Any]:
    if window not in WINDOW_SECONDS:
        raise HTTPException(status_code=400, detail="Invalid usage window")
    if type not in VALID_USER_TYPES:
        raise HTTPException(status_code=400, detail="Invalid user type")
    if sort not in VALID_OVERVIEW_SORTS:
        raise HTTPException(status_code=400, detail="Invalid sort field")
    if direction not in VALID_SORT_DIRECTIONS:
        raise HTTPException(status_code=400, detail="Invalid sort direction")
    end_at = time.time()
    start_at = end_at - WINDOW_SECONDS[window]
    try:
        usage_items = await fetch_usage_aggregate(start_at=start_at, end_at=end_at)
        usage_available = True
    except AdminUsageUnavailable:
        usage_items = []
        usage_available = False
    try:
        usage_by_principal = {
            str(item.get("mapping_username") or ""): _normalize_usage(item)
            for item in usage_items
            if str(item.get("mapping_username") or "").strip()
        }
    except AdminUsageUnavailable:
        usage_by_principal = {}
        usage_available = False

    active_runtime_user_ids = list_active_runtime_user_ids(db_path=ADMIN_AUTH_DB_PATH)
    rows: list[dict[str, Any]] = []
    normalized_query = q.strip().casefold()
    for entity in list_overview_entities(db_path=ADMIN_AUTH_DB_PATH):
        mapping_username = str(entity["mapping_username"])
        entity_usage = usage_by_principal.get(
            mapping_username,
            _empty_usage(available=usage_available),
        )
        if entity["is_retired"] and (
            not usage_available or int(entity_usage.get("request_count") or 0) <= 0
        ):
            continue
        lifecycle = "retired" if entity["is_retired"] else "current"
        account_type = str(entity["account_type"])
        if type == "formal" and account_type != "formal":
            continue
        if type == "temporary" and (account_type != "temporary" or lifecycle != "current"):
            continue
        if type == "retired" and lifecycle != "retired":
            continue
        searchable = " ".join(
            str(entity.get(field) or "")
            for field in ("username", "email", "name", "mapping_username")
        ).casefold()
        if normalized_query and normalized_query not in searchable:
            continue
        rows.append(
            _entity_row(
                entity,
                usage=entity_usage,
                runtime_active=str(entity.get("user_id") or "")
                in active_runtime_user_ids,
            )
        )

    _sort_overview_rows(rows, sort_by=sort, direction=direction)
    total_users = len(rows)
    total_pages = max(1, math.ceil(total_users / OVERVIEW_PAGE_SIZE))
    normalized_page = min(page, total_pages)
    offset = (normalized_page - 1) * OVERVIEW_PAGE_SIZE
    paged_rows = rows[offset : offset + OVERVIEW_PAGE_SIZE]
    if usage_available:
        total_usage = _empty_usage(available=True)
        for row in rows:
            for field in total_usage:
                if field != "status":
                    total_usage[field] += int(row["usage"][field] or 0)
    else:
        total_usage = _empty_usage(available=False)
    return {
        "window": window,
        "start_at": _iso_timestamp(start_at),
        "end_at": _iso_timestamp(end_at),
        "data_sources": {
            "usage": {
                "status": "available" if usage_available else "unavailable",
                "coverage": "Fully consumed successful proxy responses; not billing-grade data.",
            },
            "storage": {"status": "available"},
        },
        "totals": {"user_count": total_users, "usage": total_usage},
        "pagination": {
            "page": normalized_page,
            "page_size": OVERVIEW_PAGE_SIZE,
            "total_pages": total_pages,
            "total_items": total_users,
        },
        "sorting": {"sort": sort, "direction": direction},
        "users": paged_rows,
    }


@router.get("/admin/api/system")
async def admin_system(
    request: Request,
    _admin: InterfaceUser = Depends(require_admin),
) -> dict[str, Any]:
    sampler = getattr(request.app.state, "system_resource_sampler", None)
    if sampler is None:
        return {
            "status": "unavailable",
            "sampled_at": None,
            "cpu_percent": None,
            "memory": {"used_bytes": None, "available_bytes": None, "total_bytes": None},
            "page_cache_bytes": None,
            "swap_bytes": None,
            "load": {"one": None, "five": None, "fifteen": None},
        }
    payload = sampler.snapshot()
    payload["sampled_at"] = _iso_timestamp(payload.get("sampled_at"))
    return payload

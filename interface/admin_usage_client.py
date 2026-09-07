from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from interface.admin_store import (
    PRINCIPAL_CATALOG_MIGRATION,
    migration_completed,
    reconcile_principal_catalog,
)
from interface.auth_db import DEFAULT_AUTH_DB_PATH
from interface.secret_config import SecretConfigurationError, load_secret


ADMIN_USAGE_TOKEN_ENVIRONMENT = "POTATO_ADMIN_USAGE_TOKEN"
ADMIN_USAGE_TOKEN_CREDENTIAL = "admin-usage-token"
ADMIN_USAGE_TOKEN_MIN_BYTES = 32
DEFAULT_ADMIN_USAGE_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_ADMIN_USAGE_TIMEOUT_SECONDS = 3.0


class AdminUsageUnavailable(RuntimeError):
    pass


def _load_token() -> str:
    try:
        token = load_secret(
            ADMIN_USAGE_TOKEN_ENVIRONMENT,
            credential_name=ADMIN_USAGE_TOKEN_CREDENTIAL,
        )
    except SecretConfigurationError as exc:
        raise AdminUsageUnavailable("usage credential is unavailable") from exc
    if token is None or len(token.encode("utf-8")) < ADMIN_USAGE_TOKEN_MIN_BYTES:
        raise AdminUsageUnavailable("usage credential is unavailable")
    return token


def _base_url() -> str:
    value = (os.getenv("POTATO_ADMIN_USAGE_BASE_URL") or DEFAULT_ADMIN_USAGE_BASE_URL).strip()
    return value.rstrip("/")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_load_token()}"}


async def fetch_usage_aggregate(
    *, start_at: float, end_at: float
) -> list[dict[str, Any]]:
    return await _fetch_usage("aggregate", start_at=start_at, end_at=end_at)


async def fetch_usage_daily(
    *, start_at: float, end_at: float
) -> list[dict[str, Any]]:
    return await _fetch_usage("daily", start_at=start_at, end_at=end_at)


async def _fetch_usage(
    endpoint: str, *, start_at: float, end_at: float
) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_ADMIN_USAGE_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            response = await client.get(
                f"{_base_url()}/internal/admin/usage/{endpoint}",
                params={"start_at": start_at, "end_at": end_at},
                headers=_headers(),
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, AdminUsageUnavailable) as exc:
        raise AdminUsageUnavailable("usage source is unavailable") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise AdminUsageUnavailable("usage source returned an invalid response")
    return [dict(item) for item in items]


async def fetch_principal_catalog() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_ADMIN_USAGE_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            while True:
                response = await client.get(
                    f"{_base_url()}/internal/admin/usage/principals",
                    params={"page": page, "page_size": 200},
                    headers=_headers(),
                )
                response.raise_for_status()
                payload = response.json()
                page_items = payload.get("items") if isinstance(payload, dict) else None
                total = payload.get("total") if isinstance(payload, dict) else None
                if not isinstance(page_items, list) or not isinstance(total, int):
                    raise ValueError("invalid principal catalog")
                if not all(isinstance(item, dict) for item in page_items):
                    raise ValueError("invalid principal catalog items")
                items.extend(dict(item) for item in page_items)
                if len(items) >= total:
                    return items
                if not page_items:
                    raise ValueError("incomplete principal catalog")
                page += 1
    except (httpx.HTTPError, ValueError, AdminUsageUnavailable) as exc:
        raise AdminUsageUnavailable("principal catalog is unavailable") from exc


async def reconcile_principals_once(
    *, db_path: Path = DEFAULT_AUTH_DB_PATH
) -> bool:
    if migration_completed(PRINCIPAL_CATALOG_MIGRATION, db_path=db_path):
        return False
    principals = await fetch_principal_catalog()
    return reconcile_principal_catalog(
        principals,
        migration_key=PRINCIPAL_CATALOG_MIGRATION,
        db_path=db_path,
    )

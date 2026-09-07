from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from interface import bulk_rnaseq_viewer, gene_catalog, genome_browser
from interface.admin_store import SERVICE_PRINCIPALS, list_overview_entities
from interface.admin_usage_client import AdminUsageUnavailable, fetch_usage_daily
from interface.auth_db import DEFAULT_AUTH_DB_PATH


router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parent / "static" / "dashboard"
TIME_ZONE = ZoneInfo("Asia/Shanghai")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
AUTH_DB_PATH = DEFAULT_AUTH_DB_PATH
USAGE_RETRY_SECONDS = 60
RESOURCE_CACHE_SECONDS = 300


def _now() -> datetime:
    return datetime.now(TIME_ZONE)


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Invalid count")
    return value


def _public_days(
    items: list[dict[str, Any]],
    start: date,
    today: date,
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    principals = {
        entity["mapping_username"]
        for entity in entities
        if entity["account_type"] in {"formal", "temporary"}
        and entity["mapping_username"] not in SERVICE_PRINCIPALS
    }
    days = {
        (start + timedelta(days=offset)).isoformat(): dict.fromkeys(TOKEN_FIELDS, 0)
        for offset in range((today - start).days)
    }
    for item in items:
        if item["mapping_username"] not in principals:
            continue
        day = days[item["date"]]
        for field in TOKEN_FIELDS:
            day[field] += _count(item[field])
    return [
        {"date": date, **tokens, "total_tokens": sum(tokens.values())}
        for date, tokens in days.items()
    ]


def _resource_counts() -> dict[str, Any]:
    sources = (
        (
            "genome_accessions", "Genome accessions", "/genomes",
            lambda: sum(
                isinstance(item, dict)
                for item in genome_browser.load_manifest()["assemblies"]
            ),
        ),
        (
            "catalog_genes", "Catalog genes", "/genes",
            lambda: gene_catalog.load_catalog()["counts"]["genes"],
        ),
        (
            "expression_samples", "Expression samples", "/bulk-rnaseq",
            lambda: bulk_rnaseq_viewer.load_status()["counts"]["samples"],
        ),
    )
    items = []
    for key, label, href, read in sources:
        try:
            count = _count(read())
        except Exception:
            # Each source is independent; internal paths and errors stay private.
            count = None
        items.append(
            {
                "id": key, "label": label, "href": href, "count": count,
                "status": "available" if count is not None else "unavailable",
            }
        )
    return {"items": items}


class DashboardCache:
    def __init__(self) -> None:
        self.usage_lock = asyncio.Lock()
        self.resource_lock = asyncio.Lock()
        self.usage_day: date | None = None
        self.usage_value: dict[str, Any] | None = None
        self.usage_retry_at = 0.0
        self.resource_value: dict[str, Any] | None = None
        self.resource_expires_at = 0.0

    async def usage(self) -> dict[str, Any]:
        async with self.usage_lock:
            today = _now().astimezone(TIME_ZONE).date()
            start = today - timedelta(days=30)
            if self.usage_day == today and self.usage_value is not None:
                return self.usage_value
            payload = {
                "status": "unavailable", "time_zone": TIME_ZONE.key,
                "start_date": start.isoformat(),
                "through_date": (today - timedelta(days=1)).isoformat(),
                "days": None,
            }
            if time.monotonic() < self.usage_retry_at:
                return payload
            try:
                if not AUTH_DB_PATH.is_file():
                    raise AdminUsageUnavailable("User catalog is unavailable")
                items = await fetch_usage_daily(
                    start_at=datetime.combine(start, day_time(), TIME_ZONE).timestamp(),
                    end_at=datetime.combine(today, day_time(), TIME_ZONE).timestamp(),
                )
                entities = await asyncio.to_thread(
                    list_overview_entities, db_path=AUTH_DB_PATH
                )
                payload["days"] = _public_days(items, start, today, entities)
            except (
                AdminUsageUnavailable, OSError, sqlite3.Error,
                ValueError, KeyError, TypeError,
            ):
                self.usage_retry_at = time.monotonic() + USAGE_RETRY_SECONDS
                return payload
            payload["status"] = "available"
            self.usage_day = today
            self.usage_value = payload
            self.usage_retry_at = 0.0
            return payload

    async def resources(self) -> dict[str, Any]:
        async with self.resource_lock:
            if self.resource_value is None or time.monotonic() >= self.resource_expires_at:
                self.resource_value = await asyncio.to_thread(_resource_counts)
                self.resource_expires_at = time.monotonic() + RESOURCE_CACHE_SECONDS
            return self.resource_value


def _cache(request: Request) -> DashboardCache:
    if not hasattr(request.app.state, "dashboard_cache"):
        request.app.state.dashboard_cache = DashboardCache()
    return request.app.state.dashboard_cache


@router.get("/dashboard", include_in_schema=False)
@router.get("/dashboard/", include_in_schema=False)
async def dashboard_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/dashboard/usage")
async def dashboard_usage(request: Request) -> JSONResponse:
    return JSONResponse(await _cache(request).usage(), headers={"Cache-Control": "no-store"})


@router.get("/api/dashboard/resources")
async def dashboard_resources(request: Request) -> JSONResponse:
    return JSONResponse(await _cache(request).resources(), headers={"Cache-Control": "no-store"})

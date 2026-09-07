from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from interface import admin_api, announcement_store


router = APIRouter()
admin_router = APIRouter(
    prefix="/admin/api/announcement",
    dependencies=[Depends(admin_api.require_admin)],
)


class AnnouncementContent(BaseModel):
    message: str = Field(strict=True, min_length=1, max_length=500)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None

    @field_validator("starts_at", "ends_at", mode="before")
    @classmethod
    def require_iso_time(cls, value):
        if value is not None and not isinstance(value, str):
            raise ValueError("Use an ISO timestamp with a timezone")
        return value


class AnnouncementIdentity(BaseModel):
    id: str = Field(strict=True, min_length=1, max_length=100)


class AnnouncementEdit(AnnouncementContent, AnnouncementIdentity):
    pass


def _response(
    response: Response, record: announcement_store.Announcement | None,
    now: float, *, public: bool = False,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    payload = None
    if record is not None:
        if public:
            if record.status(now) == "active":
                payload = record.public_payload()
        else:
            payload = record.admin_payload(now)
    return {"announcement": payload, "server_time": announcement_store.iso_timestamp(now)}


@router.get("/api/announcement")
def public_announcement(response: Response) -> dict:
    record = announcement_store.get_announcement()
    return _response(response, record, time.time(), public=True)


@admin_router.get("")
def read_announcement(response: Response) -> dict:
    record = announcement_store.get_announcement(db_path=admin_api.ADMIN_AUTH_DB_PATH)
    return _response(response, record, time.time())


def _save(
    content: AnnouncementContent, response: Response, existing_id: str | None = None,
) -> dict:
    now = time.time()
    try:
        record = announcement_store.save_announcement(
            message=content.message,
            starts_at=content.starts_at.timestamp() if content.starts_at else None,
            ends_at=content.ends_at.timestamp() if content.ends_at else None,
            existing_id=existing_id,
            now=now,
            db_path=admin_api.ADMIN_AUTH_DB_PATH,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _response(response, record, now)


@admin_router.post("/publish", dependencies=[Depends(admin_api.validate_same_origin)])
def publish_announcement(content: AnnouncementContent, response: Response) -> dict:
    return _save(content, response)


@admin_router.put("", dependencies=[Depends(admin_api.validate_same_origin)])
def edit_announcement(content: AnnouncementEdit, response: Response) -> dict:
    return _save(content, response, content.id)


@admin_router.post("/withdraw", dependencies=[Depends(admin_api.validate_same_origin)])
def withdraw_announcement(content: AnnouncementIdentity, response: Response) -> dict:
    now = time.time()
    try:
        record = announcement_store.withdraw_announcement(
            content.id, now=now, db_path=admin_api.ADMIN_AUTH_DB_PATH,
        )
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _response(response, record, now)


router.include_router(admin_router)

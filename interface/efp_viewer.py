from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter()
STATIC_ROOT = Path(__file__).resolve().parent / "static" / "efp"


@router.get("/efp", include_in_schema=False)
async def serve_efp_index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")

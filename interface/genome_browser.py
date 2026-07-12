from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


STATIC_ROOT = Path(__file__).resolve().parent / "static" / "genome_browser"
DEFAULT_DB_ROOT = Path("/mnt/data/public_data/Genome_browser_DB")
MAX_DEFAULT_REGION_BP = 100_000

router = APIRouter()


def get_db_root() -> Path:
    return Path(os.getenv("GENOME_BROWSER_DB_ROOT", str(DEFAULT_DB_ROOT))).resolve()


def resolve_under(root: Path, relative_path: str) -> Path:
    posix_path = PurePosixPath(relative_path)
    if posix_path.is_absolute() or ".." in posix_path.parts:
        raise HTTPException(status_code=404, detail="file not found")
    target = (root / Path(*posix_path.parts)).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=404, detail="file not found")
    return target


def load_manifest() -> dict[str, Any]:
    root = get_db_root()
    manifest_path = root / "assemblies.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=503, detail="Genome browser database not found")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Genome browser manifest is invalid: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("assemblies"), list):
        raise HTTPException(status_code=500, detail="Genome browser manifest is missing assemblies")
    return payload


def read_default_location(root: Path, assembly: dict[str, Any]) -> str:
    chrom_sizes = str(assembly.get("chromSizes") or "")
    if chrom_sizes:
        try:
            chrom_sizes_path = resolve_under(root, chrom_sizes)
            with chrom_sizes_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    parts = raw.rstrip("\n").split("\t")
                    if len(parts) >= 2:
                        length = max(1, int(parts[1]))
                        end = min(length, MAX_DEFAULT_REGION_BP)
                        return f"{parts[0]}:1..{end}"
        except (OSError, ValueError, HTTPException):
            pass
    return ""


def public_assembly(root: Path, assembly: dict[str, Any]) -> dict[str, Any]:
    item = dict(assembly)
    item.pop("featureCount", None)
    item.pop("note", None)
    item["defaultLocation"] = read_default_location(root, assembly)
    return item


def media_type_for_path(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".bgz"):
        return "application/gzip"
    if name.endswith((".gzi", ".tbi")):
        return "application/octet-stream"
    if name.endswith((".fai", ".sizes", ".tsv")):
        return "text/plain"
    return None


@router.head("/genome-browser", include_in_schema=False)
@router.get("/genome-browser", include_in_schema=False)
async def serve_genome_browser_index() -> FileResponse:
    index_path = STATIC_ROOT / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Genome browser frontend not found")
    return FileResponse(index_path)


@router.get("/api/genome-browser/assemblies")
async def api_genome_browser_assemblies() -> dict[str, Any]:
    root = get_db_root()
    manifest = load_manifest()
    assemblies = [
        public_assembly(root, assembly)
        for assembly in manifest["assemblies"]
        if isinstance(assembly, dict)
    ]
    return {
        "name": manifest.get("name", "Genome_browser_DB"),
        "description": manifest.get("description", ""),
        "version": manifest.get("version", ""),
        "updatedAt": manifest.get("updatedAt", ""),
        "counts": manifest.get("counts", {}),
        "assemblies": assemblies,
    }


@router.head("/api/genome-browser/data/{file_path:path}", include_in_schema=False)
@router.get("/api/genome-browser/data/{file_path:path}", include_in_schema=False)
async def api_genome_browser_data(file_path: str) -> FileResponse:
    root = get_db_root()
    target = resolve_under(root, file_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target, media_type=media_type_for_path(target))

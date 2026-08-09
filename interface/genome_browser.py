from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from interface.genome_feature_index import (
    AmbiguousFeatureError,
    FeatureIndexError,
    FeatureNotFoundError,
    default_index_path,
    load_fai,
    resolve_feature,
)


STATIC_ROOT = Path(__file__).resolve().parent / "static" / "genome_browser"
GENOMES_STATIC_ROOT = Path(__file__).resolve().parent / "static" / "genomes"
DEFAULT_DB_ROOT = Path("/mnt/data/public_data/Genome_browser_DB")
MAX_DEFAULT_REGION_BP = 100_000
MAX_SEQUENCE_SEGMENTS = 256
MAX_SEQUENCE_BP = 1_000_000
MAX_CONCURRENT_SEQUENCE_JOBS = 4
SEQUENCE_EXTRACTION_TIMEOUT_SECONDS = 30
PUBLIC_ASSEMBLY_FILE_KEYS = (
    "reference",
    "fai",
    "gzi",
    "chromSizes",
    "annotation",
    "annotationIndex",
)
DNA_COMPLEMENT = str.maketrans(
    "ACGTRYKMSWBDHVNacgtrykmswbdhvn",
    "TGCAYRMKSWVHDBNtgcayrmkswvhdbn",
)

router = APIRouter()
_sequence_limiter = threading.BoundedSemaphore(MAX_CONCURRENT_SEQUENCE_JOBS)


class SequenceSegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)

    name: str = Field(min_length=1, max_length=256)
    ref_name: str = Field(alias="refName", min_length=1, max_length=512)
    start: int
    end: int
    strand: Literal["+", "-"] = "+"


class SequenceBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assembly: str = Field(min_length=1, max_length=512)
    clip: bool = False
    segments: list[SequenceSegmentRequest] = Field(
        min_length=1, max_length=MAX_SEQUENCE_SEGMENTS
    )


class SequenceExtractionError(RuntimeError):
    pass


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


@lru_cache(maxsize=8)
def _load_manifest_cached(
    manifest_name: str,
    inode: int,
    size: int,
    modified_ns: int,
    changed_ns: int,
) -> dict[str, Any]:
    del inode, size, modified_ns, changed_ns
    manifest_path = Path(manifest_name)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Genome browser database not found") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Genome browser manifest is invalid: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("assemblies"), list):
        raise HTTPException(status_code=500, detail="Genome browser manifest is missing assemblies")
    return payload


def load_manifest() -> dict[str, Any]:
    manifest_path = get_db_root() / "assemblies.json"
    try:
        file_stat = manifest_path.stat()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Genome browser database not found") from exc
    if not manifest_path.is_file():
        raise HTTPException(status_code=503, detail="Genome browser database not found")
    return _load_manifest_cached(
        str(manifest_path),
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def find_assembly(manifest: dict[str, Any], assembly_id: str) -> dict[str, Any]:
    for assembly in manifest["assemblies"]:
        if isinstance(assembly, dict) and assembly.get("id") == assembly_id:
            return assembly
    raise HTTPException(status_code=404, detail="assembly not found")


def public_data_paths(manifest: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for assembly in manifest["assemblies"]:
        if not isinstance(assembly, dict):
            continue
        for key in PUBLIC_ASSEMBLY_FILE_KEYS:
            value = str(assembly.get(key) or "").strip()
            if value:
                paths.add(str(PurePosixPath(value)))
    return paths


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
    item.pop("representativeMap", None)
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


def reverse_complement(sequence: str) -> str:
    return sequence.translate(DNA_COMPLEMENT)[::-1]


@lru_cache(maxsize=256)
def _load_fai_cached(
    fai_name: str,
    inode: int,
    size: int,
    modified_ns: int,
    changed_ns: int,
) -> dict[str, int]:
    del inode, size, modified_ns, changed_ns
    return load_fai(Path(fai_name))


def load_reference_lengths(fai: Path) -> dict[str, int]:
    file_stat = fai.stat()
    return _load_fai_cached(
        str(fai),
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _iter_fasta_sequences(text: str) -> list[str]:
    sequences: list[str] = []
    parts: list[str] = []
    saw_header = False
    for line in text.splitlines():
        if line.startswith(">"):
            if saw_header:
                sequences.append("".join(parts))
            saw_header = True
            parts = []
        elif saw_header and line.strip():
            parts.append(line.strip())
    if saw_header:
        sequences.append("".join(parts))
    return sequences


def run_faidx(reference: Path, intervals: list[tuple[str, int, int]]) -> list[str]:
    configured = os.getenv("GENOME_BROWSER_SAMTOOLS", "samtools").strip() or "samtools"
    executable = shutil.which(configured) if os.path.sep not in configured else configured
    if not executable or not Path(executable).is_file():
        raise SequenceExtractionError("samtools is unavailable")
    with tempfile.TemporaryDirectory(prefix="genome-browser-sequence-") as temporary:
        regions_path = Path(temporary) / "regions.txt"
        regions_path.write_text(
            "".join(f"{ref_name}:{start}-{end}\n" for ref_name, start, end in intervals),
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(executable), "faidx", str(reference), "-r", str(regions_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=SEQUENCE_EXTRACTION_TIMEOUT_SECONDS,
        )
    if result.returncode != 0:
        raise SequenceExtractionError("samtools faidx failed")
    sequences = _iter_fasta_sequences(result.stdout)
    if len(sequences) != len(intervals):
        raise SequenceExtractionError("samtools returned an unexpected record count")
    for interval, sequence in zip(intervals, sequences):
        expected = interval[2] - interval[1] + 1
        if len(sequence) != expected:
            raise SequenceExtractionError("samtools returned an unexpected sequence length")
    return sequences


def extract_sequence_batch(
    root: Path,
    assembly: dict[str, Any],
    request: SequenceBatchRequest,
) -> dict[str, Any]:
    reference_value = str(assembly.get("reference") or "").strip()
    fai_value = str(assembly.get("fai") or "").strip()
    if not reference_value or not fai_value:
        raise HTTPException(status_code=503, detail="assembly reference is incomplete")
    reference = resolve_under(root, reference_value)
    fai = resolve_under(root, fai_value)
    if not reference.is_file() or not fai.is_file():
        raise HTTPException(status_code=503, detail="assembly reference is unavailable")
    try:
        ref_lengths = load_reference_lengths(fai)
    except (OSError, FeatureIndexError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="assembly reference index is invalid") from exc

    seen_names: set[str] = set()
    requested_total = 0
    prepared: list[dict[str, Any]] = []
    unique_intervals: list[tuple[str, int, int]] = []
    interval_indexes: dict[tuple[str, int, int], int] = {}
    for segment in request.segments:
        if segment.name in seen_names:
            raise HTTPException(status_code=422, detail="segment names must be unique")
        seen_names.add(segment.name)
        requested_length = segment.end - segment.start + 1
        if requested_length < 1:
            raise HTTPException(status_code=422, detail="segment end must be greater than or equal to start")
        if requested_length > MAX_SEQUENCE_BP:
            raise HTTPException(status_code=413, detail=f"a segment may contain at most {MAX_SEQUENCE_BP} bp")
        requested_total += requested_length
        if requested_total > MAX_SEQUENCE_BP:
            raise HTTPException(status_code=413, detail=f"a request may contain at most {MAX_SEQUENCE_BP} bp")
        ref_length = ref_lengths.get(segment.ref_name)
        if ref_length is None:
            raise HTTPException(status_code=404, detail=f"reference sequence not found: {segment.ref_name}")
        start, end = segment.start, segment.end
        clipped = False
        if start < 1 or end > ref_length:
            if not request.clip:
                raise HTTPException(status_code=416, detail="sequence interval is outside the reference")
            start, end = max(1, start), min(ref_length, end)
            if start > end:
                raise HTTPException(status_code=416, detail="sequence interval does not overlap the reference")
            clipped = True
        interval = (segment.ref_name, start, end)
        interval_index = interval_indexes.get(interval)
        if interval_index is None:
            interval_index = len(unique_intervals)
            interval_indexes[interval] = interval_index
            unique_intervals.append(interval)
        prepared.append(
            {
                "request": segment,
                "refLength": ref_length,
                "start": start,
                "end": end,
                "clipped": clipped,
                "intervalIndex": interval_index,
            }
        )

    sequences = run_faidx(reference, unique_intervals)
    records: list[dict[str, Any]] = []
    for item in prepared:
        segment = item["request"]
        sequence = sequences[item["intervalIndex"]].upper()
        if segment.strand == "-":
            sequence = reverse_complement(sequence)
        records.append(
            {
                "name": segment.name,
                "refName": segment.ref_name,
                "refLength": item["refLength"],
                "requestedStart": segment.start,
                "requestedEnd": segment.end,
                "start": item["start"],
                "end": item["end"],
                "strand": segment.strand,
                "clipped": item["clipped"],
                "length": len(sequence),
                "sequence": sequence,
            }
        )
    return {
        "assembly": request.assembly,
        "coordinateSystem": "1-based-inclusive",
        "requestedTotalLength": requested_total,
        "totalLength": sum(record["length"] for record in records),
        "records": records,
    }


def _run_limited_sequence_job(
    root: Path,
    assembly: dict[str, Any],
    request: SequenceBatchRequest,
) -> dict[str, Any]:
    try:
        return extract_sequence_batch(root, assembly, request)
    finally:
        _sequence_limiter.release()


def _consume_background_task_exception(task: asyncio.Task[dict[str, Any]]) -> None:
    if not task.cancelled():
        task.exception()


@router.head("/genomes/browser", include_in_schema=False)
@router.get("/genomes/browser", include_in_schema=False)
async def serve_genome_browser_index() -> FileResponse:
    index_path = STATIC_ROOT / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Genome browser frontend not found")
    return FileResponse(index_path)


@router.head("/genome-browser", include_in_schema=False)
@router.get("/genome-browser", include_in_schema=False)
async def redirect_legacy_genome_browser(request: Request) -> RedirectResponse:
    destination = "/genomes/browser"
    if request.url.query:
        destination = f"{destination}?{request.url.query}"
    return RedirectResponse(destination, status_code=308)


@router.head("/genomes", include_in_schema=False)
@router.get("/genomes", include_in_schema=False)
async def serve_genomes_index() -> FileResponse:
    index_path = GENOMES_STATIC_ROOT / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Genomes frontend not found")
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


@router.get("/api/genome-browser/features/resolve")
async def api_genome_browser_resolve_feature(
    assembly: str = Query(min_length=1, max_length=512),
    id: str = Query(min_length=1, max_length=1024),
) -> dict[str, Any]:
    root = get_db_root()
    manifest = load_manifest()
    find_assembly(manifest, assembly)
    index_path = default_index_path(root)
    try:
        return await asyncio.to_thread(
            resolve_feature,
            index_path,
            assembly_id=assembly,
            query_id=id,
        )
    except FeatureNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AmbiguousFeatureError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "feature alias is ambiguous",
                "candidates": [
                    {"type": feature_type, "id": feature_id}
                    for feature_type, feature_id in exc.candidates[:10]
                ],
            },
        ) from exc
    except (FeatureIndexError, OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="genome feature index is unavailable") from exc


@router.post("/api/genome-browser/sequences")
async def api_genome_browser_sequences(request: SequenceBatchRequest) -> dict[str, Any]:
    root = get_db_root()
    assembly = find_assembly(load_manifest(), request.assembly)
    if not _sequence_limiter.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="sequence extraction is busy",
            headers={"Retry-After": "1"},
        )
    try:
        worker = asyncio.create_task(
            asyncio.to_thread(_run_limited_sequence_job, root, assembly, request)
        )
    except BaseException:
        _sequence_limiter.release()
        raise
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        worker.add_done_callback(_consume_background_task_exception)
        raise
    except HTTPException:
        raise
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="sequence extraction timed out") from exc
    except (SequenceExtractionError, OSError) as exc:
        raise HTTPException(status_code=503, detail="sequence extraction failed") from exc


@router.head("/api/genome-browser/data/{file_path:path}", include_in_schema=False)
@router.get("/api/genome-browser/data/{file_path:path}", include_in_schema=False)
async def api_genome_browser_data(file_path: str) -> FileResponse:
    root = get_db_root()
    normalized_path = str(PurePosixPath(file_path))
    if normalized_path not in public_data_paths(load_manifest()):
        raise HTTPException(status_code=404, detail="file not found")
    target = resolve_under(root, file_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target, media_type=media_type_for_path(target))

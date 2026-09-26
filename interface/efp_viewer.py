import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from interface.bulk_rnaseq_viewer import load_expression, search_genes
from interface.subprocess_env import interface_subprocess_env


router = APIRouter()
STATIC_ROOT = Path(__file__).resolve().parent / "static" / "efp"
RENDER_SCRIPT = Path(__file__).resolve().parent / "render_efp.mjs"
RENDER_TIMEOUT_SECONDS = 20
_RENDER_SLOTS = threading.BoundedSemaphore(2)
Transform = Literal["log2_tpm", "tpm", "row_zscore"]
Gene = Annotated[str, Query(min_length=1, max_length=200)]


def validate_gene(gene: str) -> str:
    if not re.fullmatch(r"[!-~]+", gene) or re.search(r"[,;]", gene):
        raise HTTPException(400, "Provide one exact gene ID without whitespace, commas or semicolons")
    return gene


def render_expression(gene: str, transform: str, output_format: str) -> bytes:
    """Share the browser's adapter and vector writer without a browser or npm."""
    if not _RENDER_SLOTS.acquire(blocking=False):
        raise HTTPException(503, "eFP renderer is busy; retry shortly", headers={"Retry-After": "2"})
    try:
        data = load_expression(gene, "tissue", transform)
        node = os.environ.get("POTATO_EFP_NODE") or shutil.which("node")
        if not node:
            raise HTTPException(503, "eFP renderer requires Node.js 18 or newer on the Interface server")
        # Only the data needed by the shared adapter enters the subprocess.
        payload = {key: data[key] for key in (
            "dataset", "scope", "transform", "genes", "columns", "values", "rawValues",
        )}
        try:
            result = subprocess.run(
                [node, str(RENDER_SCRIPT)],
                input=json.dumps({"format": output_format, "data": payload}, allow_nan=False).encode(),
                capture_output=True,
                timeout=RENDER_TIMEOUT_SECONDS,
                env=interface_subprocess_env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "eFP rendering timed out") from exc
        except OSError as exc:
            raise HTTPException(503, "eFP renderer is unavailable") from exc
        if result.returncode:
            raise HTTPException(503, "eFP rendering failed; check server runtime, expression data and PDF assets")
        return result.stdout
    finally:
        _RENDER_SLOTS.release()


@router.get("/efp", include_in_schema=False)
async def serve_efp_index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@router.get("/api/efp/source")
async def api_efp_source() -> dict[str, Any]:
    """Describe the figure and expression sources without querying a gene."""
    return {
        "description": (
            "本图从 Tissue Expression Map（eFP）页面导出，数据使用 Gene Expression 页面 "
            "Tissue mean（组织平均表达）视图的平均组织表达水平。"
            "返回的图中，灰色表示该图形区域缺少可映射的组织表达数据；"
            "白色表示示意图未着色部分或背景，不参与色阶比较。"
        ),
        "figure": {
            "name": "Tissue Expression Map (eFP)",
            "page": "/efp",
            "exportEndpoint": "/api/efp/export.pdf",
            "format": "vector PDF",
        },
        "data": {
            "name": "Gene Expression",
            "page": "/bulk-rnaseq",
            "expressionEndpoint": "/api/bulk-rnaseq/expression",
            "scope": "tissue",
            "grouping": "Tissue mean",
            "unit": "TPM",
            "aggregation": "对图谱中同一组织的样本 TPM 取算术平均，跨材料汇总。",
            "displayNote": "图中按所选尺度显示平均 TPM、log2(平均 TPM + 1) 或其跨组织 Z-score。",
        },
    }


@router.get("/api/efp/genes")
async def api_efp_genes(
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Search available gene IDs; expression/export require an exact match."""
    return await asyncio.to_thread(search_genes, q, limit)


@router.get("/api/efp/expression")
async def api_efp_expression(gene: Gene, transform: Transform = "log2_tpm") -> dict[str, Any]:
    """Return the page's tissue values, region mapping, colors and NA state."""
    result = await asyncio.to_thread(render_expression, validate_gene(gene), transform, "json")
    try:
        return json.loads(result)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(503, "eFP renderer returned invalid expression data") from exc


@router.get("/api/efp/export.pdf", response_class=Response, responses={
    200: {"content": {"application/pdf": {}}, "description": "Editable vector tissue expression map"},
})
async def api_efp_pdf(gene: Gene, transform: Transform = "log2_tpm") -> Response:
    """Export the full diagram, legend, tissue table and expression metadata."""
    gene = validate_gene(gene)
    pdf = await asyncio.to_thread(render_expression, gene, transform, "pdf")
    if not pdf.startswith(b"%PDF-1.4"):
        raise HTTPException(503, "eFP renderer returned an invalid PDF")
    filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", gene) + f"_efp_{transform}.pdf"
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    })

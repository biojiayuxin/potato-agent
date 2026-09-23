"""Run an isolated eFP preview, optionally using an existing public expression API."""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from interface.efp_viewer import router


def create_preview_app(api_origin: str = "") -> FastAPI:
    app = FastAPI(title="Tissue Expression Map preview")
    static = Path(__file__).resolve().parent / "static"
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse("/efp")

    @app.get("/api/announcement")
    async def announcement() -> dict:
        return {"announcement": None}

    if api_origin:
        parsed = urlsplit(api_origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("api_origin must be an HTTP(S) origin without credentials or a path")

        @app.get("/api/bulk-rnaseq/{endpoint}")
        async def expression_proxy(endpoint: str, request: Request) -> Response:
            if endpoint not in {"status", "genes", "expression"}:
                raise HTTPException(404)
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
                    upstream = await client.get(
                        f"{api_origin.rstrip('/')}/api/bulk-rnaseq/{endpoint}",
                        params=request.query_params,
                    )
            except httpx.HTTPError as exc:
                raise HTTPException(503, "Expression API unavailable") from exc
            return Response(upstream.content, status_code=upstream.status_code, media_type="application/json")

        @app.get("/bulk-rnaseq", include_in_schema=False)
        async def gene_expression() -> FileResponse:
            return FileResponse(static / "bulk_rnaseq" / "index.html")
    else:
        from interface.bulk_rnaseq_viewer import router as expression_router

        app.include_router(expression_router)

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3011)
    parser.add_argument("--api-origin", default="", help="Existing public Interface origin; only expression GET requests are forwarded")
    args = parser.parse_args()
    uvicorn.run(create_preview_app(args.api_origin), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()

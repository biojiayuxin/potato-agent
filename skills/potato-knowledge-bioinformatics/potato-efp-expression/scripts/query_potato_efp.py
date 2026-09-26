#!/usr/bin/env python3
"""Thin, standard-library client for the Potato Interface eFP API."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "https://potato-agent.ynnu.edu.cn"
INSTALL_BASE_URL_FILE = Path(__file__).resolve().parents[1] / "api-base-url.txt"


def default_base_url() -> str:
    override = os.getenv("POTATO_EFP_BASE_URL")
    if override:
        return override
    if INSTALL_BASE_URL_FILE.is_file():
        return INSTALL_BASE_URL_FILE.read_text(encoding="utf-8").strip()
    return DEFAULT_BASE_URL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=default_base_url())
    common.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds (1–600).")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("source", parents=[common], help="Describe the figure and expression data sources.")
    search = commands.add_parser("search", parents=[common], help="Search for an exact gene ID.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    for name in ("query", "plot"):
        command = commands.add_parser(name, parents=[common])
        command.add_argument("gene")
        command.add_argument("--transform", choices=("log2_tpm", "tpm", "row_zscore"), default="log2_tpm")
        if name == "plot":
            command.add_argument("--output", type=Path, required=True, help="Destination .pdf; must not exist.")
    return parser


def api_root(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") not in {"", "/efp", "/api/efp"}):
        raise ValueError("--base-url must be an HTTP(S) site, /efp page or /api/efp root without credentials")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/api/efp", "", ""))


def run(args: argparse.Namespace) -> dict:
    if not 1 <= args.timeout <= 600:
        raise ValueError("--timeout must be between 1 and 600 seconds")
    if args.command == "source":
        endpoint, params = "source", {}
    elif args.command == "search":
        if not 1 <= args.limit <= 100:
            raise ValueError("--limit must be between 1 and 100")
        endpoint, params = "genes", {"q": args.query, "limit": args.limit}
    else:
        if not args.gene or len(args.gene) > 200 or any(c.isspace() or c in ",;" for c in args.gene):
            raise ValueError("Provide one exact gene ID without whitespace, commas or semicolons")
        endpoint = "export.pdf" if args.command == "plot" else "expression"
        params = {"gene": args.gene, "transform": args.transform}
    output = None
    if args.command == "plot":
        output = args.output.expanduser().absolute()
        if output.suffix.lower() != ".pdf":
            raise ValueError("--output must end in .pdf")
        if output.exists() or output.is_symlink():
            raise ValueError("Output already exists; choose another --output")
    url = api_root(args.base_url) + "/" + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Accept": "application/pdf" if output else "application/json",
        "User-Agent": "Potato-eFP-skill/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            content_type = response.headers.get_content_type()
            body = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", exc.reason)
        except (ValueError, AttributeError):
            detail = exc.reason
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    if output:
        if content_type != "application/pdf" or not body.startswith(b"%PDF-") or not body.rstrip().endswith(b"%%EOF"):
            raise RuntimeError("The eFP API did not return a complete PDF")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            try:
                handle.write(body)
            except OSError:
                output.unlink()
                raise
        return {"api_url": url, "geneId": args.gene, "transform": args.transform,
                "scope": "tissue", "pdf": str(output.resolve()), "bytes": len(body),
                "source_url": api_root(args.base_url) + "/source"}
    if content_type != "application/json":
        raise RuntimeError("The eFP API did not return JSON")
    return {"api_url": url, "data": json.loads(body)}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"eFP: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

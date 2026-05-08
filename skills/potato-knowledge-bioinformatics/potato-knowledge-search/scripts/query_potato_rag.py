#!/usr/bin/env python3
"""Query the deployed Potato Knowledge Hub RAG API.

The script intentionally depends only on the Python standard library so it can be
used from a fresh Hermes skill installation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://www.potato-ai.top"
DEFAULT_TOP_K_RETRIEVE = 200
DEFAULT_TOP_K_RERANK = 20
TIMEOUT_SECONDS = 120


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query the Potato Knowledge Hub RAG API and print retrieved literature evidence."
    )
    parser.add_argument("query", help="Text to retrieve from the RAG literature index.")
    parser.add_argument(
        "--top-k-retrieve",
        type=int,
        default=DEFAULT_TOP_K_RETRIEVE,
        help=f"Number of vector candidates to retrieve. Default: {DEFAULT_TOP_K_RETRIEVE}.",
    )
    parser.add_argument(
        "--top-k-rerank",
        type=int,
        default=DEFAULT_TOP_K_RERANK,
        help=f"Number of reranked results to return. Default: {DEFAULT_TOP_K_RERANK}.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POTATO_RAG_BASE_URL", DEFAULT_BASE_URL),
        help=(
            "Base URL for the deployed service. Can also be set with "
            f"POTATO_RAG_BASE_URL. Default: {DEFAULT_BASE_URL}."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds. Default: {TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "summary", "tsv"),
        default="json",
        help="Output format. Default: json.",
    )
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=700,
        help="Maximum snippet characters per result in summary output. Default: 700.",
    )
    return parser


def post_json(url: str, payload: dict[str, object], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from RAG API: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to connect to RAG API: {exc.reason}") from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"RAG API returned non-JSON response: {response_body[:500]}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("RAG API returned JSON that is not an object")
    return data


def normalize_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    results = data.get("results", [])
    if results is None:
        return []
    if not isinstance(results, list):
        raise RuntimeError("RAG API returned 'results' that is not a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row.setdefault("rank", index)
        normalized.append(row)
    return normalized


def truncate_text(value: object, max_chars: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def format_summary(data: dict[str, Any], max_text_chars: int) -> str:
    query = data.get("query", "")
    results = normalize_results(data)
    lines = [f"Query: {query}", f"Results: {len(results)}"]
    for row in results:
        rank = row.get("rank", "")
        score = row.get("score", "")
        title = row.get("title") or "未返回标题"
        doi = row.get("doi") or "未返回 DOI"
        snippet = truncate_text(row.get("text"), max_text_chars)
        lines.extend(
            [
                "",
                f"[{rank}] {title}",
                f"DOI: {doi}",
                f"Score: {score}",
                "Text:",
                textwrap.fill(snippet, width=100, replace_whitespace=False),
            ]
        )
    return "\n".join(lines)


def format_tsv(data: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["rank", "score", "doi", "title", "text"],
        delimiter="\t",
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in normalize_results(data):
        writer.writerow(
            {
                "rank": row.get("rank", ""),
                "score": row.get("score", ""),
                "doi": row.get("doi", ""),
                "title": row.get("title", ""),
                "text": truncate_text(row.get("text"), 0),
            }
        )
    return output.getvalue().rstrip("\n")


def positive_int(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    query = args.query.strip()
    if not query:
        print("error: query must not be empty", file=sys.stderr)
        return 2
    try:
        positive_int("--top-k-retrieve", args.top_k_retrieve)
        positive_int("--top-k-rerank", args.top_k_rerank)
        positive_int("--timeout", args.timeout)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.max_text_chars < 0:
        print("error: --max-text-chars must be non-negative", file=sys.stderr)
        return 2

    base_url = args.base_url.rstrip("/")
    endpoint = f"{base_url}/api/rag/search"
    payload = {
        "query": query,
        "top_k_retrieve": args.top_k_retrieve,
        "top_k_rerank": args.top_k_rerank,
    }

    try:
        data = post_json(endpoint, payload, args.timeout)
        normalize_results(data)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if data.get("success") is False:
        print(f"error: RAG API reported failure: {data.get('error', data)}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.format == "summary":
        print(format_summary(data, args.max_text_chars))
    elif args.format == "tsv":
        print(format_tsv(data))
    else:  # defensive; argparse enforces choices
        print(f"error: unsupported format: {args.format}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

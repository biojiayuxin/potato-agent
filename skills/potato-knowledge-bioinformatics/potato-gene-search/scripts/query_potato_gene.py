#!/usr/bin/env python3
"""Query Potato Knowledge Hub gene APIs and print JSON for AI use."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://www.potato-ai.top"
TIMEOUT_SECONDS = 60
COMMANDS = {"search", "details"}
DETAIL_RESPONSE_FIELDS_TO_DROP = {"ls_exp"}


def common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POTATO_GENE_BASE_URL", DEFAULT_BASE_URL),
        help=f"Base URL for Potato Knowledge Hub. Default: {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds. Default: {TIMEOUT_SECONDS}.",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = common_parser()
    parser = argparse.ArgumentParser(
        description="Query Potato Knowledge Hub gene APIs and print JSON for AI use.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command")

    search = subparsers.add_parser(
        "search",
        parents=[common],
        help="Search genes by DMv8.2 ID, symbol, reported ID, or partial query.",
    )
    search.add_argument("query", help="Gene search query, such as PYL8 or LOC102580526.")

    details = subparsers.add_parser(
        "details",
        parents=[common],
        help="Fetch details for one DMv8.2 gene ID.",
    )
    details.add_argument("gene_id", help="DMv8.2 gene ID, such as DM8.2_chr06G09000.")
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] in {"-h", "--help"}:
        return argv
    if any(arg in COMMANDS for arg in argv):
        return argv
    return ["search", *argv]


def request_json(base_url: str, path: str, params: dict[str, str], timeout: int) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}{path}?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from Potato gene API: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to connect to Potato gene API: {exc.reason}") from exc

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Potato gene API returned non-JSON response: {response_body[:500]}") from exc


def run_search(args: argparse.Namespace) -> Any:
    query = args.query.strip()
    if not query:
        raise ValueError("query must not be empty")
    return request_json(args.base_url, "/api/gene_search", {"q": query}, args.timeout)


def filter_details_response(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return {
        field: value
        for field, value in data.items()
        if field not in DETAIL_RESPONSE_FIELDS_TO_DROP
    }


def run_details(args: argparse.Namespace) -> Any:
    gene_id = args.gene_id.strip()
    if not gene_id:
        raise ValueError("gene_id must not be empty")
    data = request_json(args.base_url, "/api/gene_details", {"id": gene_id}, args.timeout)
    return filter_details_response(data)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv if argv is not None else sys.argv[1:]))

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        if args.command == "search":
            data = run_search(args)
        elif args.command == "details":
            data = run_details(args)
        else:
            parser.error(f"unknown command: {args.command}")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

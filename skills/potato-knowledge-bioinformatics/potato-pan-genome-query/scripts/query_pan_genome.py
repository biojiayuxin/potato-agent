#!/usr/bin/env python3
"""Query the public Potato Agent pan-genome Orthogroups API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://potato-agent.ynnu.edu.cn"
DEFAULT_TIMEOUT = 60
MAX_MEMBER_LIMIT = 1_000
MAX_MEMBER_OFFSET = 100_000
MAX_ORTHOGROUP_LIMIT = 1_000
MAX_ORTHOGROUP_OFFSET = 250_000
ORTHOGROUP_CATEGORIES = ("core", "soft-core", "dispensable", "private")


def bounded_int(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"expected an integer, got {value!r}"
            ) from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"expected a value from {minimum} through {maximum}, got {number}"
            )
        return number

    return parse


def nonempty_identifier(value: str) -> str:
    identifier = value.strip()
    if not identifier:
        raise argparse.ArgumentTypeError("identifier must not be empty")
    if len(identifier) > 1_024:
        raise argparse.ArgumentTypeError("identifier exceeds 1024 characters")
    return identifier


def common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POTATO_PAN_GENOME_BASE_URL", DEFAULT_BASE_URL),
        help=(
            "Potato Agent site root or pan-genome API root. "
            f"Default: {DEFAULT_BASE_URL}"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=bounded_int(1, 600),
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds. Default: {DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--indent",
        type=bounded_int(0, 8),
        default=2,
        help="JSON indentation; use 0 for compact JSON. Default: 2",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query the Potato Agent pan-genome Orthogroups API."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = common_parser()

    metadata = subparsers.add_parser(
        "metadata", parents=[common], help="Show the active dataset version and counts."
    )
    metadata.set_defaults(func=run_metadata)

    genomes = subparsers.add_parser(
        "genomes", parents=[common], help="List all genomes and membership counts."
    )
    genomes.set_defaults(func=run_genomes)

    gene = subparsers.add_parser(
        "gene", parents=[common], help="Look up an exact gene ID."
    )
    gene.add_argument("gene_id", type=nonempty_identifier, help="Exact gene ID.")
    gene.add_argument(
        "--genome",
        type=nonempty_identifier,
        help="Optional exact genome name used to disambiguate the gene ID.",
    )
    gene.set_defaults(func=run_gene)

    orthogroups = subparsers.add_parser(
        "orthogroups",
        parents=[common],
        help="List orthogroups, optionally filtered by pan-genome category.",
    )
    orthogroups.add_argument(
        "--category",
        choices=ORTHOGROUP_CATEGORIES,
        help="Optional category: core, soft-core, dispensable, or private.",
    )
    orthogroups.add_argument(
        "--limit",
        type=bounded_int(1, MAX_ORTHOGROUP_LIMIT),
        default=100,
        help=(
            "Maximum orthogroups returned on this page, used to limit agent "
            "context size; it is not the category's total count. Read "
            "pagination.total for the complete count. "
            f"Default: 100; maximum: {MAX_ORTHOGROUP_LIMIT}"
        ),
    )
    orthogroups.add_argument(
        "--offset",
        type=bounded_int(0, MAX_ORTHOGROUP_OFFSET),
        default=0,
        help=f"Pagination offset. Default: 0; maximum: {MAX_ORTHOGROUP_OFFSET}",
    )
    orthogroups.set_defaults(func=run_orthogroups)

    orthogroup = subparsers.add_parser(
        "orthogroup",
        parents=[common],
        help="Show an orthogroup summary and per-genome member counts.",
    )
    orthogroup.add_argument(
        "orthogroup", type=nonempty_identifier, help="Exact orthogroup ID."
    )
    orthogroup.set_defaults(func=run_orthogroup)

    members = subparsers.add_parser(
        "members", parents=[common], help="List paginated orthogroup members."
    )
    members.add_argument(
        "orthogroup", type=nonempty_identifier, help="Exact orthogroup ID."
    )
    members.add_argument(
        "--genome",
        type=nonempty_identifier,
        help="Optional exact genome name used to filter members.",
    )
    members.add_argument(
        "--limit",
        type=bounded_int(1, MAX_MEMBER_LIMIT),
        default=100,
        help=f"Members returned per request. Default: 100; maximum: {MAX_MEMBER_LIMIT}",
    )
    members.add_argument(
        "--offset",
        type=bounded_int(0, MAX_MEMBER_OFFSET),
        default=0,
        help=f"Pagination offset. Default: 0; maximum: {MAX_MEMBER_OFFSET}",
    )
    members.set_defaults(func=run_members)
    return parser


def api_root(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid Potato pan-genome base URL: {base_url!r}")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/pan-genome"):
        api_path = path
    else:
        api_path = f"{path}/api/pan-genome"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, api_path, "", ""))


def request_json(
    base_url: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    url = f"{api_root(base_url)}/{endpoint.lstrip('/')}"
    query = urllib.parse.urlencode(params or {}, doseq=True)
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "potato-pan-genome-query/1.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
            detail = parsed.get("detail", body) if isinstance(parsed, dict) else body
        except json.JSONDecodeError:
            detail = body
        raise RuntimeError(
            f"Pan-genome API returned HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Failed to connect to Potato pan-genome API: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            "Timed out while connecting to Potato pan-genome API"
        ) from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Pan-genome API returned non-JSON data: {body[:500]}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Pan-genome API returned JSON that is not an object")
    return url, payload


def run_metadata(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    return request_json(args.base_url, "metadata", params=None, timeout=args.timeout)


def run_genomes(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    return request_json(args.base_url, "genomes", params=None, timeout=args.timeout)


def run_gene(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    params = {"gene_id": args.gene_id}
    if args.genome:
        params["genome"] = args.genome
    return request_json(
        args.base_url, "genes/lookup", params=params, timeout=args.timeout
    )


def run_orthogroups(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {"limit": args.limit, "offset": args.offset}
    if args.category:
        params["category"] = args.category
    return request_json(
        args.base_url, "orthogroups", params=params, timeout=args.timeout
    )


def run_orthogroup(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    identifier = urllib.parse.quote(args.orthogroup, safe="")
    return request_json(
        args.base_url, f"orthogroups/{identifier}", params=None, timeout=args.timeout
    )


def run_members(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    identifier = urllib.parse.quote(args.orthogroup, safe="")
    params: dict[str, Any] = {"limit": args.limit, "offset": args.offset}
    if args.genome:
        params["genome"] = args.genome
    return request_json(
        args.base_url,
        f"orthogroups/{identifier}/members",
        params=params,
        timeout=args.timeout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        api_url, payload = args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result = {"api_url": api_url, "data": payload}
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.indent == 0 else args.indent,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Query Potato Knowledge Hub gene search and gene detail APIs.

By default, the details command omits long sequence fields
(cds/pep/genomic/promoter) and reports compact sequence metadata instead.
Use --include-sequences only when the user explicitly asks for full sequences.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://www.potato-ai.top"
TIMEOUT_SECONDS = 60
COMMANDS = {"search", "details"}
SEQUENCE_FIELDS = ("cds", "pep", "genomic", "promoter")
DMV8_GENE_RE = re.compile(r"^DM8C\d{2}G\d{5}$", re.IGNORECASE)


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
        description="Query Potato Knowledge Hub gene APIs.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command")

    search = subparsers.add_parser(
        "search",
        parents=[common],
        help="Search genes by DMv8 ID, symbol, reported ID, or partial query.",
    )
    search.add_argument("query", help="Gene search query, such as PYL8 or LOC102580526.")
    search.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Keep only the first N search hits in the output.",
    )

    details = subparsers.add_parser(
        "details",
        parents=[common],
        help="Fetch details for one DMv8 gene ID. Full sequence fields are omitted by default.",
    )
    details.add_argument("gene_id", help="DMv8 gene ID, such as DM8C06G10190.")
    details.add_argument(
        "--include-sequences",
        action="store_true",
        help="Include full sequence fields. Use only when the user explicitly asks for sequences.",
    )
    details.add_argument(
        "--sequence-fields",
        default=",".join(SEQUENCE_FIELDS),
        help=(
            "Comma-separated sequence fields to include when --include-sequences is set. "
            f"Allowed: {', '.join(SEQUENCE_FIELDS)}. Default: all."
        ),
    )
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] in {"-h", "--help"}:
        return argv
    if any(arg in COMMANDS for arg in argv):
        return argv
    return ["search", *argv]


def request_json(base_url: str, path: str, params: dict[str, str], timeout: int) -> dict[str, Any]:
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
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Potato gene API returned non-JSON response: {response_body[:500]}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("Potato gene API returned JSON that is not an object")
    if "error" in data:
        raise RuntimeError(str(data["error"]))
    return data


def parse_ref_info(data: dict[str, Any]) -> None:
    ref_info = data.get("ref_info")
    if not isinstance(ref_info, str) or not ref_info.strip():
        return
    try:
        parsed = json.loads(ref_info)
    except json.JSONDecodeError:
        return
    if isinstance(parsed, list):
        data["ref_info_parsed"] = parsed


def fasta_summary(value: Any) -> dict[str, Any]:
    """Return compact metadata for a FASTA-like string without exposing sequence."""
    if not isinstance(value, str) or not value.strip():
        return {"present": False, "length": 0}

    lines = value.splitlines()
    header = lines[0].strip() if lines and lines[0].startswith(">") else None
    seq_lines = [line.strip() for line in lines if line.strip() and not line.startswith(">")]
    sequence = "".join(seq_lines)
    return {
        "present": True,
        "header": header,
        "length": len(sequence),
        "line_count": len(lines),
    }


def parse_sequence_fields(raw_fields: str) -> set[str]:
    requested = {field.strip() for field in raw_fields.split(",") if field.strip()}
    unknown = requested - set(SEQUENCE_FIELDS)
    if unknown:
        raise ValueError(
            "unknown sequence field(s): "
            + ", ".join(sorted(unknown))
            + f". Allowed: {', '.join(SEQUENCE_FIELDS)}"
        )
    return requested


def omit_sequence_fields(data: dict[str, Any], include_sequences: bool, sequence_fields: set[str]) -> None:
    summary: dict[str, Any] = {}
    omitted: list[str] = []

    for field in SEQUENCE_FIELDS:
        if field in data:
            summary[field] = fasta_summary(data.get(field))

        should_include = include_sequences and field in sequence_fields
        if not should_include and field in data:
            data.pop(field, None)
            omitted.append(field)

    if summary:
        data["sequence_summary"] = summary
    if omitted:
        data["sequence_fields_omitted"] = omitted
        data["sequence_omission_note"] = (
            "Full cds/pep/genomic/promoter sequence fields are omitted by default. "
            "Re-run details with --include-sequences when the user explicitly asks for them."
        )


def normalize_gene_id(gene_id: str) -> str:
    gene_id = gene_id.strip()
    if not gene_id:
        raise ValueError("gene_id must not be empty")
    if DMV8_GENE_RE.match(gene_id):
        return gene_id.upper()
    return gene_id


def run_search(args: argparse.Namespace) -> dict[str, Any]:
    query = args.query.strip()
    if not query:
        raise ValueError("query must not be empty")
    data = request_json(args.base_url, "/api/gene_search", {"q": query}, args.timeout)
    results = data.setdefault("results", [])
    if not isinstance(results, list):
        raise RuntimeError("Potato gene API search response field 'results' is not a list")
    if args.max_results is not None:
        if args.max_results < 0:
            raise ValueError("--max-results must be >= 0")
        data["results"] = results[: args.max_results]
    data["endpoint"] = "gene_search"
    data["query"] = query
    data["result_count"] = len(results)
    return data


def run_details(args: argparse.Namespace) -> dict[str, Any]:
    gene_id = normalize_gene_id(args.gene_id)
    sequence_fields = parse_sequence_fields(args.sequence_fields)
    data = request_json(args.base_url, "/api/gene_details", {"id": gene_id}, args.timeout)
    parse_ref_info(data)
    omit_sequence_fields(data, args.include_sequences, sequence_fields)
    data["endpoint"] = "gene_details"
    data["gene_id"] = gene_id
    data["full_sequences_included"] = bool(args.include_sequences)
    if args.include_sequences:
        data["included_sequence_fields"] = sorted(sequence_fields)
    return data


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

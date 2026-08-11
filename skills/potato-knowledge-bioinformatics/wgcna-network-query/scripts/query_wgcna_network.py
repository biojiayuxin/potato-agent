#!/usr/bin/env python3
"""Query the deployed Potato Agent WGCNA network API."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence


API_ROOT = "https://potato-agent.ynnu.edu.cn/api/wgcna"
DEFAULT_TIMEOUT = 60
NETWORKS = ("leaf", "stem", "root", "reproductive", "tuberization")
MAX_QUERY_GENES = 20
MAX_TOP_N = 500
MAX_TOTAL_EDGES = 10_000
GENE_ID_PATTERN = re.compile(r"DM8\.2_chr\d{2}G[0-9A-Za-z_-]+")


def bounded_int(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"expected a value from {minimum} through {maximum}, got {number}"
            )
        return number

    return parse


def tom_value(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from exc
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError("TOM minimum must be between 0 and 1")
    return number


def exact_gene_id(value: str) -> str:
    gene_id = value.strip()
    if not GENE_ID_PATTERN.fullmatch(gene_id):
        raise argparse.ArgumentTypeError(
            f"expected an exact DMv8.2 gene ID such as DM8.2_chr09G24280, got {value!r}"
        )
    return gene_id


def common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
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
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Also write the complete result object to this JSON file.",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Query the public Potato Agent WGCNA API at "
            "https://potato-agent.ynnu.edu.cn/api/wgcna."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = common_parser()

    status = subparsers.add_parser(
        "status", parents=[common], help="Show deployed WGCNA datasets and row counts."
    )
    status.set_defaults(func=run_status)

    search = subparsers.add_parser(
        "search", parents=[common], help="Search deployed WGCNA gene IDs and names."
    )
    search.add_argument("query", help="Gene ID or gene-name fragment.")
    search.add_argument(
        "--limit", type=bounded_int(1, 100), default=20, help="Maximum matches. Default: 20"
    )
    search.set_defaults(func=run_search)

    gene = subparsers.add_parser(
        "gene", parents=[common], help="Show one gene's membership in every network."
    )
    gene.add_argument("gene_id", type=exact_gene_id, help="Exact DMv8.2 gene ID.")
    gene.set_defaults(func=run_gene)

    module = subparsers.add_parser(
        "module", parents=[common], help="Show module hubs and cross-network overlaps."
    )
    module.add_argument("network", choices=NETWORKS, help="WGCNA network ID.")
    module.add_argument("module", help="Exact module color/name, for example red.")
    module.set_defaults(func=run_module)

    coexpression = subparsers.add_parser(
        "coexpression", parents=[common], help="Query TOM-ranked co-expression neighbors."
    )
    coexpression.add_argument(
        "genes",
        nargs="+",
        help=f"Exact DMv8.2 IDs, separated by spaces or commas (maximum {MAX_QUERY_GENES}).",
    )
    coexpression.add_argument(
        "--network",
        action="append",
        choices=NETWORKS,
        help="Network to query; repeat for multiple networks. Default: all networks.",
    )
    coexpression.add_argument(
        "--top-n",
        type=bounded_int(1, MAX_TOP_N),
        default=50,
        help=f"Top TOM neighbors per query gene and network. Default: 50; maximum: {MAX_TOP_N}",
    )
    coexpression.add_argument(
        "--tom-min", type=tom_value, help="Optional minimum TOM similarity from 0 through 1."
    )
    coexpression.add_argument(
        "--all-modules",
        action="store_true",
        help="Allow neighbors outside each query gene's assigned module.",
    )
    coexpression.add_argument(
        "--no-neighbor-edges",
        action="store_true",
        help="Exclude TOM edges among the returned neighbor nodes.",
    )
    coexpression.add_argument(
        "--no-cross-network",
        action="store_true",
        help="Exclude same-gene display links across selected networks.",
    )
    coexpression.add_argument(
        "--include-module-overlaps",
        action="store_true",
        help="Include cross-network module-overlap statistics. Interface default: excluded.",
    )
    coexpression.add_argument(
        "--no-shared-edges",
        action="store_true",
        help="Exclude annotations for pairs recurring across networks.",
    )
    coexpression.add_argument(
        "--max-total-edges",
        type=bounded_int(1, MAX_TOTAL_EDGES),
        default=3000,
        help=f"Total TOM-edge cap. Default: 3000; maximum: {MAX_TOTAL_EDGES}",
    )
    coexpression.set_defaults(func=run_coexpression)
    return parser


def request_json(
    endpoint: str,
    *,
    params: dict[str, Any] | None,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    url = f"{API_ROOT}/{endpoint.lstrip('/')}"
    query = urllib.parse.urlencode(params or {}, doseq=True)
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "potato-wgcna-network-query/1.0",
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
        raise RuntimeError(f"WGCNA API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to connect to deployed WGCNA API: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Timed out while connecting to deployed WGCNA API") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"WGCNA API returned non-JSON data: {body[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("WGCNA API returned JSON that is not an object")
    return url, payload


def parse_gene_list(values: Sequence[str]) -> list[str]:
    genes: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in re.split(r"[\s,;]+", value.strip()):
            if not token:
                continue
            gene_id = exact_gene_id(token)
            key = gene_id.casefold()
            if key not in seen:
                seen.add(key)
                genes.append(gene_id)
    if not genes:
        raise ValueError("at least one gene ID is required")
    if len(genes) > MAX_QUERY_GENES:
        raise ValueError(f"at most {MAX_QUERY_GENES} unique query genes are supported")
    return genes


def run_status(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    return request_json("status", params=None, timeout=args.timeout)


def run_search(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    query = args.query.strip()
    if not query:
        raise ValueError("search query must not be empty")
    return request_json("genes", params={"q": query, "limit": args.limit}, timeout=args.timeout)


def run_gene(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    encoded_gene = urllib.parse.quote(args.gene_id, safe="")
    return request_json(f"gene/{encoded_gene}", params=None, timeout=args.timeout)


def run_module(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    module = args.module.strip()
    if not module:
        raise ValueError("module must not be empty")
    endpoint = "module/{}/{}".format(
        urllib.parse.quote(args.network, safe=""),
        urllib.parse.quote(module, safe=""),
    )
    return request_json(endpoint, params=None, timeout=args.timeout)


def run_coexpression(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    genes = parse_gene_list(args.genes)
    selected_networks = list(dict.fromkeys(args.network or []))
    params = {
        "genes": ",".join(genes),
        "networks": ",".join(selected_networks) if selected_networks else "all",
        "top_n": args.top_n,
        "same_module_only": str(not args.all_modules).lower(),
        "include_neighbor_edges": str(not args.no_neighbor_edges).lower(),
        "include_cross_network": str(not args.no_cross_network).lower(),
        "include_module_overlaps": str(args.include_module_overlaps).lower(),
        "include_shared_edges": str(not args.no_shared_edges).lower(),
        "max_total_edges": args.max_total_edges,
    }
    if args.tom_min is not None:
        params["tom_min"] = args.tom_min
    return request_json("coexpression", params=params, timeout=args.timeout)


def emit_result(
    *,
    api_url: str,
    payload: dict[str, Any],
    indent: int,
    output_json: Path | None,
) -> None:
    result = {"api_url": api_url, "data": payload}
    rendered = json.dumps(
        result,
        ensure_ascii=False,
        indent=None if indent == 0 else indent,
        sort_keys=False,
    )
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        api_url, payload = args.func(args)
        emit_result(
            api_url=api_url,
            payload=payload,
            indent=args.indent,
            output_json=args.output_json,
        )
    except (RuntimeError, ValueError, argparse.ArgumentTypeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

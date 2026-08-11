#!/usr/bin/env python3
"""Query the Potato Agent Bulk RNA-Seq expression API."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence


DEFAULT_BASE_URL = "https://potato-agent.ynnu.edu.cn"
DEFAULT_TIMEOUT = 60
MAX_QUERY_GENES = 50
SCOPES = ("sample_tissue", "tissue", "sample_name", "sample")
TRANSFORMS = ("log2_tpm", "row_zscore", "tpm")
SCOPE_LABELS = {
    "sample_tissue": "Material by tissue",
    "tissue": "Tissue mean",
    "sample_name": "Material mean",
    "sample": "All runs",
}
TRANSFORM_LABELS = {
    "log2_tpm": "log2(TPM + 1)",
    "row_zscore": "Row z-score",
    "tpm": "TPM",
}
TSV_FIELDS = (
    "dataset",
    "scope",
    "grouping_label",
    "transform",
    "scale_label",
    "gene_id",
    "transcript_id",
    "gene_name",
    "column_id",
    "column_label",
    "sample_name",
    "tissue",
    "value",
    "mean_tpm",
    "sd_tpm",
    "n",
)


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POTATO_BULK_RNASEQ_BASE_URL", DEFAULT_BASE_URL),
        help=(
            "Potato Agent site root, Bulk RNA-Seq page URL, or API root. "
            f"Default: {DEFAULT_BASE_URL}"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds. Default: {DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation; use 0 for compact JSON. Default: 2",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query expression from the Potato Agent Bulk RNA-Seq API."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = common_parser()

    status = subparsers.add_parser(
        "status",
        parents=[common],
        help="Show the active dataset and available grouping counts.",
    )
    status.set_defaults(func=run_status)

    search = subparsers.add_parser(
        "search",
        parents=[common],
        help="Search API gene IDs and gene names.",
    )
    search.add_argument("query", help="Gene ID or gene-name fragment.")
    search.add_argument("--limit", type=int, default=20, help="Maximum matches (1-100).")
    search.set_defaults(func=run_search)

    query = subparsers.add_parser(
        "query",
        parents=[common],
        help="Query expression for one or more exact gene IDs.",
    )
    query.add_argument(
        "genes",
        nargs="+",
        help=(
            "One or more DMv8.2 gene IDs, for example DM8.2_chr06G22780; "
            f"separate multiple IDs with spaces or commas (maximum {MAX_QUERY_GENES})."
        ),
    )
    query.add_argument(
        "--scope",
        "--grouping",
        dest="scope",
        choices=SCOPES,
        default="sample_tissue",
        help="Grouping mode. Default: sample_tissue",
    )
    query.add_argument(
        "--transform",
        "--scale",
        dest="transform",
        choices=TRANSFORMS,
        default="log2_tpm",
        help="Expression scale. Default: log2_tpm",
    )
    query.add_argument(
        "--tissue",
        action="append",
        help="Keep an exact tissue name; repeat to select multiple tissues.",
    )
    query.add_argument(
        "--sample-name",
        action="append",
        help="Keep an exact material/sample name; repeat to select multiple names.",
    )
    query.add_argument(
        "--top",
        type=int,
        default=10,
        help="Rows shown per gene, ordered by selected scale; use 0 for all. Default: 10",
    )
    query.add_argument(
        "--output-tsv",
        help="Write every selected expression row to a TSV file.",
    )
    query.add_argument(
        "--raw-response",
        action="store_true",
        help="Print the unmodified API response instead of the compact result.",
    )
    query.set_defaults(func=run_query)
    return parser


def api_root(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid Bulk RNA-Seq base URL: {base_url!r}")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/bulk-rnaseq"):
        api_path = path
    else:
        if path.endswith("/bulk-rnaseq"):
            path = path[: -len("/bulk-rnaseq")]
        api_path = f"{path}/api/bulk-rnaseq"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, api_path, "", ""))


def request_json(
    base_url: str,
    endpoint: str,
    params: dict[str, Any] | None,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    root = api_root(base_url)
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{root}/{endpoint.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "potato-expression-atlas-query/2.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("detail", body)
        except (json.JSONDecodeError, AttributeError):
            detail = body
        raise RuntimeError(f"Bulk RNA-Seq API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to connect to Bulk RNA-Seq API: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Bulk RNA-Seq API returned non-JSON data: {body[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Bulk RNA-Seq API returned JSON that is not an object")
    return url, payload


def print_json(payload: dict[str, Any], indent: int) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=None if indent == 0 else indent))


def normalize_gene_id(gene_id: str) -> str:
    value = gene_id.strip()
    match = re.fullmatch(r"DM8C(\d{1,2})G([0-9A-Za-z_-]+)(?:\.(\d+))?", value, re.I)
    if match:
        return f"DM8.2_chr{match.group(1).zfill(2)}G{match.group(2)}"
    match = re.fullmatch(r"(DM8(?:\.2)?_chr\d{1,2}G[0-9A-Za-z_-]+)(?:\.\d+)?", value, re.I)
    if match:
        return match.group(1)
    return value


def parse_genes(values: Sequence[str]) -> list[str]:
    genes: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in re.split(r"[\s,;]+", value.strip()):
            gene_id = normalize_gene_id(token)
            if not gene_id or gene_id in seen:
                continue
            genes.append(gene_id)
            seen.add(gene_id)
    if not genes:
        raise ValueError("At least one gene ID is required")
    if len(genes) > MAX_QUERY_GENES:
        raise ValueError(f"At most {MAX_QUERY_GENES} gene IDs may be queried at once")
    return genes


def as_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def matrix_value(matrix: Any, row_index: int, column_index: int, default: Any) -> Any:
    try:
        return matrix[row_index][column_index]
    except (IndexError, TypeError):
        return default


def exact_match(value: Any, filters: Sequence[str] | None) -> bool:
    if not filters:
        return True
    wanted = {item.strip().casefold() for item in filters}
    return str(value or "").strip().casefold() in wanted


def normalize_rows(payload: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    genes = payload.get("genes")
    columns = payload.get("columns")
    if not isinstance(genes, list) or not isinstance(columns, list):
        raise RuntimeError("Bulk RNA-Seq response is missing genes or columns")

    values = payload.get("values") or []
    raw_values = payload.get("rawValues") or []
    sd_values = payload.get("sdValues") or []
    n_values = payload.get("nValues") or []
    rows: list[dict[str, Any]] = []
    for column_index, column in enumerate(columns):
        if not isinstance(column, dict):
            continue
        if not exact_match(column.get("tissue"), args.tissue):
            continue
        if not exact_match(column.get("sampleName"), args.sample_name):
            continue
        for gene_index, gene in enumerate(genes):
            if not isinstance(gene, dict):
                continue
            rows.append(
                {
                    "dataset": payload.get("dataset", ""),
                    "scope": payload.get("scope", args.scope),
                    "grouping_label": SCOPE_LABELS[args.scope],
                    "transform": payload.get("transform", args.transform),
                    "scale_label": TRANSFORM_LABELS[args.transform],
                    "gene_id": gene.get("geneId", ""),
                    "transcript_id": gene.get("transcriptId", ""),
                    "gene_name": gene.get("geneName", ""),
                    "column_id": column.get("id", ""),
                    "column_label": column.get("label", ""),
                    "sample_name": column.get("sampleName", ""),
                    "tissue": column.get("tissue", ""),
                    "value": as_number(matrix_value(values, gene_index, column_index, 0.0)),
                    "mean_tpm": as_number(
                        matrix_value(raw_values, gene_index, column_index, 0.0)
                    ),
                    "sd_tpm": as_number(
                        matrix_value(sd_values, gene_index, column_index, 0.0)
                    ),
                    "n": int(as_number(matrix_value(n_values, gene_index, column_index, 0))),
                }
            )
    return rows


def write_tsv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TSV_FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in TSV_FIELDS})


def selected_summary(rows: Sequence[dict[str, Any]], gene_count: int) -> dict[str, Any]:
    columns = {str(row["column_id"]) for row in rows}
    values = [float(row["value"]) for row in rows]
    raw_values = [float(row["mean_tpm"]) for row in rows]
    return {
        "geneCount": gene_count,
        "columnCount": len(columns),
        "rowCount": len(rows),
        "valueMin": round(min(values), 6) if values else 0.0,
        "valueMax": round(max(values), 6) if values else 0.0,
        "rawMin": round(min(raw_values), 6) if raw_values else 0.0,
        "rawMax": round(max(raw_values), 6) if raw_values else 0.0,
    }


def top_rows_per_gene(
    rows: Sequence[dict[str, Any]],
    genes: Sequence[dict[str, Any]],
    top: int,
) -> list[dict[str, Any]]:
    order = {
        str(gene.get("geneId", "")): index
        for index, gene in enumerate(genes)
        if isinstance(gene, dict)
    }
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            order.get(str(row["gene_id"]), len(order)),
            -float(row["value"]),
            str(row["column_id"]),
        ),
    )
    if top == 0:
        return sorted_rows
    counts: dict[str, int] = {}
    visible: list[dict[str, Any]] = []
    for row in sorted_rows:
        gene_id = str(row["gene_id"])
        count = counts.get(gene_id, 0)
        if count >= top:
            continue
        visible.append(row)
        counts[gene_id] = count + 1
    return visible


def run_status(args: argparse.Namespace) -> int:
    url, payload = request_json(args.base_url, "status", None, args.timeout)
    print_json(
        {
            "source": "Potato Agent Bulk RNA-Seq API",
            "endpoint": url,
            **payload,
        },
        args.indent,
    )
    return 0


def run_search(args: argparse.Namespace) -> int:
    limit = max(1, min(100, args.limit))
    url, payload = request_json(
        args.base_url,
        "genes",
        {"q": args.query, "limit": limit},
        args.timeout,
    )
    print_json(
        {
            "source": "Potato Agent Bulk RNA-Seq API",
            "endpoint": url,
            **payload,
        },
        args.indent,
    )
    return 0


def run_query(args: argparse.Namespace) -> int:
    if args.top < 0:
        raise ValueError("--top must be zero or greater")
    genes = parse_genes(args.genes)
    url, payload = request_json(
        args.base_url,
        "expression",
        {
            "genes": ",".join(genes),
            "scope": args.scope,
            "transform": args.transform,
        },
        args.timeout,
    )
    if args.raw_response:
        print_json(payload, args.indent)
        return 0

    rows = normalize_rows(payload, args)
    if args.output_tsv:
        write_tsv(Path(args.output_tsv), rows)
    gene_rows = payload.get("genes") if isinstance(payload.get("genes"), list) else []
    result = {
        "source": "Potato Agent Bulk RNA-Seq API",
        "endpoint": url,
        "dataset": payload.get("dataset", ""),
        "queryGenes": genes,
        "scope": payload.get("scope", args.scope),
        "groupingLabel": SCOPE_LABELS[args.scope],
        "transform": payload.get("transform", args.transform),
        "scaleLabel": TRANSFORM_LABELS[args.transform],
        "filters": {
            "tissue": args.tissue or [],
            "sampleName": args.sample_name or [],
        },
        "genes": gene_rows,
        "apiSummary": payload.get("summary", {}),
        "selectedSummary": selected_summary(rows, len(gene_rows)),
        "topPerGene": args.top,
        "rows": top_rows_per_gene(rows, gene_rows, args.top),
        "outputTsv": args.output_tsv,
    }
    print_json(result, args.indent)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BrokenPipeError:
        return 0
    except (RuntimeError, ValueError) as exc:
        eprint(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

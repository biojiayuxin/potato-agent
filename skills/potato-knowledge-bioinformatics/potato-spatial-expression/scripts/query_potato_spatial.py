#!/usr/bin/env python3
"""Query Potato Agent spatial expression statistics and build dotplots."""

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
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:3000"
TIMEOUT_SECONDS = 60
GROUP_KEYS = {
    "cluster": "clusterExpression",
    "tissue": "tissueExpression",
}
TSV_FIELDS = [
    "dataset",
    "gene",
    "group_type",
    "scope",
    "sample",
    "group_id",
    "group_label",
    "cell_count",
    "expressing_count",
    "pct_expr",
    "avg_expr",
    "avg_expr_expressing",
    "sum_expr",
    "max_expr",
]


def common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POTATO_SPATIAL_BASE_URL", DEFAULT_BASE_URL),
        help=f"Potato Agent base URL. Default: {DEFAULT_BASE_URL}.",
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
        description="Query potato spatial expression statistics and generate dotplots.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    expression = subparsers.add_parser(
        "expression",
        parents=[common],
        help="Print aggregate expression JSON for one dataset and gene.",
    )
    expression.add_argument("gene", help="Exact spatial gene ID, such as Soltu.DM.03G024100.")
    expression.add_argument("--dataset", required=True, help="Spatial dataset ID.")

    dotplot = subparsers.add_parser(
        "dotplot",
        parents=[common],
        help="Generate PDF and TSV dotplot files for one or more datasets.",
    )
    dotplot.add_argument("gene", help="Exact spatial gene ID, such as Soltu.DM.03G024100.")
    dotplot.add_argument(
        "--dataset",
        action="append",
        required=True,
        help="Spatial dataset ID. Repeat to include multiple datasets.",
    )
    dotplot.add_argument(
        "--group",
        choices=sorted(GROUP_KEYS),
        default="cluster",
        help="Group type for the x axis. Default: cluster.",
    )
    dotplot.add_argument(
        "--outdir",
        default="spatial_plots",
        help="Output directory for PDF and TSV files. Default: spatial_plots.",
    )
    return parser


def request_expression(base_url: str, dataset: str, gene: str, timeout: int) -> dict[str, Any]:
    params = urllib.parse.urlencode({"dataset": dataset, "gene": gene})
    url = f"{base_url.rstrip('/')}/api/spatial/agent/expression?{params}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from spatial expression API: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to connect to spatial expression API: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Spatial expression API returned non-JSON response: {body[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Spatial expression API returned JSON that is not an object")
    return payload


def safe_filename_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return safe or "spatial_expression"


def row_number(row: dict[str, Any], key: str) -> float:
    try:
        value = float(row.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return value


def normalize_expression_rows(payload: dict[str, Any], group_type: str) -> list[dict[str, Any]]:
    dataset = str(payload.get("dataset") or "")
    gene = str(payload.get("gene") or "")
    source_rows = payload.get(GROUP_KEYS[group_type]) or []
    if not isinstance(source_rows, list):
        raise RuntimeError(f"API field {GROUP_KEYS[group_type]!r} is not a list")

    rows: list[dict[str, Any]] = []
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "dataset": dataset,
                "gene": gene,
                "group_type": group_type,
                "scope": str(row.get("scope") or ""),
                "sample": str(row.get("sample") or ""),
                "group_id": str(row.get("groupId") or ""),
                "group_label": str(row.get("groupLabel") or row.get("groupId") or ""),
                "cell_count": int(row_number(row, "cellCount")),
                "expressing_count": int(row_number(row, "expressingCount")),
                "pct_expr": row_number(row, "pctExpr"),
                "avg_expr": row_number(row, "avgExpr"),
                "avg_expr_expressing": row_number(row, "avgExprExpressing"),
                "sum_expr": row_number(row, "sumExpr"),
                "max_expr": row_number(row, "maxExpr"),
            }
        )
    return rows


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TSV_FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in TSV_FIELDS})


def pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_text(value: str, x: float, y: float, size: float = 8, angle: float = 0.0) -> str:
    escaped = pdf_escape(value)
    if angle:
        radians = math.radians(angle)
        c = math.cos(radians)
        s = math.sin(radians)
        return (
            f"BT /F1 {size:.1f} Tf {c:.4f} {s:.4f} {-s:.4f} {c:.4f} "
            f"{x:.2f} {y:.2f} Tm ({escaped}) Tj ET"
        )
    return f"BT /F1 {size:.1f} Tf {x:.2f} {y:.2f} Td ({escaped}) Tj ET"


def pdf_circle(x: float, y: float, radius: float) -> str:
    k = 0.5522847498
    c = radius * k
    return "\n".join(
        [
            f"{x + radius:.2f} {y:.2f} m",
            f"{x + radius:.2f} {y + c:.2f} {x + c:.2f} {y + radius:.2f} {x:.2f} {y + radius:.2f} c",
            f"{x - c:.2f} {y + radius:.2f} {x - radius:.2f} {y + c:.2f} {x - radius:.2f} {y:.2f} c",
            f"{x - radius:.2f} {y - c:.2f} {x - c:.2f} {y - radius:.2f} {x:.2f} {y - radius:.2f} c",
            f"{x + c:.2f} {y - radius:.2f} {x + radius:.2f} {y - c:.2f} {x + radius:.2f} {y:.2f} c",
            "f",
        ]
    )


def color_for_value(value: float, max_value: float) -> tuple[float, float, float]:
    if max_value <= 0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, value / max_value))
    low = (219, 234, 254)
    high = (185, 28, 28)
    rgb = [low[i] + (high[i] - low[i]) * t for i in range(3)]
    return tuple(channel / 255.0 for channel in rgb)


def build_pdf(rows: list[dict[str, Any]], group_type: str, gene: str) -> bytes:
    if not rows:
        raise RuntimeError("no rows available for dotplot")

    group_ids: list[str] = []
    group_labels: dict[str, str] = {}
    y_ids: list[str] = []
    y_labels: dict[str, str] = {}
    values: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        group_id = str(row["group_id"])
        if group_id not in group_labels:
            group_ids.append(group_id)
            group_labels[group_id] = str(row["group_label"] or group_id)

        if row["scope"] == "dataset":
            y_id = f"{row['dataset']}|dataset|"
            y_label = str(row["dataset"])
        else:
            y_id = f"{row['dataset']}|sample|{row['sample']}"
            y_label = f"{row['dataset']} / {row['sample']}"
        if y_id not in y_labels:
            y_ids.append(y_id)
            y_labels[y_id] = y_label
        values[(y_id, group_id)] = row

    cell_w = 70.0
    cell_h = 26.0
    left = 160.0
    right = 48.0
    top = 70.0
    bottom = 118.0
    plot_w = max(1.0, (len(group_ids) - 1) * cell_w)
    plot_h = max(1.0, (len(y_ids) - 1) * cell_h)
    width = max(360.0, left + right + plot_w)
    height = max(260.0, top + bottom + plot_h)
    plot_left = left
    plot_right = left + plot_w
    plot_bottom = bottom
    plot_top = bottom + plot_h
    max_avg = max((float(row["avg_expr"]) for row in rows), default=0.0)

    commands: list[str] = [
        "1 1 1 rg 0 0 {0:.2f} {1:.2f} re f".format(width, height),
        "0.06 0.07 0.09 rg",
        pdf_text(f"{gene} {group_type} dotplot", 24, height - 30, 13),
        pdf_text("size: pct_expr    color: avg_expr", 24, height - 48, 8),
        "0.88 0.90 0.93 RG 0.4 w",
    ]

    for index, group_id in enumerate(group_ids):
        x = plot_left + index * cell_w
        commands.append(f"{x:.2f} {plot_bottom:.2f} m {x:.2f} {plot_top:.2f} l S")
        label = group_labels[group_id]
        commands.append("0.06 0.07 0.09 rg")
        commands.append(pdf_text(label[:42], x - 3, plot_bottom - 12, 7, angle=-55))

    for index, y_id in enumerate(y_ids):
        y = plot_top - index * cell_h
        commands.append("0.88 0.90 0.93 RG 0.4 w")
        commands.append(f"{plot_left:.2f} {y:.2f} m {plot_right:.2f} {y:.2f} l S")
        commands.append("0.06 0.07 0.09 rg")
        commands.append(pdf_text(y_labels[y_id][:34], 18, y - 3, 8))

    for y_index, y_id in enumerate(y_ids):
        y = plot_top - y_index * cell_h
        for x_index, group_id in enumerate(group_ids):
            row = values.get((y_id, group_id))
            if not row:
                continue
            pct_expr = max(0.0, min(100.0, float(row["pct_expr"])))
            if pct_expr <= 0:
                continue
            avg_expr = max(0.0, float(row["avg_expr"]))
            radius = 2.0 + 9.0 * math.sqrt(pct_expr / 100.0)
            r, g, b = color_for_value(avg_expr, max_avg)
            x = plot_left + x_index * cell_w
            commands.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
            commands.append(pdf_circle(x, y, radius))

    commands.extend(
        [
            "0.06 0.07 0.09 rg",
            pdf_text(group_type, (plot_left + plot_right) / 2 - 20, 24, 9),
            pdf_text("dataset / sample", 24, plot_top + 16, 9),
        ]
    )
    return write_pdf(commands, width, height)


def write_pdf(commands: list[str], width: float, height: float) -> bytes:
    content = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def run_expression(args: argparse.Namespace) -> int:
    payload = request_expression(args.base_url, args.dataset, args.gene, args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_dotplot(args: argparse.Namespace) -> int:
    all_rows: list[dict[str, Any]] = []
    for dataset in args.dataset:
        payload = request_expression(args.base_url, dataset, args.gene, args.timeout)
        all_rows.extend(normalize_expression_rows(payload, args.group))
    if not all_rows:
        raise RuntimeError("API returned no expression rows")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    output_prefix = f"{safe_filename_component(args.gene)}_{args.group}_dotplot"
    tsv_path = outdir / f"{output_prefix}.tsv"
    pdf_path = outdir / f"{output_prefix}.pdf"
    write_tsv(tsv_path, all_rows)
    pdf_path.write_bytes(build_pdf(all_rows, args.group, args.gene))

    print(
        json.dumps(
            {
                "gene": args.gene,
                "group": args.group,
                "datasets": args.dataset,
                "rows": len(all_rows),
                "tsv": str(tsv_path),
                "pdf": str(pdf_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "expression":
            return run_expression(args)
        if args.command == "dotplot":
            return run_dotplot(args)
        parser.error(f"unknown command: {args.command}")
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render Potato Agent Bulk RNA-Seq API results as an editable vector PDF."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from query_expression_atlas import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    SCOPES,
    SCOPE_LABELS,
    TRANSFORMS,
    TRANSFORM_LABELS,
    parse_genes,
    request_json,
)


FONT_SIZE = 11.0
FONT_COLOR = "#334155"
PANEL_BORDER = "#d6dfef"
LOW_COLOR = "#f8fafc"
HIGH_COLOR = "#ff0000"
NEGATIVE_COLOR = "#0000ff"

# Adobe Helvetica-Bold widths in 1/1000 em for printable ASCII.
HELVETICA_BOLD_WIDTHS = {
    " ": 278, "!": 333, '"': 474, "#": 556, "$": 556, "%": 889,
    "&": 722, "'": 278, "(": 333, ")": 333, "*": 389, "+": 584,
    ",": 278, "-": 333, ".": 278, "/": 278, "0": 556, "1": 556,
    "2": 556, "3": 556, "4": 556, "5": 556, "6": 556, "7": 556,
    "8": 556, "9": 556, ":": 333, ";": 333, "<": 584, "=": 584,
    ">": 584, "?": 611, "@": 975, "A": 722, "B": 722, "C": 722,
    "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 556, "K": 722, "L": 611, "M": 833, "N": 722, "O": 778,
    "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 722, "W": 944, "X": 722, "Y": 722, "Z": 611, "[": 333,
    "\\": 278, "]": 333, "^": 584, "_": 556, "`": 333, "a": 556,
    "b": 611, "c": 556, "d": 611, "e": 556, "f": 333, "g": 611,
    "h": 611, "i": 278, "j": 278, "k": 556, "l": 278, "m": 889,
    "n": 611, "o": 611, "p": 611, "q": 611, "r": 389, "s": 556,
    "t": 333, "u": 611, "v": 556, "w": 778, "x": 556, "y": 556,
    "z": 500, "{": 389, "|": 280, "}": 389, "~": 584,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draw a Bulk RNA-Seq heatmap matching the Interface page as vector PDF."
    )
    parser.add_argument(
        "genes",
        nargs="+",
        help="DMv8.2 gene IDs, for example DM8.2_chr06G22780 (maximum 50).",
    )
    parser.add_argument(
        "--scope",
        "--grouping",
        dest="scope",
        choices=SCOPES,
        default="sample_tissue",
        help="Grouping mode. Default: sample_tissue",
    )
    parser.add_argument(
        "--transform",
        "--scale",
        dest="transform",
        choices=TRANSFORMS,
        default="log2_tpm",
        help="Expression scale. Default: log2_tpm",
    )
    parser.add_argument("--output", required=True, help="Output path; must end in .pdf.")
    parser.add_argument(
        "--width",
        type=float,
        default=1280.0,
        help="PDF width in points/CSS pixels. Default: 1280",
    )
    parser.add_argument(
        "--stage-height",
        type=float,
        help="Heatmap stage height. Defaults to the Interface page height for the layout.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POTATO_BULK_RNASEQ_BASE_URL", DEFAULT_BASE_URL),
        help=f"Potato Agent site or API root. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds. Default: {DEFAULT_TIMEOUT}",
    )
    return parser


def hex_rgb(value: str) -> tuple[float, float, float]:
    text = value.lstrip("#")
    return tuple(int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4))


def mix_rgb(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
    amount: float,
) -> tuple[float, float, float]:
    return tuple(left[index] + (right[index] - left[index]) * amount for index in range(3))


def color_from_stops(
    value: float,
    stops: Sequence[tuple[float, tuple[float, float, float]]],
) -> tuple[float, float, float]:
    normalized = max(0.0, min(1.0, value))
    for index in range(1, len(stops)):
        right_stop, right_color = stops[index]
        left_stop, left_color = stops[index - 1]
        if normalized <= right_stop:
            local = (normalized - left_stop) / max(0.000001, right_stop - left_stop)
            return mix_rgb(left_color, right_color, local)
    return stops[-1][1]


SEQUENTIAL_STOPS = ((0.0, hex_rgb(LOW_COLOR)), (1.0, hex_rgb(HIGH_COLOR)))
DIVERGING_STOPS = (
    (0.0, hex_rgb(NEGATIVE_COLOR)),
    (0.5, hex_rgb(LOW_COLOR)),
    (1.0, hex_rgb(HIGH_COLOR)),
)


def display_range(payload: dict[str, Any]) -> tuple[float, float]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    minimum = float(summary.get("valueMin") or 0.0)
    maximum = float(summary.get("valueMax") or 0.0)
    if payload.get("transform") == "row_zscore":
        max_abs = max(1.0, abs(minimum), abs(maximum))
        return -max_abs, max_abs
    return min(0.0, minimum), max(1.0, maximum)


def value_color(
    value: float,
    value_range: tuple[float, float],
    transform: str,
) -> tuple[float, float, float]:
    minimum, maximum = value_range
    normalized = (value - minimum) / max(0.000001, maximum - minimum)
    stops = DIVERGING_STOPS if transform == "row_zscore" else SEQUENTIAL_STOPS
    return color_from_stops(normalized, stops)


def format_number(value: Any, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    if abs(number) >= 1000:
        return str(round(number))
    rounded = round(number, digits)
    return f"{rounded:.{digits}f}".rstrip("0").rstrip(".") or "0"


def short_label(value: Any, max_length: int = 22) -> str:
    text = str(value or "")
    return f"{text[:max_length - 3]}..." if len(text) > max_length else text


def short_gene_label(gene_id: Any) -> str:
    text = str(gene_id or "")
    match = re.search(r"chr([0-9A-Za-z]+)G([0-9]+)$", text)
    if match:
        return f"chr{match.group(1)}G{match.group(2)}"
    return f"{text[:12]}...{text[-7:]}" if len(text) > 22 else text


def column_label(column: dict[str, Any], scope: str) -> str:
    if scope == "sample_tissue":
        return f"{column.get('sampleName', '')} {column.get('tissue', '')}".strip()
    if scope == "sample_name":
        return str(column.get("sampleName") or column.get("label") or "")
    if scope == "tissue":
        return str(column.get("tissue") or column.get("label") or "")
    return str(column.get("sampleColumn") or column.get("label") or "")


def wrap_label(label: Any, max_chars: int) -> list[str]:
    text = re.sub(r"\s*\([^)]*\)", "", str(label or "")).strip()
    if not text:
        return [""]
    limit = max(6, max_chars)
    if len(text) <= limit:
        return [text]
    words = [word for word in text.split() if word]
    if len(words) <= 1:
        return [short_label(text, limit)]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return [short_label(line, limit) for line in lines]
    return [short_label(lines[0], limit), short_label(" ".join(lines[1:]), limit)]


def text_width(value: Any, size: float = FONT_SIZE) -> float:
    units = sum(HELVETICA_BOLD_WIDTHS.get(char, 600) for char in str(value or ""))
    return units * size / 1000.0


def max_text_width(labels: Sequence[str], cap: float) -> float:
    return min(cap, max((text_width(label) for label in labels), default=0.0))


def pdf_escape(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class PdfCanvas:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height
        self.commands: list[str] = []

    def _color(self, color: str | tuple[float, float, float]) -> str:
        rgb = hex_rgb(color) if isinstance(color, str) else color
        return " ".join(f"{channel:.5f}" for channel in rgb)

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: str | tuple[float, float, float] | None = None,
        stroke: str | tuple[float, float, float] | None = None,
        line_width: float = 1.0,
        alpha: str | None = None,
    ) -> None:
        pdf_y = self.height - y - height
        parts = ["q"]
        if alpha:
            parts.append(f"/{alpha} gs")
        if fill is not None:
            parts.append(f"{self._color(fill)} rg")
        if stroke is not None:
            parts.append(f"{self._color(stroke)} RG {line_width:.3f} w")
        operation = "B" if fill is not None and stroke is not None else ("f" if fill is not None else "S")
        parts.append(f"{x:.3f} {pdf_y:.3f} {width:.3f} {height:.3f} re {operation}")
        parts.append("Q")
        self.commands.append(" ".join(parts))

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str | tuple[float, float, float],
        line_width: float = 1.0,
        alpha: str | None = None,
    ) -> None:
        parts = ["q"]
        if alpha:
            parts.append(f"/{alpha} gs")
        parts.append(f"{self._color(color)} RG {line_width:.3f} w")
        parts.append(
            f"{x1:.3f} {self.height - y1:.3f} m "
            f"{x2:.3f} {self.height - y2:.3f} l S"
        )
        parts.append("Q")
        self.commands.append(" ".join(parts))

    def text(
        self,
        value: Any,
        x: float,
        y: float,
        *,
        size: float = FONT_SIZE,
        color: str = FONT_COLOR,
        align: str = "left",
        angle: float = 0.0,
        bold: bool = True,
    ) -> None:
        text = str(value or "")
        if not text:
            return
        if align == "right":
            x -= text_width(text, size)
        elif align == "center":
            x -= text_width(text, size) / 2.0
        radians = math.radians(angle)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        baseline = self.height - y - size * 0.34
        font = "F2" if bold else "F1"
        self.commands.append(
            f"q {self._color(color)} rg BT /{font} {size:.2f} Tf "
            f"{cosine:.5f} {sine:.5f} {-sine:.5f} {cosine:.5f} "
            f"{x:.3f} {baseline:.3f} Tm ({pdf_escape(text)}) Tj ET Q"
        )


def compact_grid_height(row_count: int, available_height: float) -> float:
    if row_count <= 1:
        return min(38.0, available_height)
    if row_count <= 3:
        return min(row_count * 34.0, available_height)
    if row_count <= 12:
        return min(row_count * 28.0, available_height)
    return available_height


def draw_wrapped_column_labels(
    canvas: PdfCanvas,
    labels: Sequence[str],
    left: float,
    top: float,
    cell_width: float,
) -> None:
    max_chars = max(6, math.floor((cell_width - 6.0) / 6.2))
    step = 1 if len(labels) <= 24 else max(1, math.ceil(38.0 / max(1.0, cell_width)))
    for index in range(0, len(labels), step):
        x = left + index * cell_width + cell_width / 2.0
        lines = wrap_label(labels[index], max_chars)[:2]
        y = top - 30.0 if len(lines) > 1 else top - 22.0
        canvas.text(lines[0], x, y, align="center")
        if len(lines) > 1:
            canvas.text(lines[1], x, y + 14.0, align="center")


def draw_rotated_column_labels(
    canvas: PdfCanvas,
    labels: Sequence[str],
    left: float,
    top: float,
    cell_width: float,
    *,
    max_length: int,
    min_gap: float,
) -> None:
    step = max(1, math.ceil(min_gap / max(1.0, cell_width)))
    for index in range(0, len(labels), step):
        x = left + index * cell_width + cell_width / 2.0
        canvas.text(short_label(labels[index], max_length), x, top - 8.0, angle=45.0)


def draw_row_labels(
    canvas: PdfCanvas,
    labels: Sequence[str],
    left: float,
    top: float,
    cell_height: float,
) -> None:
    step = max(1, math.ceil(10.0 / max(1.0, cell_height)))
    for index in range(0, len(labels), step):
        y = top + index * cell_height + cell_height / 2.0
        canvas.text(short_label(labels[index], 26), left - 8.0, y, align="right")


def draw_grid(
    canvas: PdfCanvas,
    left: float,
    top: float,
    grid_width: float,
    grid_height: float,
    rows: int,
    columns: int,
    cell_width: float,
    cell_height: float,
    *,
    threshold: float,
) -> None:
    if cell_width >= threshold and cell_height >= threshold:
        for index in range(columns + 1):
            x = left + index * cell_width
            canvas.line(x, top, x, top + grid_height, color="#ffffff", alpha="GS56")
        for index in range(rows + 1):
            y = top + index * cell_height
            canvas.line(left, y, left + grid_width, y, color="#ffffff", alpha="GS56")
    canvas.rect(left, top, grid_width, grid_height, stroke="#64748b", alpha="GS28")


def draw_single_gene_pivot(
    canvas: PdfCanvas,
    payload: dict[str, Any],
    stage_top: float,
    stage_height: float,
) -> None:
    tissues: list[str] = []
    sample_names: list[str] = []
    column_by_pair: dict[tuple[str, str], tuple[dict[str, Any], int]] = {}
    for column_index, column in enumerate(payload["columns"]):
        tissue = str(column.get("tissue") or "")
        sample_name = str(column.get("sampleName") or "")
        if tissue and tissue not in tissues:
            tissues.append(tissue)
        if sample_name and sample_name not in sample_names:
            sample_names.append(sample_name)
        column_by_pair[(sample_name, tissue)] = (column, column_index)

    left = max(92.0, max_text_width(sample_names, 162.0) + 16.0)
    top = stage_top + 56.0
    right = 24.0
    bottom = 24.0
    grid_width = max(1.0, canvas.width - left - right)
    grid_height = max(1.0, stage_height - 56.0 - bottom)
    cell_width = grid_width / max(1, len(tissues))
    cell_height = grid_height / max(1, len(sample_names))
    value_range = display_range(payload)

    draw_wrapped_column_labels(canvas, tissues, left, top, cell_width)
    draw_row_labels(canvas, sample_names, left, top, cell_height)
    for row_index, sample_name in enumerate(sample_names):
        for column_index, tissue in enumerate(tissues):
            x = left + column_index * cell_width
            y = top + row_index * cell_height
            pair = column_by_pair.get((sample_name, tissue))
            if pair is None:
                canvas.rect(x, y, cell_width, cell_height, fill=LOW_COLOR, alpha="GS82")
                continue
            _, payload_index = pair
            value = float(payload["values"][0][payload_index] or 0.0)
            canvas.rect(
                x,
                y,
                max(0.5, cell_width),
                max(0.5, cell_height),
                fill=value_color(value, value_range, str(payload["transform"])),
            )
    draw_grid(
        canvas,
        left,
        top,
        grid_width,
        grid_height,
        len(sample_names),
        len(tissues),
        cell_width,
        cell_height,
        threshold=5.0,
    )


def draw_matrix_heatmap(
    canvas: PdfCanvas,
    payload: dict[str, Any],
    stage_top: float,
    stage_height: float,
) -> None:
    row_labels = [short_gene_label(gene.get("geneId")) for gene in payload["genes"]]
    column_labels = [column_label(column, str(payload["scope"])) for column in payload["columns"]]
    hide_column_labels = payload["scope"] == "sample"
    compact_wrapped = len(payload["genes"]) == 1 and len(payload["columns"]) <= 24
    left = max(92.0, max_text_width(row_labels, 190.0) + 16.0)
    relative_top = 24.0 if hide_column_labels else (56.0 if compact_wrapped else 78.0)
    top = stage_top + relative_top
    right = 24.0
    bottom = 24.0
    grid_width = max(1.0, canvas.width - left - right)
    available_height = max(1.0, stage_height - relative_top - bottom)
    row_count = len(payload["genes"])
    column_count = len(payload["columns"])
    grid_height = compact_grid_height(row_count, available_height)
    cell_width = grid_width / max(1, column_count)
    cell_height = grid_height / max(1, row_count)
    value_range = display_range(payload)

    if not hide_column_labels:
        if compact_wrapped:
            draw_wrapped_column_labels(canvas, column_labels, left, top, cell_width)
        else:
            draw_rotated_column_labels(
                canvas,
                column_labels,
                left,
                top,
                cell_width,
                max_length=30 if payload["scope"] == "sample_tissue" else 24,
                min_gap=74.0 if column_count > 80 else 54.0,
            )
    draw_row_labels(canvas, row_labels, left, top, cell_height)
    for row_index in range(row_count):
        for column_index in range(column_count):
            value = float(payload["values"][row_index][column_index] or 0.0)
            canvas.rect(
                left + column_index * cell_width,
                top + row_index * cell_height,
                max(0.5, cell_width),
                max(0.5, cell_height),
                fill=value_color(value, value_range, str(payload["transform"])),
            )
    draw_grid(
        canvas,
        left,
        top,
        grid_width,
        grid_height,
        row_count,
        column_count,
        cell_width,
        cell_height,
        threshold=7.0,
    )


def draw_legend(
    canvas: PdfCanvas,
    payload: dict[str, Any],
    legend_top: float,
) -> None:
    minimum, maximum = display_range(payload)
    bar_width = min(320.0, max(160.0, canvas.width * 0.25))
    bar_height = 12.0
    right_margin = 46.0
    bar_left = canvas.width - right_margin - bar_width
    bar_top = legend_top + 15.0
    steps = 96
    for index in range(steps):
        normalized = index / max(1, steps - 1)
        stops = DIVERGING_STOPS if payload["transform"] == "row_zscore" else SEQUENTIAL_STOPS
        color = color_from_stops(normalized, stops)
        segment_left = bar_left + bar_width * index / steps
        segment_width = bar_width / steps + 0.05
        canvas.rect(segment_left, bar_top, segment_width, bar_height, fill=color)
    canvas.rect(bar_left, bar_top, bar_width, bar_height, stroke="#64748b", alpha="GS28")
    canvas.text(format_number(minimum, 2), bar_left - 9.0, bar_top + bar_height / 2.0, size=12.0, align="right")
    canvas.text(format_number(maximum, 2), bar_left + bar_width + 9.0, bar_top + bar_height / 2.0, size=12.0)


def build_pdf(payload: dict[str, Any], width: float, stage_height: float) -> bytes:
    toolbar_height = 64.0
    legend_height = 42.0
    total_height = toolbar_height + stage_height + legend_height
    canvas = PdfCanvas(width, total_height)
    canvas.rect(0.0, 0.0, width, total_height, fill="#ffffff", stroke=PANEL_BORDER)
    canvas.line(0.0, toolbar_height, width, toolbar_height, color=PANEL_BORDER)
    canvas.line(
        0.0,
        toolbar_height + stage_height,
        width,
        toolbar_height + stage_height,
        color=PANEL_BORDER,
    )

    genes = payload["genes"]
    title = str(genes[0].get("geneId") or "") if len(genes) == 1 else f"{len(genes)} genes"
    summary = " | ".join(
        (
            SCOPE_LABELS[str(payload["scope"])],
            TRANSFORM_LABELS[str(payload["transform"])],
            f"{len(payload['columns'])} columns",
        )
    )
    canvas.text(title, 14.0, 22.0, size=16.0)
    canvas.text(summary, 14.0, 43.0, size=12.0, color="#64748b")

    stage_top = toolbar_height
    canvas.rect(0.0, stage_top, width, stage_height, fill="#ffffff")
    for x in range(0, math.ceil(width) + 1, 36):
        canvas.line(float(x), stage_top, float(x), stage_top + stage_height, color="#f5f6f8")
    for offset in range(0, math.ceil(stage_height) + 1, 36):
        canvas.line(0.0, stage_top + offset, width, stage_top + offset, color="#f5f6f8")

    if payload["scope"] == "sample_tissue" and len(genes) == 1:
        draw_single_gene_pivot(canvas, payload, stage_top, stage_height)
        layout = "single_gene_pivot"
    else:
        draw_matrix_heatmap(canvas, payload, stage_top, stage_height)
        layout = "matrix_heatmap"
    payload["_pdfLayout"] = layout
    draw_legend(canvas, payload, toolbar_height + stage_height)
    return write_pdf(canvas.commands, width, total_height, title)


def write_pdf(commands: Sequence[str], width: float, height: float, title: str) -> bytes:
    content = "\n".join(commands).encode("latin-1", errors="replace")
    info_title = pdf_escape(title)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.3f} {height:.3f}] "
            "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> "
            "/ExtGState << /GS28 6 0 R /GS56 7 0 R /GS82 8 0 R >> >> "
            "/Contents 9 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Type /ExtGState /CA 0.28 /ca 0.28 >>",
        b"<< /Type /ExtGState /CA 0.56 /ca 0.56 >>",
        b"<< /Type /ExtGState /CA 0.82 /ca 0.82 >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        f"<< /Title ({info_title}) /Creator (Potato Agent expression-atlas-query) >>".encode(
            "latin-1", errors="replace"
        ),
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
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 10 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def validate_payload(payload: dict[str, Any]) -> None:
    genes = payload.get("genes")
    columns = payload.get("columns")
    values = payload.get("values")
    if not isinstance(genes, list) or not genes:
        raise RuntimeError("Bulk RNA-Seq API returned no genes")
    if not isinstance(columns, list) or not columns:
        raise RuntimeError("Bulk RNA-Seq API returned no columns")
    if not isinstance(values, list) or len(values) != len(genes):
        raise RuntimeError("Bulk RNA-Seq API returned an invalid value matrix")
    if any(not isinstance(row, list) or len(row) != len(columns) for row in values):
        raise RuntimeError("Bulk RNA-Seq API value matrix dimensions do not match columns")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        genes = parse_genes(args.genes)
        output = Path(args.output)
        if output.suffix.lower() != ".pdf":
            raise ValueError("--output must use the .pdf extension; raster output is not supported")
        if not math.isfinite(args.width) or args.width < 480:
            raise ValueError("--width must be at least 480")
        _, payload = request_json(
            args.base_url,
            "expression",
            {
                "genes": ",".join(genes),
                "scope": args.scope,
                "transform": args.transform,
            },
            args.timeout,
        )
        validate_payload(payload)
        default_stage_height = 168.0 if len(genes) == 1 and args.scope != "sample_tissue" else 620.0
        stage_height = args.stage_height if args.stage_height is not None else default_stage_height
        if not math.isfinite(stage_height) or stage_height < 120:
            raise ValueError("--stage-height must be at least 120")
        pdf = build_pdf(payload, args.width, stage_height)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(pdf)
        print(
            json.dumps(
                {
                    "dataset": payload.get("dataset", ""),
                    "genes": genes,
                    "scope": args.scope,
                    "transform": args.transform,
                    "columns": len(payload["columns"]),
                    "layout": payload.get("_pdfLayout", ""),
                    "width": args.width,
                    "height": 64.0 + stage_height + 42.0,
                    "output": str(output),
                    "format": "vector PDF",
                    "embeddedRasterImages": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except BrokenPipeError:
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

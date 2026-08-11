#!/usr/bin/env python3
"""Render saved Potato Agent WGCNA query results as vector SVG or PDF."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.parse
from collections import defaultdict
from html import escape as xml_escape
from pathlib import Path
from typing import Any, Sequence

PRODUCTION_API_ROOT = "https://potato-agent.ynnu.edu.cn/api/wgcna"
NETWORKS = ("leaf", "stem", "root", "reproductive", "tuberization")
NETWORK_LABELS = {
    "leaf": "Leaf",
    "stem": "Stem",
    "root": "Root",
    "reproductive": "Reproductive",
    "tuberization": "Tuberization",
}
NETWORK_COLORS = {
    "leaf": "#15803d",
    "stem": "#0f766e",
    "root": "#7c3aed",
    "reproductive": "#c2410c",
    "tuberization": "#0369a1",
}
NETWORK_NODE_FILLS = {
    "leaf": "#22c55e",
    "stem": "#14b8a6",
    "root": "#8b5cf6",
    "reproductive": "#f97316",
    "tuberization": "#0ea5e9",
}
NETWORK_NODE_SOFT_FILLS = {
    "leaf": "#bbf7d0",
    "stem": "#99f6e4",
    "root": "#ddd6fe",
    "reproductive": "#fed7aa",
    "tuberization": "#bae6fd",
}
TEXT_COLOR = "#14213d"
MUTED_COLOR = "#64748b"
PANEL_BORDER = "#d6dfef"
GRID_COLOR = "#f0f3f8"
QUERY_BORDER = "#0f172a"
WARNING_COLOR = "#c2410c"
DEFAULT_WIDTH = 1400.0
DEFAULT_STAGE_HEIGHT = 760.0


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("expected a positive finite number")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a saved WGCNA coexpression query result as vector SVG or PDF."
    )
    parser.add_argument(
        "input_json",
        type=Path,
        help="JSON file written by query_wgcna_network.py coexpression --output-json.",
    )
    parser.add_argument(
        "--labels",
        choices=("all", "query", "none"),
        default="all",
        help="Node label mode. Default: all",
    )
    parser.add_argument(
        "--width",
        type=positive_float,
        default=DEFAULT_WIDTH,
        help=f"Figure width in points/CSS pixels. Default: {DEFAULT_WIDTH:g}",
    )
    parser.add_argument(
        "--stage-height",
        type=positive_float,
        default=DEFAULT_STAGE_HEIGHT,
        help=f"Network stage height. Default: {DEFAULT_STAGE_HEIGHT:g}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Vector output path ending in .svg or .pdf.",
    )
    return parser


def validate_payload(payload: dict[str, Any]) -> None:
    summary = payload.get("summary")
    elements = payload.get("elements")
    if not isinstance(summary, dict) or not isinstance(elements, dict):
        raise RuntimeError("WGCNA API response is missing summary or elements")
    nodes = elements.get("nodes")
    edges = elements.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise RuntimeError("WGCNA API response has invalid nodes or edges")
    for item in [*nodes, *edges]:
        if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
            raise RuntimeError("WGCNA API response contains an invalid graph element")


def load_query_result(input_json: Path) -> tuple[str, dict[str, Any]]:
    try:
        document = json.loads(input_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"input is not valid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise ValueError("query result JSON must be an object")
    api_url = document.get("api_url")
    payload = document.get("data")
    if not isinstance(api_url, str) or not isinstance(payload, dict):
        raise ValueError(
            "input must be the {api_url, data} document written by "
            "query_wgcna_network.py coexpression --output-json"
        )

    parsed = urllib.parse.urlsplit(api_url)
    endpoint = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )
    if endpoint != f"{PRODUCTION_API_ROOT}/coexpression":
        raise ValueError(
            "input api_url must be the production Potato Agent WGCNA coexpression endpoint"
        )
    validate_payload(payload)
    return api_url, payload


def query_note_from_url(api_url: str) -> str:
    parsed = urllib.parse.urlsplit(api_url)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def first(name: str, default: str) -> str:
        values = params.get(name)
        return values[0] if values else default

    networks = first("networks", "all")
    top_n = first("top_n", "50")
    tom_min = first("tom_min", "")
    same_module = first("same_module_only", "true").lower() == "true"
    tom_note = "any TOM" if not tom_min else f"TOM >= {tom_min}"
    return (
        f"{networks} | top {top_n} | {tom_note} | "
        f"{'same module' if same_module else 'all modules'}"
    )


def short_gene_label(gene_id: Any) -> str:
    text = str(gene_id or "")
    match = re.search(r"chr([0-9A-Za-z]+)G([0-9]+)$", text)
    if match:
        return f"chr{match.group(1)}G{match.group(2)}"
    return f"{text[:9]}...{text[-6:]}" if len(text) > 18 else text


def safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def edge_width(data: dict[str, Any]) -> float:
    if data.get("edge_type") == "same_gene":
        return 0.65
    rank = max(1.0, safe_number(data.get("rank"), 100.0))
    base = 0.35 + 1.35 / math.sqrt(rank)
    return min(1.7, base + (0.15 if data.get("shared_coexpression") else 0.0))


def hex_rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) / 255.0 for index in (0, 2, 4))


def blend_hex(color: str, opacity: float) -> str:
    opacity = max(0.0, min(1.0, opacity))
    channels = [round((channel * opacity + (1.0 - opacity)) * 255) for channel in hex_rgb(color)]
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def estimated_text_width(value: Any, size: float) -> float:
    width = 0.0
    for char in str(value or ""):
        if char in "ilI1.,:;'|":
            width += 0.31
        elif char in "MW@%":
            width += 0.88
        else:
            width += 0.58
    return width * size


def pdf_escape(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class SvgCanvas:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height
        self.parts = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" '
                f'height="{height:g}" viewBox="0 0 {width:g} {height:g}">'
            ),
            '<rect width="100%" height="100%" fill="#f4f7fc"/>',
        ]

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        line_width: float = 1.0,
        opacity: float = 1.0,
        radius: float = 0.0,
    ) -> None:
        attrs = [f'x="{x:.3f}"', f'y="{y:.3f}"', f'width="{width:.3f}"', f'height="{height:.3f}"']
        if radius:
            attrs.extend((f'rx="{radius:.3f}"', f'ry="{radius:.3f}"'))
        attrs.append(f'fill="{fill or "none"}"')
        if stroke:
            attrs.extend((f'stroke="{stroke}"', f'stroke-width="{line_width:.3f}"'))
        if opacity < 1.0:
            attrs.append(f'opacity="{opacity:.3f}"')
        self.parts.append(f"<rect {' '.join(attrs)}/>")

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str,
        line_width: float = 1.0,
        opacity: float = 1.0,
        dashed: bool = False,
    ) -> None:
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        self.parts.append(
            f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
            f'stroke="{color}" stroke-width="{line_width:.3f}" opacity="{opacity:.3f}"{dash}/>'
        )

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        *,
        fill: str,
        stroke: str,
        line_width: float,
    ) -> None:
        self.parts.append(
            f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{radius:.3f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{line_width:.3f}"/>'
        )

    def polygon(
        self,
        points: Sequence[tuple[float, float]],
        *,
        fill: str,
        stroke: str,
        line_width: float,
    ) -> None:
        rendered = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self.parts.append(
            f'<polygon points="{rendered}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{line_width:.3f}"/>'
        )

    def text(
        self,
        value: Any,
        x: float,
        y: float,
        *,
        size: float,
        color: str,
        align: str = "left",
        bold: bool = False,
    ) -> None:
        anchor = {"left": "start", "center": "middle", "right": "end"}[align]
        weight = 800 if bold else 600
        self.parts.append(
            f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="{anchor}" '
            f'font-family="Inter,Arial,sans-serif" font-size="{size:.3f}" '
            f'font-weight="{weight}" fill="{color}">{xml_escape(str(value or ""))}</text>'
        )

    def finish(self) -> str:
        return "\n".join([*self.parts, "</svg>", ""])


class PdfCanvas:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height
        self.commands: list[str] = []

    @staticmethod
    def _color(color: str) -> str:
        return " ".join(f"{channel:.5f}" for channel in hex_rgb(color))

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        line_width: float = 1.0,
        opacity: float = 1.0,
        radius: float = 0.0,
    ) -> None:
        del radius
        effective_fill = blend_hex(fill, opacity) if fill and opacity < 1.0 else fill
        effective_stroke = blend_hex(stroke, opacity) if stroke and opacity < 1.0 else stroke
        parts = ["q"]
        if effective_fill:
            parts.append(f"{self._color(effective_fill)} rg")
        if effective_stroke:
            parts.append(f"{self._color(effective_stroke)} RG {line_width:.3f} w")
        operation = "B" if effective_fill and effective_stroke else ("f" if effective_fill else "S")
        parts.append(
            f"{x:.3f} {self.height - y - height:.3f} {width:.3f} {height:.3f} re {operation}"
        )
        parts.append("Q")
        self.commands.append(" ".join(parts))

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str,
        line_width: float = 1.0,
        opacity: float = 1.0,
        dashed: bool = False,
    ) -> None:
        effective = blend_hex(color, opacity) if opacity < 1.0 else color
        dash = "[6 5] 0 d " if dashed else ""
        self.commands.append(
            f"q {self._color(effective)} RG {line_width:.3f} w {dash}"
            f"{x1:.3f} {self.height - y1:.3f} m {x2:.3f} {self.height - y2:.3f} l S Q"
        )

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        *,
        fill: str,
        stroke: str,
        line_width: float,
    ) -> None:
        k = radius * 0.5522847498
        cy = self.height - y
        path = (
            f"{x + radius:.3f} {cy:.3f} m "
            f"{x + radius:.3f} {cy + k:.3f} {x + k:.3f} {cy + radius:.3f} {x:.3f} {cy + radius:.3f} c "
            f"{x - k:.3f} {cy + radius:.3f} {x - radius:.3f} {cy + k:.3f} {x - radius:.3f} {cy:.3f} c "
            f"{x - radius:.3f} {cy - k:.3f} {x - k:.3f} {cy - radius:.3f} {x:.3f} {cy - radius:.3f} c "
            f"{x + k:.3f} {cy - radius:.3f} {x + radius:.3f} {cy - k:.3f} {x + radius:.3f} {cy:.3f} c"
        )
        self.commands.append(
            f"q {self._color(fill)} rg {self._color(stroke)} RG {line_width:.3f} w {path} B Q"
        )

    def polygon(
        self,
        points: Sequence[tuple[float, float]],
        *,
        fill: str,
        stroke: str,
        line_width: float,
    ) -> None:
        commands = []
        for index, (x, y) in enumerate(points):
            commands.append(f"{x:.3f} {self.height - y:.3f} {'m' if index == 0 else 'l'}")
        self.commands.append(
            f"q {self._color(fill)} rg {self._color(stroke)} RG {line_width:.3f} w "
            f"{' '.join(commands)} h B Q"
        )

    def text(
        self,
        value: Any,
        x: float,
        y: float,
        *,
        size: float,
        color: str,
        align: str = "left",
        bold: bool = False,
    ) -> None:
        text = str(value or "")
        if align == "center":
            x -= estimated_text_width(text, size) / 2.0
        elif align == "right":
            x -= estimated_text_width(text, size)
        font = "F2" if bold else "F1"
        baseline = self.height - y
        self.commands.append(
            f"q {self._color(color)} rg BT /{font} {size:.3f} Tf 1 0 0 1 "
            f"{x:.3f} {baseline:.3f} Tm ({pdf_escape(text)}) Tj ET Q"
        )


def compute_positions(
    nodes: Sequence[dict[str, Any]],
    width: float,
    height: float,
    network_order: Sequence[str] | None = None,
) -> dict[str, tuple[float, float]]:
    ordered_networks = list(network_order or NETWORKS)
    columns = min(3 if width > 1000 else 2, max(1, len(ordered_networks)))
    rows = math.ceil(len(ordered_networks) / columns)
    cell_width = width / columns
    cell_height = height / rows
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        grouped[str(node.get("data", {}).get("network_id") or "unknown")].append(node)

    positions: dict[str, tuple[float, float]] = {}
    for network_index, network in enumerate(ordered_networks):
        group = grouped.get(network, [])
        column = network_index % columns
        row = network_index // columns
        center_x = column * cell_width + cell_width / 2.0
        center_y = row * cell_height + cell_height / 2.0
        query_nodes = [node for node in group if node["data"].get("is_query_gene")]
        other_nodes = [node for node in group if not node["data"].get("is_query_gene")]

        center_count = max(len(query_nodes), 1)
        center_radius = min(42.0, max(0.0, center_count * 8.0))
        for index, node in enumerate(query_nodes):
            angle = math.tau * index / center_count
            positions[str(node["data"]["id"])] = (
                center_x + math.cos(angle) * center_radius,
                center_y + math.sin(angle) * center_radius,
            )

        ring_count = max(len(other_nodes), 1)
        radius = max(78.0, min(cell_width, cell_height) * 0.34)
        for index, node in enumerate(other_nodes):
            angle = math.tau * index / ring_count
            ring_offset = (index % 7) * 4.0
            positions[str(node["data"]["id"])] = (
                center_x + math.cos(angle) * (radius + ring_offset),
                center_y + math.sin(angle) * (radius + ring_offset),
            )
    return positions


def selected_networks(payload: dict[str, Any]) -> list[str]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    raw = summary.get("networks") if isinstance(summary, dict) else []
    selected = [network for network in NETWORKS if network in (raw or [])]
    return selected or list(NETWORKS)


def figure_height(payload: dict[str, Any], stage_height: float) -> float:
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    warning_lines = min(3, len(warnings))
    return 76.0 + stage_height + 104.0 + warning_lines * 17.0


def draw_label_background(canvas: Any, label: str, x: float, y: float, size: float) -> None:
    width = estimated_text_width(label, size) + 6.0
    canvas.rect(x - width / 2.0, y - size + 1.0, width, size + 4.0, fill="#ffffff", opacity=0.82)


def draw_figure(
    canvas: Any,
    payload: dict[str, Any],
    *,
    api_url: str,
    stage_height: float,
    label_mode: str,
    query_note: str,
) -> None:
    width = canvas.width
    stage_top = 76.0
    graph_left = 18.0
    graph_top = stage_top + 20.0
    graph_width = width - 36.0
    graph_height = stage_height - 40.0
    elements = payload["elements"]
    nodes = elements["nodes"]
    edges = elements["edges"]
    summary = payload["summary"]
    query_genes = [str(value) for value in payload.get("query_genes") or []]
    title = ", ".join(query_genes) if query_genes else "No query loaded"
    if len(title) > 112:
        title = f"{len(query_genes)} query genes"

    canvas.rect(0.0, 0.0, width, canvas.height, fill="#ffffff", stroke=PANEL_BORDER, radius=8.0)
    canvas.text(title, 18.0, 29.0, size=16.0, color=TEXT_COLOR, bold=True)
    canvas.text(
        f"{summary.get('node_count', len(nodes))} nodes | {summary.get('edge_count', len(edges))} edges | "
        f"{summary.get('tom_edge_count', 0)} TOM edges",
        18.0,
        51.0,
        size=11.0,
        color=MUTED_COLOR,
        bold=True,
    )
    canvas.text("WGCNA Network", width - 18.0, 29.0, size=14.0, color="#166534", align="right", bold=True)
    canvas.text(query_note, width - 18.0, 51.0, size=9.0, color=MUTED_COLOR, align="right")
    canvas.line(0.0, stage_top, width, stage_top, color=PANEL_BORDER)

    canvas.rect(0.0, stage_top, width, stage_height, fill="#ffffff")
    for x in range(0, math.ceil(width) + 1, 36):
        canvas.line(float(x), stage_top, float(x), stage_top + stage_height, color=GRID_COLOR)
    for offset in range(0, math.ceil(stage_height) + 1, 36):
        canvas.line(0.0, stage_top + offset, width, stage_top + offset, color=GRID_COLOR)

    chosen_networks = selected_networks(payload)
    positions = compute_positions(nodes, graph_width, graph_height, chosen_networks)
    columns = min(3 if graph_width > 1000 else 2, max(1, len(chosen_networks)))
    rows = math.ceil(len(chosen_networks) / columns)
    cell_width = graph_width / columns
    cell_height = graph_height / rows
    for network_index, network in enumerate(chosen_networks):
        column = network_index % columns
        row = network_index // columns
        x = graph_left + column * cell_width + 8.0
        y = graph_top + row * cell_height + 8.0
        canvas.rect(
            x,
            y,
            cell_width - 16.0,
            cell_height - 16.0,
            stroke=blend_hex(NETWORK_COLORS[network], 0.18),
            line_width=0.7,
            radius=6.0,
        )
        canvas.circle(x + 12.0, y + 14.0, 4.0, fill=NETWORK_COLORS[network], stroke=NETWORK_COLORS[network], line_width=1.0)
        canvas.text(NETWORK_LABELS[network], x + 22.0, y + 18.0, size=10.0, color=NETWORK_COLORS[network], bold=True)

    shifted = {
        node_id: (graph_left + x, graph_top + y)
        for node_id, (x, y) in positions.items()
    }
    for edge in edges:
        data = edge["data"]
        source = shifted.get(str(data.get("source") or ""))
        target = shifted.get(str(data.get("target") or ""))
        if source is None or target is None:
            continue
        same_gene = data.get("edge_type") == "same_gene"
        color = MUTED_COLOR if same_gene else NETWORK_COLORS.get(str(data.get("network_id")), MUTED_COLOR)
        opacity = 0.32 if same_gene else (0.60 if data.get("shared_coexpression") else 0.38)
        canvas.line(
            source[0],
            source[1],
            target[0],
            target[1],
            color=color,
            line_width=edge_width(data),
            opacity=opacity,
            dashed=same_gene,
        )

    for node in nodes:
        data = node["data"]
        position = shifted.get(str(data.get("id") or ""))
        if position is None:
            continue
        network = str(data.get("network_id") or "")
        query = bool(data.get("is_query_gene"))
        radius = 20.0 if query else 11.0
        fill = NETWORK_NODE_FILLS.get(network, "#475569") if query else NETWORK_NODE_SOFT_FILLS.get(network, "#e2e8f0")
        stroke = QUERY_BORDER if query else NETWORK_COLORS.get(network, "#334155")
        if query:
            canvas.polygon(
                (
                    (position[0], position[1] - radius),
                    (position[0] + radius, position[1]),
                    (position[0], position[1] + radius),
                    (position[0] - radius, position[1]),
                ),
                fill=fill,
                stroke=stroke,
                line_width=3.0,
            )
        else:
            canvas.circle(position[0], position[1], radius, fill=fill, stroke=stroke, line_width=2.0)

        show_label = label_mode == "all" or (label_mode == "query" and query)
        if show_label:
            label = short_gene_label(data.get("gene_id"))
            size = 10.0 if query else 8.0
            label_y = position[1] - radius - 6.0
            draw_label_background(canvas, label, position[0], label_y, size)
            canvas.text(label, position[0], label_y, size=size, color=TEXT_COLOR, align="center", bold=query)

    if not nodes:
        canvas.text(
            "No network loaded",
            width / 2.0,
            stage_top + stage_height / 2.0,
            size=15.0,
            color=MUTED_COLOR,
            align="center",
            bold=True,
        )

    legend_top = stage_top + stage_height + 21.0
    legend_x = 20.0
    for network in NETWORKS:
        if network not in chosen_networks:
            continue
        canvas.circle(legend_x + 5.0, legend_top, 5.0, fill=NETWORK_NODE_FILLS[network], stroke=NETWORK_COLORS[network], line_width=1.5)
        canvas.text(NETWORK_LABELS[network], legend_x + 15.0, legend_top + 4.0, size=10.0, color=TEXT_COLOR, bold=True)
        legend_x += estimated_text_width(NETWORK_LABELS[network], 10.0) + 42.0

    semantics_y = legend_top + 30.0
    canvas.polygon(
        ((25.0, semantics_y - 8.0), (33.0, semantics_y), (25.0, semantics_y + 8.0), (17.0, semantics_y)),
        fill="#475569",
        stroke=QUERY_BORDER,
        line_width=2.0,
    )
    canvas.text("Query gene", 40.0, semantics_y + 4.0, size=10.0, color=TEXT_COLOR, bold=True)
    canvas.circle(130.0, semantics_y, 6.0, fill="#e2e8f0", stroke=MUTED_COLOR, line_width=1.5)
    canvas.text("Neighbor", 143.0, semantics_y + 4.0, size=10.0, color=TEXT_COLOR, bold=True)
    canvas.line(220.0, semantics_y, 258.0, semantics_y, color=MUTED_COLOR, line_width=1.4, opacity=0.6)
    canvas.text("TOM edge", 267.0, semantics_y + 4.0, size=10.0, color=TEXT_COLOR, bold=True)
    canvas.line(347.0, semantics_y, 385.0, semantics_y, color=MUTED_COLOR, line_width=0.8, opacity=0.5, dashed=True)
    canvas.text("Same gene across networks", 394.0, semantics_y + 4.0, size=10.0, color=TEXT_COLOR, bold=True)

    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    warning_y = semantics_y + 27.0
    for index, warning in enumerate(warnings[:3]):
        text = str(warning)
        if len(text) > 180:
            text = text[:177] + "..."
        canvas.text(f"Warning: {text}", 20.0, warning_y + index * 17.0, size=9.0, color=WARNING_COLOR, bold=True)

    source_y = canvas.height - 13.0
    canvas.text(f"Source: {api_url.split('?', 1)[0]}", 20.0, source_y, size=8.5, color=MUTED_COLOR)
    canvas.text("Vector output | no embedded raster images", width - 20.0, source_y, size=8.5, color=MUTED_COLOR, align="right")


def write_pdf(commands: Sequence[str], width: float, height: float, title: str) -> bytes:
    content = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.3f} {height:.3f}] "
            "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        f"<< /Title ({pdf_escape(title)}) /Creator (Potato Agent wgcna-network-query) >>".encode("latin-1", errors="replace"),
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
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 7 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def render_svg(
    payload: dict[str, Any],
    *,
    api_url: str,
    width: float,
    stage_height: float,
    label_mode: str,
    query_note: str,
) -> str:
    canvas = SvgCanvas(width, figure_height(payload, stage_height))
    draw_figure(
        canvas,
        payload,
        api_url=api_url,
        stage_height=stage_height,
        label_mode=label_mode,
        query_note=query_note,
    )
    return canvas.finish()


def render_pdf(
    payload: dict[str, Any],
    *,
    api_url: str,
    width: float,
    stage_height: float,
    label_mode: str,
    query_note: str,
) -> bytes:
    canvas = PdfCanvas(width, figure_height(payload, stage_height))
    draw_figure(
        canvas,
        payload,
        api_url=api_url,
        stage_height=stage_height,
        label_mode=label_mode,
        query_note=query_note,
    )
    title = ", ".join(str(value) for value in payload.get("query_genes") or []) or "WGCNA Network"
    return write_pdf(canvas.commands, canvas.width, canvas.height, title)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        input_json = args.input_json.expanduser().resolve()
        output_path = args.output.expanduser().resolve()
        suffix = output_path.suffix.lower()
        if suffix not in {".svg", ".pdf"}:
            raise ValueError("--output must end in .svg or .pdf; raster output is not supported")
        if args.width < 800:
            raise ValueError("--width must be at least 800")
        if args.stage_height < 480:
            raise ValueError("--stage-height must be at least 480")
        api_url, payload = load_query_result(input_json)
        query_note = query_note_from_url(api_url)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".svg":
            document = render_svg(
                payload,
                api_url=api_url,
                width=args.width,
                stage_height=args.stage_height,
                label_mode=args.labels,
                query_note=query_note,
            )
            output_path.write_text(document, encoding="utf-8")
            format_label = "vector SVG"
        else:
            document = render_pdf(
                payload,
                api_url=api_url,
                width=args.width,
                stage_height=args.stage_height,
                label_mode=args.labels,
                query_note=query_note,
            )
            output_path.write_bytes(document)
            format_label = "vector PDF"

        print(
            json.dumps(
                {
                    "api_url": api_url,
                    "input_json": str(input_json),
                    "genes": payload.get("query_genes", []),
                    "networks": payload["summary"].get("networks", []),
                    "node_count": payload["summary"].get("node_count", 0),
                    "edge_count": payload["summary"].get("edge_count", 0),
                    "tom_edge_count": payload["summary"].get("tom_edge_count", 0),
                    "warnings": payload.get("warnings", []),
                    "width": args.width,
                    "height": figure_height(payload, args.stage_height),
                    "labels": args.labels,
                    "output": str(output_path),
                    "format": format_label,
                    "embeddedRasterImages": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except BrokenPipeError:
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

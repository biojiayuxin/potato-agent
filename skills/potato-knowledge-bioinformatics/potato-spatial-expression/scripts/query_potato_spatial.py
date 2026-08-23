#!/usr/bin/env python3
"""Query and reproduce Potato Agent spatial expression viewer plots."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


PRODUCTION_BASE_URL = "https://potato-agent.ynnu.edu.cn"
DEFAULT_BASE_URL = PRODUCTION_BASE_URL
TIMEOUT_SECONDS = 60
DEFAULT_WIDTH = 2000
DEFAULT_WORKERS = 6
DEFAULT_OUTPUT_FORMAT = "pdf"
PDF_POINTS_PER_PIXEL = 72.0 / 150.0
USER_AGENT = "potato-spatial-expression/2.0"

# Synchronized with interface/static/spatial/app.js.
PANEL_PADDING = 160
PANEL_GUTTER = 420
PANEL_ROW_GUTTER = 520
PANEL_LABEL_HEIGHT = 220
REDS = [
    (255, 245, 240),
    (254, 224, 210),
    (252, 187, 161),
    (252, 146, 114),
    (251, 106, 74),
    (239, 59, 44),
    (203, 24, 29),
    (165, 15, 21),
    (103, 0, 13),
]
DATASET_LABEL_OVERRIDES = {
    "S1 + S2": "Stolon and tuber",
    "S1 + Stem": "Stolon and Stem",
}
SAMPLE_LABEL_OVERRIDES = {
    "S1": "Stolon (S1)",
    "S2": "Early Swelling Tuber (S2)",
}
DOTPLOT_TSV_FIELDS = [
    "dataset",
    "gene",
    "cluster_column",
    "cluster_id",
    "cluster_label",
    "cluster_name",
    "order",
    "cell_count",
    "expressing_count",
    "pct_expr",
    "avg_expr",
    "avg_expr_scaled",
]


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url",
        default=PRODUCTION_BASE_URL,
        help=(
            "Potato Agent origin. Production is authoritative and is the default: "
            f"{PRODUCTION_BASE_URL}"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds. Default: {TIMEOUT_SECONDS}.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query and reproduce Potato Agent spatial expression viewer plots."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    datasets = subparsers.add_parser("datasets", help="Print the live spatial dataset catalog.")
    add_common_arguments(datasets)

    expression = subparsers.add_parser(
        "expression", help="Print aggregate cluster and tissue expression statistics."
    )
    add_common_arguments(expression)
    expression.add_argument("gene", help="Exact spatial gene ID, such as Soltu.DM.03G024100.")
    expression.add_argument("--dataset", required=True, help="Spatial dataset ID.")

    dotplot = subparsers.add_parser(
        "dotplot", help="Reproduce the Interface cluster dotplot plus TSV and JSON."
    )
    add_common_arguments(dotplot)
    dotplot.add_argument("gene", help="Exact spatial gene ID, such as Soltu.DM.03G024100.")
    dotplot.add_argument("--dataset", required=True, help="Spatial dataset ID.")
    dotplot.add_argument("--outdir", default="spatial_plots", help="Output directory.")
    dotplot.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="Render width in pixels.")
    dotplot.add_argument(
        "--format",
        dest="output_format",
        choices=("pdf", "png"),
        default=DEFAULT_OUTPUT_FORMAT,
        help=f"Plot format. Default: {DEFAULT_OUTPUT_FORMAT}.",
    )

    plot = subparsers.add_parser(
        "plot", help="Generate Interface-equivalent spatial expression maps and cluster dotplot."
    )
    add_common_arguments(plot)
    plot.add_argument("gene", help="Exact spatial gene ID, such as Soltu.DM.03G024100.")
    plot.add_argument("--dataset", required=True, help="Spatial dataset ID.")
    plot.add_argument(
        "--sample",
        action="append",
        help="Sample ID to render. Repeat as needed; omit to render every dataset sample.",
    )
    plot.add_argument("--outdir", default="spatial_plots", help="Output directory.")
    plot.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="Render width in pixels.")
    plot.add_argument(
        "--format",
        dest="output_format",
        choices=("pdf", "png"),
        default=DEFAULT_OUTPUT_FORMAT,
        help=f"Plot format. Default: {DEFAULT_OUTPUT_FORMAT}.",
    )
    plot.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Concurrent contour downloads (1-16). Default: {DEFAULT_WORKERS}.",
    )
    return parser


def normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("--base-url must be an absolute HTTP(S) origin")
    return value


def request_json_url(url: str, timeout: int) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {error_body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to connect to {url}: {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: {body[:500]}") from exc


def api_url(base_url: str, path: str, params: dict[str, str] | None = None) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{normalize_base_url(base_url)}{normalized_path}"
    return f"{url}?{urllib.parse.urlencode(params)}" if params else url


def request_api(
    base_url: str,
    path: str,
    timeout: int,
    params: dict[str, str] | None = None,
) -> Any:
    return request_json_url(api_url(base_url, path, params), timeout)


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is not a JSON object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} is not a JSON array")
    return value


def request_expression(base_url: str, dataset: str, gene: str, timeout: int) -> dict[str, Any]:
    return require_object(
        request_api(
            base_url,
            "/api/spatial/agent/expression",
            timeout,
            {"dataset": dataset, "gene": gene},
        ),
        "aggregate expression response",
    )


def safe_filename_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return safe or "spatial_expression"


def dataset_display_label(dataset: dict[str, Any]) -> str:
    label = str(dataset.get("label") or dataset.get("id") or "")
    return DATASET_LABEL_OVERRIDES.get(label, label)


def sample_display_label(sample: dict[str, Any]) -> str:
    sample_id = str(sample.get("id") or "")
    label = str(sample.get("label") or sample_id)
    return SAMPLE_LABEL_OVERRIDES.get(sample_id, SAMPLE_LABEL_OVERRIDES.get(label, label))


def find_dataset(catalog: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    datasets = require_list(catalog.get("datasets"), "dataset catalog field 'datasets'")
    for dataset in datasets:
        if isinstance(dataset, dict) and str(dataset.get("id")) == dataset_id:
            return dataset
    available = ", ".join(
        str(item.get("id")) for item in datasets if isinstance(item, dict) and item.get("id")
    )
    raise RuntimeError(f"dataset {dataset_id!r} not found; available: {available or 'none'}")


def select_samples(dataset: dict[str, Any], requested: list[str] | None) -> list[dict[str, Any]]:
    samples = [
        item
        for item in require_list(dataset.get("samples"), "dataset samples")
        if isinstance(item, dict)
    ]
    if not requested:
        return samples
    by_id = {str(sample.get("id")): sample for sample in samples}
    missing = [sample_id for sample_id in requested if sample_id not in by_id]
    if missing:
        raise RuntimeError(
            f"sample(s) not found in dataset {dataset.get('id')}: {', '.join(missing)}; "
            f"available: {', '.join(by_id)}"
        )
    seen: set[str] = set()
    selected = []
    for sample_id in requested:
        if sample_id not in seen:
            selected.append(by_id[sample_id])
            seen.add(sample_id)
    return selected


def same_origin_url(base_url: str, value: str) -> str:
    base_url = normalize_base_url(base_url)
    resolved = urllib.parse.urljoin(f"{base_url}/", value)
    base = urllib.parse.urlsplit(base_url)
    parsed = urllib.parse.urlsplit(resolved)
    if parsed.scheme not in {"http", "https"} or (parsed.scheme, parsed.netloc) != (
        base.scheme,
        base.netloc,
    ):
        raise RuntimeError(f"spatial API returned an off-origin resource URL: {value}")
    return resolved


def spatial_data_url(base_url: str, raw_path: str, dataset_id: str) -> str:
    raw = str(raw_path or "").strip()
    if not raw:
        raise RuntimeError("spatial API returned an empty resource URL")
    if raw.startswith(("http://", "https://", "/api/spatial/data")):
        return same_origin_url(base_url, raw)
    if raw.startswith("/dataset-data/"):
        parts = raw.lstrip("/").split("/")
        selected_dataset = parts[1] if len(parts) > 1 and parts[1] else dataset_id
        rest = "/".join(urllib.parse.quote(part) for part in parts[2:])
        path = f"/api/spatial/data/{urllib.parse.quote(selected_dataset)}"
        return same_origin_url(base_url, f"{path}/{rest}" if rest else path)
    if raw == "/data":
        return same_origin_url(base_url, "/api/spatial/data/_root/data")
    if raw.startswith("/data/"):
        rest = "/".join(urllib.parse.quote(part) for part in raw[len("/data/") :].split("/"))
        return same_origin_url(base_url, f"/api/spatial/data/_root/data/{rest}")
    if raw.startswith("/"):
        return same_origin_url(base_url, raw)
    rest = "/".join(urllib.parse.quote(part) for part in raw.split("/"))
    return same_origin_url(
        base_url, f"/api/spatial/data/{urllib.parse.quote(dataset_id)}/{rest}"
    )


def sample_manifest_url(base_url: str, dataset: dict[str, Any], sample: dict[str, Any]) -> str:
    dataset_id = str(dataset.get("id") or "")
    if sample.get("contoursPath"):
        root = spatial_data_url(base_url, str(sample["contoursPath"]), dataset_id)
    else:
        data_path = str(dataset.get("dataPath") or f"/api/spatial/data/{dataset_id}")
        root = same_origin_url(base_url, data_path)
        root = f"{root.rstrip('/')}/contours/{urllib.parse.quote(str(sample.get('id') or ''))}"
    return f"{root.rstrip('/')}/manifest.json"


def bbox_width(bbox: list[Any]) -> float:
    return float(bbox[2]) - float(bbox[0]) + 1.0


def bbox_height(bbox: list[Any]) -> float:
    return float(bbox[3]) - float(bbox[1]) + 1.0


def normalize_replicates(sample_payload: Any) -> list[dict[str, Any]]:
    source = (
        sample_payload
        if isinstance(sample_payload, list)
        else sample_payload.get("replicates") or []
        if isinstance(sample_payload, dict)
        else []
    )
    replicates = []
    for raw in source:
        if not isinstance(raw, dict):
            continue
        rep = dict(raw)
        rep["cellIds"] = rep.get("cellIds") or rep.get("cells") or []
        raw_keys = rep.get("tileKeys")
        if raw_keys is None:
            raw_keys = [f"{item[0]},{item[1]}" for item in rep.get("tiles") or []]
        rep["tileKeys"] = [str(item) for item in raw_keys]
        replicates.append(rep)
    return replicates


def prepare_spatial_layout(
    manifest: dict[str, Any],
    replicate_payload: Any,
    columns: int | None,
) -> dict[str, Any]:
    replicates = normalize_replicates(replicate_payload)
    if not replicates:
        raise RuntimeError(f"no replicate metadata for sample {manifest.get('sample')}")
    sample_id = str(manifest.get("sample") or "")
    reference = next(
        (rep for rep in replicates if str(rep.get("id")) == f"{sample_id.lower()}_rep1"),
        replicates[0],
    )
    reference_bbox = require_list(reference.get("bbox"), "replicate bbox")
    display_width = bbox_width(reference_bbox)
    display_height = bbox_height(reference_bbox)
    panel_columns = max(1, int(columns or len(replicates) or 1))
    panel_rows = max(1, math.ceil(len(replicates) / panel_columns))
    panels = []
    cell_to_panel: dict[int, dict[str, Any]] = {}
    needed_tile_keys: set[str] = set()

    for index, rep in enumerate(replicates):
        bbox = require_list(rep.get("bbox"), "replicate bbox")
        source_width = bbox_width(bbox)
        source_height = bbox_height(bbox)
        panel = {
            **rep,
            "bbox": [float(item) for item in bbox],
            "x": PANEL_PADDING + (index % panel_columns) * (display_width + PANEL_GUTTER),
            "y": PANEL_LABEL_HEIGHT
            + PANEL_PADDING
            + (index // panel_columns) * (display_height + PANEL_ROW_GUTTER),
            "width": display_width,
            "height": display_height,
            "scaleX": display_width / source_width,
            "scaleY": display_height / source_height,
        }
        panels.append(panel)
        for raw_cell_id in rep.get("cellIds") or []:
            try:
                cell_to_panel[int(raw_cell_id)] = panel
            except (TypeError, ValueError):
                continue
        needed_tile_keys.update(str(key) for key in rep.get("tileKeys") or [])

    return {
        "sample": sample_id,
        "panels": panels,
        "cellToPanel": cell_to_panel,
        "neededTileKeys": needed_tile_keys,
        "layoutWidth": PANEL_PADDING * 2
        + panel_columns * display_width
        + (panel_columns - 1) * PANEL_GUTTER,
        "layoutHeight": PANEL_LABEL_HEIGHT
        + PANEL_PADDING * 2
        + panel_rows * display_height
        + (panel_rows - 1) * PANEL_ROW_GUTTER,
    }


def load_contour_cells(
    base_url: str,
    dataset_id: str,
    manifest: dict[str, Any],
    needed_keys: set[str],
    timeout: int,
    workers: int,
) -> list[dict[str, Any]]:
    tiles = [
        item
        for item in require_list(manifest.get("tiles"), "contour manifest tiles")
        if isinstance(item, dict)
    ]
    selected = [
        tile
        for tile in tiles
        if not needed_keys or f"{tile.get('x')},{tile.get('y')}" in needed_keys
    ]
    urls = [spatial_data_url(base_url, str(tile.get("url") or ""), dataset_id) for tile in selected]

    def fetch(url: str) -> dict[str, Any]:
        return require_object(request_json_url(url, timeout), f"contour tile {url}")

    cells: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(16, workers))) as executor:
        for payload in executor.map(fetch, urls):
            cells.extend(
                item
                for item in require_list(payload.get("cells"), "contour tile cells")
                if isinstance(item, dict)
            )
    return cells


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def color_for_value(
    value: float, value_range: dict[str, Any], *, scaled: bool = False
) -> tuple[int, int, int]:
    minimum = number(value_range.get("vmin"), -2.5 if scaled else 0.0)
    maximum = number(value_range.get("vmax"), 2.5 if scaled else 0.0)
    fraction = (
        max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))
        if maximum > minimum
        else 0.5
        if scaled
        else 0.0
    )
    position = fraction * (len(REDS) - 1)
    index = min(len(REDS) - 2, math.floor(position))
    local = position - index
    start, end = REDS[index], REDS[index + 1]
    return tuple(round(start[i] + (end[i] - start[i]) * local) for i in range(3))


def invert_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(255 - component for component in rgb)


def import_pillow() -> tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required for PNG output (it is bundled with Hermes Lite)"
        ) from exc
    return Image, ImageDraw, ImageFont


def load_font(ImageFont: Any, size: int, *, bold: bool = False) -> Any:
    names = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def format_number(value: Any, digits: int = 3) -> str:
    numeric = number(value, float("nan"))
    if not math.isfinite(numeric):
        return "-"
    if abs(numeric) >= 1000:
        return f"{numeric:,.0f}"
    return f"{numeric:.{digits}f}".rstrip("0").rstrip(".") or "0"


def draw_horizontal_gradient(
    draw: Any, box: tuple[int, int, int, int], antialias: int
) -> None:
    left, top, right, bottom = box
    width = max(1, right - left)
    for offset in range(width):
        rgb = color_for_value(offset / max(1, width - 1), {"vmin": 0.0, "vmax": 1.0})
        draw.rectangle((left + offset, top, left + offset + 1, bottom), fill=rgb)
    draw.rectangle(box, outline=(208, 180, 170), width=max(1, antialias))


def save_png_image(image: Any, output_path: Path) -> None:
    if output_path.suffix.lower() != ".png":
        raise RuntimeError("raster plot output path must end in .png")
    image.save(output_path, format="PNG", optimize=True)


def pdf_color(rgb: tuple[int, int, int]) -> str:
    return " ".join(f"{component / 255.0:.5f}" for component in rgb)


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


class VectorPdfCanvas:
    """Small PDF drawing surface using logical top-left pixel coordinates."""

    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height
        self.commands: list[str] = []

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: tuple[int, int, int] | None = None,
        stroke: tuple[int, int, int] | None = None,
        line_width: float = 1.0,
    ) -> None:
        parts = ["q"]
        if fill is not None:
            parts.append(f"{pdf_color(fill)} rg")
        if stroke is not None:
            parts.append(f"{pdf_color(stroke)} RG {line_width:.3f} w")
        operation = "B" if fill is not None and stroke is not None else "f" if fill is not None else "S"
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
        color: tuple[int, int, int],
        line_width: float = 1.0,
    ) -> None:
        self.commands.append(
            f"q {pdf_color(color)} RG {line_width:.3f} w "
            f"{x1:.3f} {self.height - y1:.3f} m "
            f"{x2:.3f} {self.height - y2:.3f} l S Q"
        )

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        *,
        fill: tuple[int, int, int],
        stroke: tuple[int, int, int],
        line_width: float = 1.0,
    ) -> None:
        control = radius * 0.5522847498
        cy = self.height - y
        path = (
            f"{x + radius:.3f} {cy:.3f} m "
            f"{x + radius:.3f} {cy + control:.3f} "
            f"{x + control:.3f} {cy + radius:.3f} {x:.3f} {cy + radius:.3f} c "
            f"{x - control:.3f} {cy + radius:.3f} "
            f"{x - radius:.3f} {cy + control:.3f} {x - radius:.3f} {cy:.3f} c "
            f"{x - radius:.3f} {cy - control:.3f} "
            f"{x - control:.3f} {cy - radius:.3f} {x:.3f} {cy - radius:.3f} c "
            f"{x + control:.3f} {cy - radius:.3f} "
            f"{x + radius:.3f} {cy - control:.3f} {x + radius:.3f} {cy:.3f} c"
        )
        self.commands.append(
            f"q {pdf_color(fill)} rg {pdf_color(stroke)} RG {line_width:.3f} w {path} B Q"
        )

    def polygon(
        self,
        points: Sequence[tuple[float, float]],
        *,
        fill: tuple[int, int, int],
        stroke: tuple[int, int, int],
        line_width: float = 0.5,
    ) -> None:
        path = " ".join(
            f"{x:.3f} {self.height - y:.3f} {'m' if index == 0 else 'l'}"
            for index, (x, y) in enumerate(points)
        )
        self.commands.append(
            f"q {pdf_color(fill)} rg {pdf_color(stroke)} RG {line_width:.3f} w {path} h B Q"
        )

    def text(
        self,
        value: Any,
        x: float,
        y: float,
        *,
        size: float,
        color: tuple[int, int, int],
        align: str = "left",
        bold: bool = False,
    ) -> None:
        content = str(value or "")
        if align == "center":
            x -= estimated_text_width(content, size) / 2.0
        elif align == "right":
            x -= estimated_text_width(content, size)
        font = "F2" if bold else "F1"
        self.commands.append(
            f"q {pdf_color(color)} rg BT /{font} {size:.3f} Tf "
            f"1 0 0 1 {x:.3f} {self.height - y:.3f} Tm "
            f"({pdf_escape(content)}) Tj ET Q"
        )


def draw_vector_gradient(
    canvas: VectorPdfCanvas, x: float, y: float, width: float, height: float
) -> None:
    segments = max(32, min(256, round(width)))
    segment_width = width / segments
    for index in range(segments):
        rgb = color_for_value(
            index / max(1, segments - 1), {"vmin": 0.0, "vmax": 1.0}
        )
        canvas.rect(x + index * segment_width, y, segment_width + 0.01, height, fill=rgb)
    canvas.rect(x, y, width, height, stroke=(208, 180, 170), line_width=0.8)


def write_vector_pdf(
    output_path: Path, canvas: VectorPdfCanvas, *, title: str
) -> None:
    if output_path.suffix.lower() != ".pdf":
        raise RuntimeError("vector plot output path must end in .pdf")
    scale = PDF_POINTS_PER_PIXEL
    command_text = "\n".join(
        [f"q {scale:.6f} 0 0 {scale:.6f} 0 0 cm", *canvas.commands, "Q"]
    )
    compressed = zlib.compress(command_text.encode("latin-1", errors="replace"), level=9)
    page_width = canvas.width * scale
    page_height = canvas.height * scale
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width:.3f} {page_height:.3f}] "
            "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        (
            b"<< /Length "
            + str(len(compressed)).encode("ascii")
            + b" /Filter /FlateDecode >>\nstream\n"
            + compressed
            + b"\nendstream"
        ),
        (
            f"<< /Title ({pdf_escape(title)}) "
            "/Creator (Potato Agent potato-spatial-expression vector renderer) >>"
        ).encode("latin-1", errors="replace"),
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)


def render_spatial_png(
    output_path: Path,
    dataset: dict[str, Any],
    sample: dict[str, Any],
    gene_payload: dict[str, Any],
    layout: dict[str, Any],
    cells: Iterable[dict[str, Any]],
    width: int,
) -> dict[str, Any]:
    Image, ImageDraw, ImageFont = import_pillow()
    if width < 800:
        raise RuntimeError("--width must be at least 800 pixels")
    antialias, header_height, outer_margin = 2, 78, 36
    scale = (width - outer_margin * 2) / max(1.0, float(layout["layoutWidth"]))
    stage_height = max(
        520, math.ceil(float(layout["layoutHeight"]) * scale + outer_margin * 2)
    )
    height = header_height + stage_height
    work = Image.new("RGB", (width * antialias, height * antialias), (238, 241, 244))
    draw = ImageDraw.Draw(work)

    def sx(value: float) -> int:
        return round((outer_margin + value * scale) * antialias)

    def sy(value: float) -> int:
        return round((header_height + outer_margin + value * scale) * antialias)

    draw.rectangle((0, 0, width * antialias, header_height * antialias), fill=(255, 255, 255))
    title_font = load_font(ImageFont, 24 * antialias, bold=True)
    subtitle_font = load_font(ImageFont, 14 * antialias)
    sample_id = str(sample.get("id") or "")
    draw.text(
        (28 * antialias, 14 * antialias),
        f"{gene_payload.get('gene', '')} spatial expression",
        fill=(37, 48, 68),
        font=title_font,
    )
    draw.text(
        (30 * antialias, 48 * antialias),
        f"{dataset_display_label(dataset)} | {sample_display_label(sample)}",
        fill=(102, 112, 133),
        font=subtitle_font,
    )

    for panel in layout["panels"]:
        draw.rectangle(
            (
                sx(float(panel["x"])),
                sy(float(panel["y"])),
                sx(float(panel["x"]) + float(panel["width"])),
                sy(float(panel["y"]) + float(panel["height"])),
            ),
            fill=(255, 255, 255),
            outline=(184, 193, 204),
            width=antialias,
        )

    samples_payload = require_object(gene_payload.get("samples"), "gene response samples")
    sample_expression = require_object(
        samples_payload.get(sample_id), f"gene response sample {sample_id}"
    )
    expression_values: dict[int, float] = {}
    for item in require_list(sample_expression.get("values") or [], "sample expression values"):
        if not isinstance(item, list) or len(item) < 2:
            continue
        try:
            cell_id = int(item[0])
        except (TypeError, ValueError):
            continue
        value = number(item[1])
        expression_values[cell_id] = max(value, expression_values.get(cell_id, value))

    value_range = require_object(gene_payload.get("range"), "gene response range")
    drawn: set[int] = set()
    for cell in cells:
        try:
            cell_id = int(cell.get("id"))
        except (TypeError, ValueError):
            continue
        if cell_id in drawn:
            continue
        panel = layout["cellToPanel"].get(cell_id)
        bbox = cell.get("bbox") or []
        if panel is None or not isinstance(bbox, list) or len(bbox) < 2:
            continue
        fill = color_for_value(expression_values.get(cell_id, 0.0), value_range)
        for contour in cell.get("contours") or []:
            if not isinstance(contour, list) or len(contour) < 3:
                continue
            points = []
            for point in contour:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                source_x = number(bbox[0]) + number(point[0])
                source_y = number(bbox[1]) + number(point[1])
                layout_x = float(panel["x"]) + (
                    source_x - float(panel["bbox"][0])
                ) * float(panel["scaleX"])
                layout_y = float(panel["y"]) + (
                    source_y - float(panel["bbox"][1])
                ) * float(panel["scaleY"])
                points.append((sx(layout_x), sy(layout_y)))
            if len(points) >= 3:
                draw.polygon(points, fill=fill, outline=invert_rgb(fill), width=1)
        drawn.add(cell_id)

    label_font = load_font(ImageFont, 13 * antialias)
    for panel in layout["panels"]:
        label = (
            f"{panel.get('label') or panel.get('id') or ''} \u00b7 "
            f"{int(number(panel.get('assignedCellCount'), len(panel.get('cellIds') or []))):,} cells"
        )
        label_x = sx(float(panel["x"])) + 6 * antialias
        label_y = sy(float(panel["y"])) - 23 * antialias
        text_box = draw.textbbox((label_x, label_y), label, font=label_font)
        draw.rectangle(
            (
                label_x - 4 * antialias,
                label_y - 2 * antialias,
                text_box[2] + 5 * antialias,
                text_box[3] + 2 * antialias,
            ),
            fill=(255, 255, 255),
        )
        draw.text((label_x, label_y), label, fill=(37, 48, 68), font=label_font)

    legend_x, legend_y = 18 * antialias, (height - 78) * antialias
    legend_w, legend_h = 220 * antialias, 58 * antialias
    draw.rounded_rectangle(
        (legend_x, legend_y, legend_x + legend_w, legend_y + legend_h),
        radius=8 * antialias,
        fill=(255, 255, 255),
        outline=(190, 195, 205),
        width=antialias,
    )
    legend_font = load_font(ImageFont, 11 * antialias, bold=True)
    minimum, maximum = format_number(value_range.get("vmin")), format_number(value_range.get("vmax"))
    draw.text(
        (legend_x + 10 * antialias, legend_y + 8 * antialias),
        minimum,
        fill=(102, 112, 133),
        font=legend_font,
    )
    max_box = draw.textbbox((0, 0), maximum, font=legend_font)
    draw.text(
        (
            legend_x + legend_w - 10 * antialias - (max_box[2] - max_box[0]),
            legend_y + 8 * antialias,
        ),
        maximum,
        fill=(102, 112, 133),
        font=legend_font,
    )
    draw_horizontal_gradient(
        draw,
        (
            legend_x + 10 * antialias,
            legend_y + 34 * antialias,
            legend_x + legend_w - 10 * antialias,
            legend_y + 46 * antialias,
        ),
        antialias,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    final = work.resize((width, height), Image.Resampling.LANCZOS)
    save_png_image(final, output_path)
    return {
        "path": str(output_path.resolve()),
        "format": output_path.suffix.lower().lstrip("."),
        "sample": sample_id,
        "drawn_cells": len(drawn),
        "expressing_cells": len(expression_values),
        "width": width,
        "height": height,
    }


def render_spatial_pdf(
    output_path: Path,
    dataset: dict[str, Any],
    sample: dict[str, Any],
    gene_payload: dict[str, Any],
    layout: dict[str, Any],
    cells: Iterable[dict[str, Any]],
    width: int,
) -> dict[str, Any]:
    if width < 800:
        raise RuntimeError("--width must be at least 800 pixels")
    header_height, outer_margin = 78, 36
    scale = (width - outer_margin * 2) / max(1.0, float(layout["layoutWidth"]))
    stage_height = max(
        520, math.ceil(float(layout["layoutHeight"]) * scale + outer_margin * 2)
    )
    height = header_height + stage_height
    canvas = VectorPdfCanvas(width, height)

    def sx(value: float) -> float:
        return outer_margin + value * scale

    def sy(value: float) -> float:
        return header_height + outer_margin + value * scale

    canvas.rect(0, 0, width, height, fill=(238, 241, 244))
    canvas.rect(0, 0, width, header_height, fill=(255, 255, 255))
    sample_id = str(sample.get("id") or "")
    canvas.text(
        f"{gene_payload.get('gene', '')} spatial expression",
        28,
        36,
        size=24,
        color=(37, 48, 68),
        bold=True,
    )
    canvas.text(
        f"{dataset_display_label(dataset)} | {sample_display_label(sample)}",
        30,
        63,
        size=14,
        color=(102, 112, 133),
    )

    for panel in layout["panels"]:
        canvas.rect(
            sx(float(panel["x"])),
            sy(float(panel["y"])),
            float(panel["width"]) * scale,
            float(panel["height"]) * scale,
            fill=(255, 255, 255),
            stroke=(184, 193, 204),
            line_width=0.7,
        )

    samples_payload = require_object(gene_payload.get("samples"), "gene response samples")
    sample_expression = require_object(
        samples_payload.get(sample_id), f"gene response sample {sample_id}"
    )
    expression_values: dict[int, float] = {}
    for item in require_list(sample_expression.get("values") or [], "sample expression values"):
        if not isinstance(item, list) or len(item) < 2:
            continue
        try:
            cell_id = int(item[0])
        except (TypeError, ValueError):
            continue
        value = number(item[1])
        expression_values[cell_id] = max(value, expression_values.get(cell_id, value))

    value_range = require_object(gene_payload.get("range"), "gene response range")
    drawn: set[int] = set()
    for cell in cells:
        try:
            cell_id = int(cell.get("id"))
        except (TypeError, ValueError):
            continue
        if cell_id in drawn:
            continue
        panel = layout["cellToPanel"].get(cell_id)
        bbox = cell.get("bbox") or []
        if panel is None or not isinstance(bbox, list) or len(bbox) < 2:
            continue
        fill = color_for_value(expression_values.get(cell_id, 0.0), value_range)
        for contour in cell.get("contours") or []:
            if not isinstance(contour, list) or len(contour) < 3:
                continue
            points = []
            for point in contour:
                if not isinstance(point, list) or len(point) < 2:
                    continue
                source_x = number(bbox[0]) + number(point[0])
                source_y = number(bbox[1]) + number(point[1])
                layout_x = float(panel["x"]) + (
                    source_x - float(panel["bbox"][0])
                ) * float(panel["scaleX"])
                layout_y = float(panel["y"]) + (
                    source_y - float(panel["bbox"][1])
                ) * float(panel["scaleY"])
                points.append((sx(layout_x), sy(layout_y)))
            if len(points) >= 3:
                canvas.polygon(points, fill=fill, stroke=invert_rgb(fill), line_width=0.5)
        drawn.add(cell_id)

    for panel in layout["panels"]:
        label = (
            f"{panel.get('label') or panel.get('id') or ''} - "
            f"{int(number(panel.get('assignedCellCount'), len(panel.get('cellIds') or []))):,} cells"
        )
        baseline_y = sy(float(panel["y"])) - 10
        label_x = sx(float(panel["x"])) + 6
        label_width = estimated_text_width(label, 13)
        canvas.rect(label_x - 4, baseline_y - 13, label_width + 9, 18, fill=(255, 255, 255))
        canvas.text(label, label_x, baseline_y, size=13, color=(37, 48, 68))

    legend_x, legend_y, legend_width, legend_height = 18.0, height - 78.0, 220.0, 58.0
    canvas.rect(
        legend_x,
        legend_y,
        legend_width,
        legend_height,
        fill=(255, 255, 255),
        stroke=(190, 195, 205),
        line_width=0.8,
    )
    minimum = format_number(value_range.get("vmin"))
    maximum = format_number(value_range.get("vmax"))
    canvas.text(
        minimum, legend_x + 10, legend_y + 20, size=11, color=(102, 112, 133), bold=True
    )
    canvas.text(
        maximum,
        legend_x + legend_width - 10,
        legend_y + 20,
        size=11,
        color=(102, 112, 133),
        align="right",
        bold=True,
    )
    draw_vector_gradient(canvas, legend_x + 10, legend_y + 34, legend_width - 20, 12)

    write_vector_pdf(
        output_path,
        canvas,
        title=f"{gene_payload.get('gene', '')} spatial expression - {sample_id}",
    )
    return {
        "path": str(output_path.resolve()),
        "format": "pdf",
        "vector": True,
        "sample": sample_id,
        "drawn_cells": len(drawn),
        "expressing_cells": len(expression_values),
        "width": width,
        "height": height,
    }


def dot_radius(pct_expr: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum:
        return 9.0
    fraction = max(0.0, min(1.0, (pct_expr - minimum) / (maximum - minimum)))
    return 3.0 + fraction * 12.0


def render_dotplot_png(
    output_path: Path, payload: dict[str, Any], width: int
) -> dict[str, Any]:
    Image, ImageDraw, ImageFont = import_pillow()
    if width < 800:
        raise RuntimeError("--width must be at least 800 pixels")
    clusters = [
        item
        for item in require_list(payload.get("clusters"), "dotplot clusters")
        if isinstance(item, dict)
    ]
    if not clusters:
        raise RuntimeError("dotplot API returned no clusters")

    antialias, height = 2, 340
    work = Image.new("RGB", (width * antialias, height * antialias), (255, 255, 255))
    draw = ImageDraw.Draw(work)

    def px(value: float) -> int:
        return round(value * antialias)

    left, right, title_y, center_y = 190.0, width - 210.0, 30.0, 108.0
    band = max(1.0, right - left) / len(clusters)
    scaled_values = [number(item.get("avgExprScaled"), float("nan")) for item in clusters]
    scaled_values = [value for value in scaled_values if math.isfinite(value)]
    pct_values = [number(item.get("pctExpr")) for item in clusters]
    use_scaled = bool(scaled_values)
    color_range = (
        {"vmin": min(scaled_values), "vmax": max(scaled_values)}
        if use_scaled
        else {"vmin": 0.0, "vmax": max(number(item.get("avgExpr")) for item in clusters)}
    )
    pct_min = min(pct_values) if pct_values else 0.0
    pct_max = max(pct_values) if pct_values else 100.0

    title_font = load_font(ImageFont, px(18), bold=True)
    gene_font = load_font(ImageFont, px(14), bold=True)
    label_font = load_font(ImageFont, px(13))
    legend_font = load_font(ImageFont, px(11))
    draw.text(
        (px(left), px(title_y)),
        "Seurat Clusters",
        fill=(37, 48, 68),
        font=title_font,
        anchor="lm",
    )
    draw.line((px(left), px(center_y), px(right), px(center_y)), fill=(215, 221, 229), width=antialias)
    draw.text(
        (px(left - 14), px(center_y)),
        str(payload.get("gene") or "-"),
        fill=(37, 48, 68),
        font=gene_font,
        anchor="rm",
    )

    for index, cluster in enumerate(clusters):
        x = left + band * (index + 0.5)
        avg_expr = number(cluster.get("avgExpr"))
        scaled_value = number(cluster.get("avgExprScaled"), float("nan"))
        pct_expr = number(cluster.get("pctExpr"))
        radius = dot_radius(pct_expr, pct_min, pct_max)
        value = scaled_value if use_scaled and math.isfinite(scaled_value) else avg_expr
        rgb = color_for_value(value, color_range, scaled=use_scaled)
        draw.ellipse(
            (px(x - radius), px(center_y - radius), px(x + radius), px(center_y + radius)),
            fill=rgb,
            outline=(122, 28, 22),
            width=antialias,
        )
        draw.text(
            (px(x), px(center_y + 28)),
            str(cluster.get("label") or cluster.get("id") or ""),
            fill=(65, 80, 100),
            font=label_font,
            anchor="ma",
        )

    legend_left, legend_top, legend_width = width - 162.0, center_y - 42.0, 116.0
    draw.text(
        (px(legend_left), px(legend_top - 5)),
        "Scaled avg expr" if use_scaled else "Avg expr",
        fill=(37, 48, 68),
        font=legend_font,
        anchor="ls",
    )
    draw_horizontal_gradient(
        draw,
        (px(legend_left), px(legend_top), px(legend_left + legend_width), px(legend_top + 10)),
        antialias,
    )
    draw.text(
        (px(legend_left), px(legend_top + 16)),
        format_number(color_range["vmin"], 1),
        fill=(102, 112, 133),
        font=legend_font,
        anchor="la",
    )
    draw.text(
        (px(legend_left + legend_width), px(legend_top + 16)),
        format_number(color_range["vmax"], 1),
        fill=(102, 112, 133),
        font=legend_font,
        anchor="ra",
    )
    size_y = legend_top + 72
    draw.text(
        (px(legend_left), px(size_y - 26)),
        "% cells",
        fill=(37, 48, 68),
        font=legend_font,
        anchor="ls",
    )
    sizes = (
        [pct_min, pct_min + (pct_max - pct_min) / 2.0, pct_max]
        if pct_max > pct_min
        else [pct_min]
    )
    for index, pct in enumerate(sizes):
        cx = legend_left + (56 if len(sizes) == 1 else 12 + index * 40)
        radius = dot_radius(pct, pct_min, pct_max)
        draw.ellipse(
            (px(cx - radius), px(size_y - radius), px(cx + radius), px(size_y + radius)),
            fill=(252, 187, 161),
            outline=(138, 29, 24),
            width=antialias,
        )
        draw.text(
            (px(cx), px(size_y + 21)),
            format_number(pct, 1),
            fill=(102, 112, 133),
            font=legend_font,
            anchor="ma",
        )

    note_font = load_font(ImageFont, px(12))
    draw.text(
        (px(28), px(height - 26)),
        "Dot size: pctExpr | Color: avgExprScaled | Source: Potato Agent Interface API",
        fill=(102, 112, 133),
        font=note_font,
        anchor="lm",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final = work.resize((width, height), Image.Resampling.LANCZOS)
    save_png_image(final, output_path)
    return {
        "path": str(output_path.resolve()),
        "format": output_path.suffix.lower().lstrip("."),
        "clusters": len(clusters),
        "width": width,
        "height": height,
    }


def render_dotplot_pdf(
    output_path: Path, payload: dict[str, Any], width: int
) -> dict[str, Any]:
    if width < 800:
        raise RuntimeError("--width must be at least 800 pixels")
    clusters = [
        item
        for item in require_list(payload.get("clusters"), "dotplot clusters")
        if isinstance(item, dict)
    ]
    if not clusters:
        raise RuntimeError("dotplot API returned no clusters")

    height = 340
    canvas = VectorPdfCanvas(width, height)
    canvas.rect(0, 0, width, height, fill=(255, 255, 255))
    left, right, title_y, center_y = 190.0, width - 210.0, 30.0, 108.0
    band = max(1.0, right - left) / len(clusters)
    scaled_values = [number(item.get("avgExprScaled"), float("nan")) for item in clusters]
    scaled_values = [value for value in scaled_values if math.isfinite(value)]
    pct_values = [number(item.get("pctExpr")) for item in clusters]
    use_scaled = bool(scaled_values)
    color_range = (
        {"vmin": min(scaled_values), "vmax": max(scaled_values)}
        if use_scaled
        else {"vmin": 0.0, "vmax": max(number(item.get("avgExpr")) for item in clusters)}
    )
    pct_min = min(pct_values) if pct_values else 0.0
    pct_max = max(pct_values) if pct_values else 100.0

    canvas.text(
        "Seurat Clusters", left, title_y + 6, size=18, color=(37, 48, 68), bold=True
    )
    canvas.line(left, center_y, right, center_y, color=(215, 221, 229), line_width=1.0)
    canvas.text(
        str(payload.get("gene") or "-"),
        left - 14,
        center_y + 5,
        size=14,
        color=(37, 48, 68),
        align="right",
        bold=True,
    )

    for index, cluster in enumerate(clusters):
        x = left + band * (index + 0.5)
        avg_expr = number(cluster.get("avgExpr"))
        scaled_value = number(cluster.get("avgExprScaled"), float("nan"))
        pct_expr = number(cluster.get("pctExpr"))
        radius = dot_radius(pct_expr, pct_min, pct_max)
        value = scaled_value if use_scaled and math.isfinite(scaled_value) else avg_expr
        fill = color_for_value(value, color_range, scaled=use_scaled)
        canvas.circle(
            x,
            center_y,
            radius,
            fill=fill,
            stroke=(122, 28, 22),
            line_width=1.0,
        )
        canvas.text(
            str(cluster.get("label") or cluster.get("id") or ""),
            x,
            center_y + 41,
            size=13,
            color=(65, 80, 100),
            align="center",
        )

    legend_left, legend_top, legend_width = width - 162.0, center_y - 42.0, 116.0
    canvas.text(
        "Scaled avg expr" if use_scaled else "Avg expr",
        legend_left,
        legend_top - 5,
        size=11,
        color=(37, 48, 68),
    )
    draw_vector_gradient(canvas, legend_left, legend_top, legend_width, 10)
    canvas.text(
        format_number(color_range["vmin"], 1),
        legend_left,
        legend_top + 26,
        size=11,
        color=(102, 112, 133),
    )
    canvas.text(
        format_number(color_range["vmax"], 1),
        legend_left + legend_width,
        legend_top + 26,
        size=11,
        color=(102, 112, 133),
        align="right",
    )
    size_y = legend_top + 72
    canvas.text("% cells", legend_left, size_y - 26, size=11, color=(37, 48, 68))
    sizes = (
        [pct_min, pct_min + (pct_max - pct_min) / 2.0, pct_max]
        if pct_max > pct_min
        else [pct_min]
    )
    for index, pct in enumerate(sizes):
        cx = legend_left + (56 if len(sizes) == 1 else 12 + index * 40)
        radius = dot_radius(pct, pct_min, pct_max)
        canvas.circle(
            cx,
            size_y,
            radius,
            fill=(252, 187, 161),
            stroke=(138, 29, 24),
            line_width=1.0,
        )
        canvas.text(
            format_number(pct, 1),
            cx,
            size_y + 32,
            size=11,
            color=(102, 112, 133),
            align="center",
        )

    canvas.text(
        "Dot size: pctExpr | Color: avgExprScaled | Source: Potato Agent Interface API",
        28,
        height - 21,
        size=12,
        color=(102, 112, 133),
    )
    write_vector_pdf(
        output_path, canvas, title=f"{payload.get('gene', '')} Seurat cluster dotplot"
    )
    return {
        "path": str(output_path.resolve()),
        "format": "pdf",
        "vector": True,
        "clusters": len(clusters),
        "width": width,
        "height": height,
    }


def render_spatial_image(
    output_path: Path,
    dataset: dict[str, Any],
    sample: dict[str, Any],
    gene_payload: dict[str, Any],
    layout: dict[str, Any],
    cells: Iterable[dict[str, Any]],
    width: int,
) -> dict[str, Any]:
    output_path = Path(output_path)
    if output_path.suffix.lower() == ".pdf":
        return render_spatial_pdf(
            output_path, dataset, sample, gene_payload, layout, cells, width
        )
    if output_path.suffix.lower() == ".png":
        return render_spatial_png(
            output_path, dataset, sample, gene_payload, layout, cells, width
        )
    raise RuntimeError("spatial plot output must end in .pdf or .png")


def render_dotplot_image(
    output_path: Path, payload: dict[str, Any], width: int
) -> dict[str, Any]:
    output_path = Path(output_path)
    if output_path.suffix.lower() == ".pdf":
        return render_dotplot_pdf(output_path, payload, width)
    if output_path.suffix.lower() == ".png":
        return render_dotplot_png(output_path, payload, width)
    raise RuntimeError("dotplot output must end in .pdf or .png")


def dotplot_rows(dataset_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for cluster in require_list(payload.get("clusters"), "dotplot clusters"):
        if not isinstance(cluster, dict):
            continue
        rows.append(
            {
                "dataset": dataset_id,
                "gene": payload.get("gene", ""),
                "cluster_column": payload.get("clusterColumn", ""),
                "cluster_id": cluster.get("id", ""),
                "cluster_label": cluster.get("label", ""),
                "cluster_name": cluster.get("name", ""),
                "order": cluster.get("order", ""),
                "cell_count": cluster.get("cellCount", ""),
                "expressing_count": cluster.get("expressingCount", ""),
                "pct_expr": cluster.get("pctExpr", ""),
                "avg_expr": cluster.get("avgExpr", ""),
                "avg_expr_scaled": cluster.get("avgExprScaled", ""),
            }
        )
    return rows


def write_dotplot_tsv(path: Path, dataset_id: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=DOTPLOT_TSV_FIELDS, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(dotplot_rows(dataset_id, payload))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_interface_payloads(
    base_url: str, dataset_id: str, gene: str, timeout: int
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    paths = [
        ("catalog", "/api/spatial/datasets", None),
        ("gene", "/api/spatial/gene", {"dataset": dataset_id, "gene": gene}),
        ("dotplot", "/api/spatial/dotplot", {"dataset": dataset_id, "gene": gene}),
        ("replicates", "/api/spatial/replicates", {"dataset": dataset_id}),
    ]

    def fetch(item: tuple[str, str, dict[str, str] | None]) -> tuple[str, dict[str, Any]]:
        name, path, params = item
        return name, require_object(
            request_api(base_url, path, timeout, params), f"{name} response"
        )

    with ThreadPoolExecutor(max_workers=len(paths)) as executor:
        responses = dict(executor.map(fetch, paths))
    return responses["catalog"], responses["gene"], responses["dotplot"], responses["replicates"]


def output_prefix(gene: str, dataset_id: str) -> str:
    return f"{safe_filename_component(gene)}_{safe_filename_component(dataset_id)}"


def write_dotplot_outputs(
    outdir: Path,
    dataset_id: str,
    gene: str,
    payload: dict[str, Any],
    width: int,
    output_format: str,
) -> dict[str, Any]:
    prefix = output_prefix(gene, dataset_id)
    plot_path = outdir / f"{prefix}_cluster_dotplot.{output_format}"
    tsv_path = outdir / f"{prefix}_cluster_dotplot.tsv"
    json_path = outdir / f"{prefix}_cluster_dotplot.json"
    plot_info = render_dotplot_image(plot_path, payload, width)
    write_dotplot_tsv(tsv_path, dataset_id, payload)
    write_json(json_path, payload)
    return {**plot_info, "tsv": str(tsv_path.resolve()), "json": str(json_path.resolve())}


def run_datasets(args: argparse.Namespace) -> int:
    payload = request_api(args.base_url, "/api/spatial/datasets", args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_expression(args: argparse.Namespace) -> int:
    payload = request_expression(args.base_url, args.dataset, args.gene, args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_dotplot(args: argparse.Namespace) -> int:
    payload = require_object(
        request_api(
            args.base_url,
            "/api/spatial/dotplot",
            args.timeout,
            {"dataset": args.dataset, "gene": args.gene},
        ),
        "dotplot response",
    )
    outdir = Path(args.outdir).expanduser().resolve()
    result = write_dotplot_outputs(
        outdir,
        args.dataset,
        args.gene,
        payload,
        args.width,
        args.output_format,
    )
    print(
        json.dumps(
            {
                "source": normalize_base_url(args.base_url),
                "dataset": args.dataset,
                "gene": args.gene,
                "dotplot": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_plot(args: argparse.Namespace) -> int:
    if not 1 <= args.workers <= 16:
        raise RuntimeError("--workers must be between 1 and 16")
    base_url = normalize_base_url(args.base_url)
    catalog, gene_payload, dotplot_payload, replicates_payload = fetch_interface_payloads(
        base_url, args.dataset, args.gene, args.timeout
    )
    dataset = find_dataset(catalog, args.dataset)
    samples = select_samples(dataset, args.sample)
    replicate_samples = require_object(
        replicates_payload.get("samples"), "replicates field 'samples'"
    )
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    spatial_outputs = []
    for sample in samples:
        sample_id = str(sample.get("id") or "")
        manifest_url = sample_manifest_url(base_url, dataset, sample)
        manifest = require_object(
            request_json_url(manifest_url, args.timeout), f"manifest for {sample_id}"
        )
        replicate_payload = replicate_samples.get(sample_id)
        if replicate_payload is None:
            raise RuntimeError(f"replicate metadata missing sample {sample_id}")
        columns = int(number(sample.get("columns"), 0)) or None
        layout = prepare_spatial_layout(manifest, replicate_payload, columns)
        cells = load_contour_cells(
            base_url,
            args.dataset,
            manifest,
            layout["neededTileKeys"],
            args.timeout,
            args.workers,
        )
        output_path = outdir / (
            f"{output_prefix(args.gene, args.dataset)}_"
            f"{safe_filename_component(sample_id)}_spatial_expression.{args.output_format}"
        )
        spatial_outputs.append(
            render_spatial_image(
                output_path,
                dataset,
                sample,
                gene_payload,
                layout,
                cells,
                args.width,
            )
        )

    dotplot_output = write_dotplot_outputs(
        outdir,
        args.dataset,
        args.gene,
        dotplot_payload,
        args.width,
        args.output_format,
    )
    provenance_path = outdir / (
        f"{output_prefix(args.gene, args.dataset)}_interface_data.json"
    )
    write_json(
        provenance_path,
        {
            "schemaVersion": 1,
            "source": base_url,
            "retrievedAt": datetime.now(timezone.utc).isoformat(),
            "dataset": dataset,
            "geneExpression": gene_payload,
            "dotplot": dotplot_payload,
        },
    )
    print(
        json.dumps(
            {
                "source": base_url,
                "dataset": args.dataset,
                "gene": args.gene,
                "spatial": spatial_outputs,
                "dotplot": dotplot_output,
                "interface_data": str(provenance_path.resolve()),
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
        if args.command == "datasets":
            return run_datasets(args)
        if args.command == "expression":
            return run_expression(args)
        if args.command == "dotplot":
            return run_dotplot(args)
        if args.command == "plot":
            return run_plot(args)
        parser.error(f"unknown command: {args.command}")
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

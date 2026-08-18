#!/usr/bin/env python3
"""Create a publication-ready vector PDF heatmap from a numeric TSV matrix."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, Normalize, TwoSlopeNorm

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "pdf.fonttype": 42,
        "axes.linewidth": 0.8,
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot a numeric TSV matrix as a vector PDF heatmap."
    )
    parser.add_argument("input", type=Path, help="TSV input; first column contains row labels")
    parser.add_argument("output", type=Path, help="Output PDF path")
    parser.add_argument("--title", default="Heatmap")
    parser.add_argument("--xlabel", default="Columns")
    parser.add_argument("--ylabel", default="Rows")
    parser.add_argument("--colorbar-label", default="Value")
    parser.add_argument(
        "--cmap",
        default="blue_white_red",
        help=(
            "Color map name. The default blue_white_red uses exact endpoints "
            "#0000FF (minimum) and #FF0000 (maximum); other Matplotlib colormap "
            "names are also accepted"
        ),
    )
    parser.add_argument("--center", type=float, default=None, help="Optional diverging-scale midpoint")
    parser.add_argument("--expected-rows", type=int, default=None)
    parser.add_argument("--expected-cols", type=int, default=None)
    parser.add_argument("--precision", type=int, default=2)
    parser.add_argument("--no-annotate", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with args.input.open("r", encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle, delimiter="\t"), None)
    if header is None or len(header) < 2:
        raise ValueError("Input matrix must contain a row-label column and numeric columns")
    column_labels = header[1:]
    duplicate_columns = sorted(
        label for label, count in Counter(column_labels).items() if count > 1
    )
    if duplicate_columns:
        raise ValueError(f"Column labels must be unique; duplicates: {duplicate_columns}")

    table = pd.read_csv(args.input, sep="\t", index_col=0)
    if not table.index.is_unique:
        duplicate_rows = sorted(
            {str(label) for label in table.index[table.index.duplicated(keep=False)]}
        )
        raise ValueError(f"Row labels must be unique; duplicates: {duplicate_rows}")
    numeric = table.apply(pd.to_numeric, errors="raise")

    if args.expected_rows is not None and numeric.shape[0] != args.expected_rows:
        raise ValueError(f"Expected {args.expected_rows} rows, found {numeric.shape[0]}")
    if args.expected_cols is not None and numeric.shape[1] != args.expected_cols:
        raise ValueError(f"Expected {args.expected_cols} columns, found {numeric.shape[1]}")
    if numeric.empty:
        raise ValueError("Input matrix is empty")

    data = numeric.to_numpy(dtype=float)
    if not np.isfinite(data).all():
        raise ValueError("Input matrix contains missing or non-finite values")

    vmin, vmax = float(data.min()), float(data.max())
    if vmin == vmax:
        raise ValueError("All matrix values are identical; no color range can be defined")

    if args.center is None:
        norm = Normalize(vmin=vmin, vmax=vmax)
    else:
        if not vmin < args.center < vmax:
            raise ValueError("--center must lie strictly between the matrix minimum and maximum")
        norm = TwoSlopeNorm(vmin=vmin, vcenter=args.center, vmax=vmax)

    if args.cmap == "blue_white_red":
        cmap = LinearSegmentedColormap.from_list(
            "blue_white_red",
            ["#0000FF", "#FFFFFF", "#FF0000"],
            N=256,
        )
    else:
        cmap = plt.get_cmap(args.cmap)
    nrows, ncols = data.shape
    fig_width = max(5.5, 1.05 * ncols + 2.6)
    fig_height = max(4.2, 0.62 * nrows + 2.2)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height), constrained_layout=True)

    mesh = ax.pcolormesh(
        np.arange(ncols + 1),
        np.arange(nrows + 1),
        data,
        cmap=cmap,
        norm=norm,
        shading="flat",
        edgecolors="white",
        linewidth=1.0,
        rasterized=False,
    )
    ax.set_xlim(0, ncols)
    ax.set_ylim(nrows, 0)
    ax.set_aspect("equal")
    ax.set_xticks(np.arange(ncols) + 0.5, labels=numeric.columns)
    ax.set_yticks(np.arange(nrows) + 0.5, labels=numeric.index)
    ax.tick_params(axis="x", rotation=25, length=0, pad=7)
    ax.tick_params(axis="y", length=0, pad=7)
    ax.set_title(args.title, fontsize=14, fontweight="bold", pad=14)
    ax.set_xlabel(args.xlabel, fontweight="bold", labelpad=10)
    ax.set_ylabel(args.ylabel, fontweight="bold", labelpad=10)

    if not args.no_annotate:
        for row in range(nrows):
            for col in range(ncols):
                rgba = cmap(norm(data[row, col]))
                luminance = 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]
                text_color = "black" if luminance > 0.58 else "white"
                ax.text(
                    col + 0.5,
                    row + 0.5,
                    f"{data[row, col]:.{args.precision}f}",
                    ha="center",
                    va="center",
                    color=text_color,
                    fontsize=9,
                    fontweight="medium",
                )

    colorbar = fig.colorbar(mesh, ax=ax, fraction=0.055, pad=0.05, shrink=0.88)
    colorbar.set_label(args.colorbar_label, fontweight="bold", labelpad=8)
    colorbar.outline.set_linewidth(0.7)
    if colorbar.solids is not None:
        # Matplotlib otherwise rasterizes long color gradients in vector output.
        colorbar.solids.set_rasterized(False)
        # Matching edges prevent hairline seams in some PDF renderers.
        colorbar.solids.set_edgecolor("face")
        colorbar.solids.set_linewidth(0)

    for spine in ax.spines.values():
        spine.set_visible(False)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        args.output,
        format="pdf",
        bbox_inches="tight",
        metadata={"Title": args.title, "Creator": "Potato Agent / Matplotlib"},
    )
    plt.close(fig)
    print(f"Saved vector PDF: {args.output.resolve()}")
    print(f"Matrix shape: {nrows} rows x {ncols} columns")
    print(f"Color scale: min={vmin:g}, max={vmax:g}, cmap={args.cmap}")


if __name__ == "__main__":
    main()

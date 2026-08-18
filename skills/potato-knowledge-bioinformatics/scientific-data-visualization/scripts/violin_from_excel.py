#!/usr/bin/env python3
"""Create publication-ready violin-plot panels from an Excel workbook.

This companion to ``boxplot_from_excel.py`` reuses the same Excel input,
JSON configuration, group colors, Student's t-test, output statistics, and
fixed-seed jitter. Each selected worksheet becomes one panel and each column
becomes one violin. A narrow boxplot is drawn inside every violin to show the
median and interquartile range.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages

from boxplot_from_excel import (
    DEFAULT_COLORS,
    STUDENT_TEST_LABEL,
    add_pvalue_bracket,
    collect_stats,
    load_config,
    read_panel,
    run_test,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path, help="Input Excel workbook")
    parser.add_argument("output_pdf", type=Path, help="Output PDF figure")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="JSON configuration used by boxplot_from_excel.py",
    )
    parser.add_argument(
        "--stats-file",
        type=Path,
        default=None,
        help="Output TSV; default: OUTPUT_PDF with .stats.tsv suffix",
    )
    parser.add_argument(
        "--preview",
        type=Path,
        default=None,
        help="Optional PNG preview path",
    )
    return parser.parse_args()


def validate_violin_groups(sheet: str, labels: list[str], groups: list[np.ndarray]) -> None:
    """Reject samples for which a kernel-density violin is undefined."""
    for label, values in zip(labels, groups):
        if values.size < 2:
            raise ValueError(
                f"Column {label!r} in sheet {sheet!r} needs at least two numeric "
                "observations for a violin plot."
            )
        if np.ptp(values) == 0:
            raise ValueError(
                f"Column {label!r} in sheet {sheet!r} is constant; kernel-density "
                "estimation for a violin plot is undefined."
            )


def violin_pdf_title(config: dict[str, Any]) -> str:
    configured = str(config.get("pdf_title", "Scientific violin plots"))
    return configured.replace("boxplots", "violin plots").replace("boxplot", "violin plot")


def create_figure(
    input_file: Path,
    output_pdf: Path,
    stats_file: Path,
    preview_file: Path | None,
    config: dict[str, Any],
) -> None:
    panels = config["panels"]
    ncols = int(config.get("ncols", min(2, len(panels))))
    if ncols < 1:
        raise ValueError("ncols must be at least 1.")
    nrows = math.ceil(len(panels) / ncols)
    colors = list(config.get("colors", DEFAULT_COLORS))
    if not colors:
        raise ValueError("At least one color is required.")

    rng = np.random.default_rng(int(config.get("jitter_seed", 20260807)))
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Liberation Sans"],
            "font.size": 10,
            "axes.linewidth": 0.9,
            "axes.labelsize": 11,
            "axes.titlesize": 12,
            "xtick.labelsize": 10,
            "ytick.labelsize": 9.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    figure_width = float(config.get("figure_width", 4.0 * ncols))
    figure_height = float(config.get("figure_height", 4.1 * nrows + 0.15))
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(figure_width, figure_height),
        squeeze=False,
    )
    axes_flat = axes.ravel()
    stats_rows: list[dict[str, Any]] = []

    for panel_index, panel in enumerate(panels):
        ax = axes_flat[panel_index]
        labels, groups = read_panel(input_file, panel)
        validate_violin_groups(str(panel["sheet"]), labels, groups)
        test_result = run_test(groups)
        stats_rows.extend(collect_stats(panel["sheet"], labels, groups, test_result))

        positions = np.arange(1, len(groups) + 1, dtype=float)
        panel_colors = [colors[i % len(colors)] for i in range(len(groups))]

        violins = ax.violinplot(
            groups,
            positions=positions,
            widths=0.78,
            showmeans=False,
            showmedians=False,
            showextrema=False,
            points=200,
            bw_method="scott",
        )
        for body, color in zip(violins["bodies"], panel_colors):
            body.set_facecolor(color)
            body.set_edgecolor("#4A4A4A")
            body.set_linewidth(1.0)
            body.set_alpha(0.72)

        inner_boxes = ax.boxplot(
            groups,
            positions=positions,
            widths=0.16,
            patch_artist=True,
            showfliers=False,
            whis=1.5,
            manage_ticks=False,
            medianprops={"color": "#171717", "linewidth": 1.6},
            whiskerprops={"color": "#3F3F3F", "linewidth": 1.0},
            capprops={"color": "#3F3F3F", "linewidth": 1.0},
            boxprops={"edgecolor": "#3F3F3F", "linewidth": 1.0},
        )
        for box in inner_boxes["boxes"]:
            box.set_facecolor("white")
            box.set_alpha(0.82)

        if bool(config.get("show_points", True)):
            for x_value, values, color in zip(positions, groups, panel_colors):
                jitter = rng.uniform(-0.13, 0.13, size=values.size)
                ax.scatter(
                    np.full(values.size, x_value) + jitter,
                    values,
                    s=15,
                    facecolors=color,
                    edgecolors="white",
                    linewidths=0.35,
                    alpha=0.72,
                    zorder=3,
                )

        all_values = np.concatenate(groups)
        data_min = float(np.min(all_values))
        data_max = float(np.max(all_values))
        data_span = data_max - data_min if data_max > data_min else 1.0
        auto_lower = (
            max(0.0, data_min - 0.08 * data_span)
            if data_min >= 0
            else data_min - 0.12 * data_span
        )
        auto_upper = data_max + 0.30 * data_span
        lower = float(panel.get("y_min", auto_lower))
        upper = float(panel.get("y_max", auto_upper))
        if upper <= lower:
            raise ValueError(f"Invalid Y-axis limits for sheet {panel['sheet']!r}.")
        ax.set_ylim(lower, upper)

        add_pvalue_bracket(
            ax,
            positions[0],
            positions[1],
            data_max,
            float(test_result["p_value"]),
        )

        ax.set_title(
            str(panel.get("title", panel["sheet"])),
            pad=10,
            fontweight="semibold",
        )
        ax.set_ylabel(str(panel.get("y_label", panel["sheet"])))
        ax.set_xlim(0.45, len(groups) + 0.55)
        ax.set_xticks(positions, labels=labels)
        ax.yaxis.grid(True, color="#D9D9D9", linewidth=0.7, alpha=0.65)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(axis="x", length=0, pad=6)

        panel_label = panel.get("panel_label")
        if panel_label:
            ax.text(
                -0.16,
                1.04,
                str(panel_label),
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=13,
                fontweight="bold",
            )

    for unused_ax in axes_flat[len(panels) :]:
        unused_ax.set_visible(False)

    footer = config.get("violin_footer")
    if footer is None:
        density_note = "Violin: kernel density; inner box: median and IQR"
        footer = (
            f"{STUDENT_TEST_LABEL}; {density_note.lower()}; "
            "each point represents one observation"
        )

    fig.subplots_adjust(
        left=float(config.get("left_margin", 0.105)),
        right=float(config.get("right_margin", 0.985)),
        bottom=float(config.get("bottom_margin", 0.16 if nrows == 1 else 0.10)),
        top=float(config.get("top_margin", 0.86 if nrows == 1 else 0.93)),
        wspace=float(config.get("wspace", 0.34)),
        hspace=float(config.get("hspace", 0.38)),
    )
    fig.text(
        0.5,
        float(config.get("footer_y", 0.045 if nrows == 1 else 0.025)),
        str(footer),
        ha="center",
        va="center",
        fontsize=8.2,
        color="#4A4A4A",
    )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(output_pdf, metadata={"Title": violin_pdf_title(config)}) as pdf:
        pdf.savefig(fig, bbox_inches="tight", pad_inches=0.08)

    if preview_file is not None:
        preview_file.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(preview_file, dpi=180, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)

    pd.DataFrame(stats_rows).to_csv(
        stats_file,
        sep="\t",
        index=False,
        float_format="%.10g",
    )


if __name__ == "__main__":
    arguments = parse_args()
    statistics_path = arguments.stats_file or arguments.output_pdf.with_suffix(".stats.tsv")
    configuration = load_config(arguments.config, arguments.input_file)
    create_figure(
        arguments.input_file,
        arguments.output_pdf,
        statistics_path,
        arguments.preview,
        configuration,
    )
    print(f"Created PDF: {arguments.output_pdf}")
    print(f"Created statistics: {statistics_path}")
    if arguments.preview is not None:
        print(f"Created preview: {arguments.preview}")

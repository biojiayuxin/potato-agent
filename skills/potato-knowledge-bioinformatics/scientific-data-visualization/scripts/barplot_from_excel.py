#!/usr/bin/env python3
"""Create publication-ready mean +/- SD barplot panels from an Excel workbook.

Each selected worksheet becomes one panel and each column becomes one group.
This companion to boxplot_from_excel.py shares its input, JSON configuration,
two-sided Student's t-test, statistics output, and fixed-seed jitter.
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
        help="JSON configuration used by boxplot_from_excel.py; default: all sheets",
    )
    parser.add_argument(
        "--stats-file",
        type=Path,
        default=None,
        help="Output TSV; default: OUTPUT_PDF with .stats.tsv suffix",
    )
    parser.add_argument("--preview", type=Path, default=None, help="Optional PNG preview path")
    return parser.parse_args()


def barplot_pdf_title(config: dict[str, Any]) -> str:
    configured = str(config.get("pdf_title", "Scientific barplots"))
    return configured.replace("boxplots", "barplots").replace("boxplot", "barplot")


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
    fig, axes = plt.subplots(nrows, ncols, figsize=(figure_width, figure_height), squeeze=False)
    axes_flat = axes.ravel()
    stats_rows: list[dict[str, Any]] = []

    try:
        for panel_index, panel in enumerate(panels):
            ax = axes_flat[panel_index]
            labels, groups = read_panel(input_file, panel)
            test_result = run_test(groups)
            panel_stats = collect_stats(panel["sheet"], labels, groups, test_result)
            stats_rows.extend(panel_stats)

            positions = np.arange(1, len(groups) + 1, dtype=float)
            panel_colors = [colors[i % len(colors)] for i in range(len(groups))]
            means = np.array([row["mean"] for row in panel_stats], dtype=float)
            standard_deviations = np.array([row["sd"] for row in panel_stats], dtype=float)
            ax.bar(
                positions,
                means,
                width=0.55,
                yerr=standard_deviations,
                color=panel_colors,
                edgecolor="#4A4A4A",
                linewidth=1.0,
                alpha=0.65,
                capsize=5,
                error_kw={"ecolor": "#222222", "elinewidth": 1.2, "capthick": 1.2, "zorder": 4},
            )

            if bool(config.get("show_points", True)):
                for x_value, values, color in zip(positions, groups, panel_colors):
                    jitter = rng.uniform(-0.16, 0.16, size=values.size)
                    ax.scatter(
                        np.full(values.size, x_value) + jitter,
                        values,
                        s=17,
                        facecolors=color,
                        edgecolors="white",
                        linewidths=0.35,
                        alpha=0.78,
                        zorder=3,
                    )

            # Include the zero baseline, raw observations, and full SD intervals.
            all_values = np.concatenate(groups)
            data_min = min(0.0, float(np.min(all_values)), float(np.min(means - standard_deviations)))
            data_max = max(0.0, float(np.max(all_values)), float(np.max(means + standard_deviations)))
            data_span = data_max - data_min if data_max > data_min else 1.0
            auto_lower = data_min - 0.08 * data_span if data_min < 0 else 0.0
            auto_upper = data_max + 0.30 * data_span
            lower = float(panel.get("y_min", auto_lower))
            upper = float(panel.get("y_max", auto_upper))
            if not np.isfinite([lower, upper]).all() or upper <= lower:
                raise ValueError(f"Invalid Y-axis limits for sheet {panel['sheet']!r}.")
            if lower > 0 or upper < 0:
                raise ValueError(f"Barplot Y-axis limits must include zero for sheet {panel['sheet']!r}.")
            ax.set_ylim(lower, upper)
            ax.axhline(0, color="#4A4A4A", linewidth=0.9)

            add_pvalue_bracket(
                ax,
                positions[0],
                positions[1],
                data_max,
                float(test_result["p_value"]),
            )

            ax.set_title(str(panel.get("title", panel["sheet"])), pad=10, fontweight="semibold")
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

        footer = config.get("barplot_footer")
        if footer is None:
            footer = f"{STUDENT_TEST_LABEL}; bars: mean ± SD"
            if bool(config.get("show_points", True)):
                footer += "; each point represents one observation"

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
        with PdfPages(output_pdf, metadata={"Title": barplot_pdf_title(config)}) as pdf:
            pdf.savefig(fig, bbox_inches="tight", pad_inches=0.08)

        if preview_file is not None:
            preview_file.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(preview_file, dpi=180, bbox_inches="tight", pad_inches=0.08)
        pd.DataFrame(stats_rows).to_csv(stats_file, sep="\t", index=False, float_format="%.10g")
    finally:
        plt.close(fig)


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

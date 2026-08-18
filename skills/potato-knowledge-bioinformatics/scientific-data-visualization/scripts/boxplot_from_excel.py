#!/usr/bin/env python3
"""Create publication-ready boxplot panels from an Excel workbook.

Each selected worksheet becomes one panel and each column becomes one group.
Use a JSON configuration to set panel titles, Y-axis labels, colors, and layout.
Each panel is compared with a two-sided Student's t-test.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from scipy import stats


STUDENT_TEST_LABEL = "Two-sided Student's t-test"
DEFAULT_COLORS = ["#9AA0A6", "#3B82C4", "#D97706", "#6A5ACD"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path, help="Input Excel workbook")
    parser.add_argument("output_pdf", type=Path, help="Output PDF figure")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="JSON configuration; without it, all sheets are plotted with defaults",
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


def load_config(config_file: Path | None, input_file: Path) -> dict[str, Any]:
    if config_file is None:
        sheets = pd.ExcelFile(input_file).sheet_names
        return {
            "panels": [
                {"sheet": sheet, "title": sheet, "y_label": sheet}
                for sheet in sheets
            ],
            "ncols": min(2, max(1, len(sheets))),
            "colors": DEFAULT_COLORS,
            "jitter_seed": 20260807,
        }

    with config_file.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    if not isinstance(config.get("panels"), list) or not config["panels"]:
        raise ValueError("Config must contain a non-empty 'panels' list.")
    return config


def read_panel(input_file: Path, panel: dict[str, Any]) -> tuple[list[str], list[np.ndarray]]:
    sheet = panel["sheet"]
    frame = pd.read_excel(input_file, sheet_name=sheet)
    if frame.shape[1] < 1:
        raise ValueError(f"Sheet {sheet!r} contains no columns.")

    labels: list[str] = []
    groups: list[np.ndarray] = []
    for column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce").dropna().to_numpy(dtype=float)
        if values.size == 0:
            raise ValueError(f"Column {column!r} in sheet {sheet!r} has no numeric observations.")
        if not np.isfinite(values).all():
            raise ValueError(
                f"Column {column!r} in sheet {sheet!r} contains non-finite values."
            )
        labels.append(str(column))
        groups.append(values)
    return labels, groups


def run_test(groups: list[np.ndarray]) -> dict[str, float | str]:
    if len(groups) != 2:
        raise ValueError(
            "Student's t-test requires exactly two groups per sheet; "
            f"found {len(groups)}."
        )
    if any(values.size < 2 for values in groups):
        raise ValueError("Student's t-test requires at least two observations per group.")

    result = stats.ttest_ind(groups[0], groups[1], equal_var=True, alternative="two-sided")
    statistic = float(result.statistic)
    p_value = float(result.pvalue)
    if not np.isfinite(statistic) or not np.isfinite(p_value):
        raise ValueError(
            "Student's t-test is undefined for these groups; check sample size and variance."
        )
    return {
        "test": STUDENT_TEST_LABEL,
        "statistic": statistic,
        "df": float(result.df),
        "p_value": p_value,
    }


def format_pvalue(p_value: float) -> str:
    if not np.isfinite(p_value) or not 0 <= p_value <= 1:
        raise ValueError(f"Invalid P value: {p_value!r}")
    if p_value == 0:
        return r"$P < 10^{-300}$"
    if p_value < 0.001:
        exponent = int(np.floor(np.log10(p_value)))
        coefficient = p_value / (10**exponent)
        return rf"$P = {coefficient:.2f} \times 10^{{{exponent}}}$"
    return rf"$P = {p_value:.3f}$"


def add_pvalue_bracket(
    ax: plt.Axes,
    x1: float,
    x2: float,
    data_max: float,
    p_value: float,
) -> None:
    ymin, ymax = ax.get_ylim()
    plot_range = ymax - ymin
    bracket_y = data_max + 0.075 * plot_range
    bracket_h = 0.035 * plot_range
    ax.plot(
        [x1, x1, x2, x2],
        [bracket_y, bracket_y + bracket_h, bracket_y + bracket_h, bracket_y],
        color="#222222",
        linewidth=1.15,
        clip_on=False,
    )
    ax.text(
        (x1 + x2) / 2,
        bracket_y + bracket_h + 0.015 * plot_range,
        format_pvalue(p_value),
        ha="center",
        va="bottom",
        fontsize=10.5,
        color="#111111",
    )


def collect_stats(
    sheet: str,
    labels: list[str],
    groups: list[np.ndarray],
    test_result: dict[str, float | str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, values in zip(labels, groups):
        rows.append(
            {
                "dataset": sheet,
                "group": label,
                "n": values.size,
                "mean": np.mean(values),
                "sd": np.std(values, ddof=1) if values.size > 1 else np.nan,
                "median": np.median(values),
                "q1": np.quantile(values, 0.25),
                "q3": np.quantile(values, 0.75),
                "test": test_result["test"],
                "test_statistic": test_result["statistic"],
                "degrees_of_freedom": test_result["df"],
                "p_value_two_sided": test_result["p_value"],
            }
        )
    return rows


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

    for panel_index, panel in enumerate(panels):
        ax = axes_flat[panel_index]
        labels, groups = read_panel(input_file, panel)
        test_result = run_test(groups)
        stats_rows.extend(collect_stats(panel["sheet"], labels, groups, test_result))

        positions = np.arange(1, len(groups) + 1, dtype=float)
        boxplot = ax.boxplot(
            groups,
            positions=positions,
            widths=0.55,
            patch_artist=True,
            showfliers=False,
            whis=1.5,
            tick_labels=labels,
            medianprops={"color": "#1F1F1F", "linewidth": 1.6},
            whiskerprops={"color": "#4A4A4A", "linewidth": 1.0},
            capprops={"color": "#4A4A4A", "linewidth": 1.0},
            boxprops={"edgecolor": "#4A4A4A", "linewidth": 1.0},
        )
        panel_colors = [colors[i % len(colors)] for i in range(len(groups))]
        for patch, color in zip(boxplot["boxes"], panel_colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.78)

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

        all_values = np.concatenate(groups)
        data_min = float(np.min(all_values))
        data_max = float(np.max(all_values))
        data_span = data_max - data_min if data_max > data_min else 1.0
        auto_lower = max(0.0, data_min - 0.08 * data_span) if data_min >= 0 else data_min - 0.12 * data_span
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

        ax.set_title(str(panel.get("title", panel["sheet"])), pad=10, fontweight="semibold")
        ax.set_ylabel(str(panel.get("y_label", panel["sheet"])))
        ax.set_xlim(0.45, len(groups) + 0.55)
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

    footer = config.get("footer")
    if footer is None:
        footer = f"{STUDENT_TEST_LABEL}; each point represents one observation"

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
        fontsize=8.5,
        color="#4A4A4A",
    )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(output_pdf, metadata={"Title": str(config.get("pdf_title", "Scientific boxplots"))}) as pdf:
        pdf.savefig(fig, bbox_inches="tight", pad_inches=0.08)

    if preview_file is not None:
        preview_file.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(preview_file, dpi=180, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)

    pd.DataFrame(stats_rows).to_csv(stats_file, sep="\t", index=False, float_format="%.10g")


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

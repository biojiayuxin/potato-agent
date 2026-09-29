"""Regression checks for bar heights, SD intervals, and shared statistics.

Run with python3 -m unittest discover -s <skill-directory>/tests -v.
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import barplot_from_excel as barplot


class BarplotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.addCleanup(barplot.plt.close, "all")
        self.root = Path(self.workspace.name)
        self.input_file = self.root / "measurements.xlsx"

    def render(self, config: dict):
        output_pdf = self.root / "figures" / "bars.pdf"
        stats_file = self.root / "statistics" / "bars.tsv"
        with patch.object(barplot.plt, "close") as close:
            barplot.create_figure(self.input_file, output_pdf, stats_file, None, config)
        self.assertTrue(output_pdf.read_bytes().startswith(b"%PDF-"))
        return close.call_args.args[0], pd.read_csv(stats_file, sep="\t")

    def test_mean_sd_and_student_test_from_observations(self) -> None:
        pd.DataFrame({"Control": [1, 3], "Treatment": [2, 6]}).to_excel(
            self.input_file, sheet_name="Trait", index=False
        )
        fig, table = self.render({"panels": [{"sheet": "Trait"}]})
        ax = fig.axes[0]
        np.testing.assert_allclose([bar.get_height() for bar in ax.patches], [2, 4])
        np.testing.assert_allclose(table["sd"], [math.sqrt(2), math.sqrt(8)])
        error_intervals = ax.containers[0].lines[2][0].get_segments()
        for segment, mean, sd in zip(error_intervals, [2, 4], [math.sqrt(2), math.sqrt(8)]):
            np.testing.assert_allclose(segment[:, 1], [mean - sd, mean + sd])

        # Hand-calculated pooled-variance t-test: t = -2/sqrt(5), df = 2.
        expected_t = -2 / math.sqrt(5)
        expected_p = 1 - abs(expected_t) / math.sqrt(expected_t**2 + 2)
        np.testing.assert_allclose(table["test_statistic"], expected_t)
        np.testing.assert_allclose(table["degrees_of_freedom"], 2)
        np.testing.assert_allclose(table["p_value_two_sided"], expected_p)
        self.assertIn("Student's t-test", fig.texts[0].get_text())
        self.assertIn("mean ± SD", fig.texts[0].get_text())
        self.assertTrue(any("P =" in label.get_text() for label in ax.texts))

    def test_axes_and_bracket_include_zero_points_and_sd(self) -> None:
        groups_by_sheet = {
            "Positive": ([1, 3], [2, 6]),
            "Negative": ([-1, -3], [-2, -6]),
            "Mixed": ([-2, 2], [0, 4]),
        }
        with pd.ExcelWriter(self.input_file) as writer:
            for sheet, (control, treatment) in groups_by_sheet.items():
                pd.DataFrame({"Control": control, "Treatment": treatment}).to_excel(
                    writer, sheet_name=sheet, index=False
                )
        fig, _ = self.render(barplot.load_config(None, self.input_file))
        self.assertFalse(fig.axes[-1].get_visible())
        for ax in fig.axes[:3]:
            with self.subTest(sheet=ax.get_title()):
                lower, upper = ax.get_ylim()
                self.assertLessEqual(lower, 0)
                self.assertGreaterEqual(upper, 0)
                error_intervals = ax.containers[0].lines[2][0].get_segments()
                for segment in error_intervals:
                    self.assertLessEqual(lower, min(segment[:, 1]))
                    self.assertGreaterEqual(upper, max(segment[:, 1]))
                # The final line is the significance bracket, above all data and SD caps.
                bracket_bottom = min(ax.lines[-1].get_ydata())
                self.assertGreater(bracket_bottom, max(max(s[:, 1]) for s in error_intervals))
                self.assertGreater(bracket_bottom, max(map(max, groups_by_sheet[ax.get_title()])))
                self.assertLess(ax.texts[-1].get_position()[1], upper)

    def test_hiding_points_preserves_statistics_and_jitter_is_repeatable(self) -> None:
        pd.DataFrame({"Control": [1, 3, None], "Treatment": [2, 6, 8]}).to_excel(
            self.input_file, sheet_name="Trait", index=False
        )
        config = {"panels": [{"sheet": "Trait"}], "jitter_seed": 42}
        first_fig, first_table = self.render(config)
        second_fig, second_table = self.render(config)
        for first, second in zip(first_fig.axes[0].collections[1:], second_fig.axes[0].collections[1:]):
            np.testing.assert_array_equal(first.get_offsets(), second.get_offsets())
        hidden_fig, hidden_table = self.render({**config, "show_points": False})
        pd.testing.assert_frame_equal(first_table, second_table)
        pd.testing.assert_frame_equal(first_table, hidden_table)
        np.testing.assert_array_equal(hidden_table["n"], [2, 3])
        self.assertEqual(len(hidden_fig.axes[0].collections), 1)  # SD intervals only.
        self.assertNotIn("each point", hidden_fig.texts[0].get_text())

    def test_invalid_groups_and_truncated_baseline_are_rejected(self) -> None:
        cases = [
            ({"A": [1, 2], "B": [3, 4], "C": [5, 6]}, {}, "exactly two groups"),
            ({"A": [1, None], "B": [3, 4]}, {}, "at least two observations"),
            ({"A": [1, 2], "B": [3, 4]}, {"y_min": 1}, "must include zero"),
        ]
        for columns, limits, message in cases:
            with self.subTest(message=message):
                pd.DataFrame(columns).to_excel(self.input_file, sheet_name="Trait", index=False)
                with self.assertRaisesRegex(ValueError, message):
                    self.render({"panels": [{"sheet": "Trait", **limits}]})
                self.assertFalse((self.root / "figures" / "bars.pdf").exists())
                self.assertFalse((self.root / "statistics" / "bars.tsv").exists())


if __name__ == "__main__":
    unittest.main()

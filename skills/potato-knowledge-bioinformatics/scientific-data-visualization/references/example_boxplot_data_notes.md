# Example boxplot, violin-plot, and barplot data

`example_boxplot_data.xlsx` is a fully synthetic, de-identified input shared by `../scripts/boxplot_from_excel.py`, `../scripts/violin_from_excel.py`, and `../scripts/barplot_from_excel.py`. The workbook is provided only to demonstrate the expected input structure and command-line workflow.

## Deliberate workbook structure

- `Continuous trait`: two independent numeric columns, `Control` and `Treatment`.
- `Count trait`: two independent integer-count columns, `Control` and `Treatment`.
- Each group contains 50 synthetic observations.
- `example_boxplot_config.json` supplies panel titles, Y-axis labels, colors, and layout. All three scripts always use a two-sided Student's t-test.
- Barplots show group means with sample standard deviation (SD, `ddof=1`) error bars and raw observations. These are not SEM or confidence intervals. Set `show_points` to `false` to hide points; `barplot_footer` customizes the barplot caption.

The source gene, treatment, phenotype, project, and sample identifiers are not included. Values were generated with a fixed random seed from generic probability distributions and must not be interpreted as real biological results.

## Usage

Use the Hermes command patterns in `SKILL.md`. All three plot types use this workbook
and `example_boxplot_config.json`; write generated files to the user's working
directory, not this skill directory. The PDFs are the formal vector outputs.
PNG files are previews only, and TSV files record descriptive statistics and
two-sided Student's t-test results.

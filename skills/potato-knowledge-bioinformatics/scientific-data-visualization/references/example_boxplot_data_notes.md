# Example boxplot and violin-plot data

`example_boxplot_data.xlsx` is a fully synthetic, de-identified input shared by `../scripts/boxplot_from_excel.py` and `../scripts/violin_from_excel.py`. The workbook is provided only to demonstrate the expected input structure and command-line workflow.

## Deliberate workbook structure

- `Continuous trait`: two independent numeric columns, `Control` and `Treatment`.
- `Count trait`: two independent integer-count columns, `Control` and `Treatment`.
- Each group contains 50 synthetic observations.
- `example_boxplot_config.json` supplies panel titles, Y-axis labels, colors, and layout. Both scripts always use a two-sided Student's t-test.

The source gene, treatment, phenotype, project, and sample identifiers are not included. Values were generated with a fixed random seed from generic probability distributions and must not be interpreted as real biological results.

## Usage

Use the Hermes command patterns in `SKILL.md`. Both plot types use this workbook
and `example_boxplot_config.json`; write generated files to the user's working
directory, not this skill directory. The PDFs are the formal vector outputs.
PNG files are previews only, and TSV files record descriptive statistics and
two-sided Student's t-test results.

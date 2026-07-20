# DMv8.2 Gene Function Prediction

This directory contains a script-controlled replacement for the Agent-orchestrated workflow. The main process has four fixed stages:

1. Resolve potato gene names, query name-deduplicated Potato RAG, and summarize the returned potato evidence.
2. Resolve the single Arabidopsis, rice, and maize homolog recorded for each potato gene, query the fixed official source and PubMed, and summarize each homolog independently. When TAIR returns two or more deduplicated names, a small LLM step selects retrieval symbols; every selected name then drives its own sequential PlantConnectome and PubMed queries. Rice PubMed retrieval runs once per deduplicated non-empty symbol from all exact RiceData matches.
3. Summarize Expression Atlas TPM by tissue as `mean +/- sample SD`. Technical runs are averaged within each `(sample_name, tissue)` before tissue statistics are calculated.
4. Send the compact potato, homolog, and expression summaries to the LLM for final function prediction and reliability grading.

No main Agent or child Agent is involved. Raw source calls and LLM responses are cached under the selected output directory, so an interrupted run can be restarted with the same command.

## Install

Use an environment that can read the Expression Atlas files. In deployment this is normally the `potato_agent` user.

```bash
python3 -m venv /opt/gene-function-prediction-env
/opt/gene-function-prediction-env/bin/pip install -r gene_function_prediction/requirements.txt
```

Configure an OpenAI-compatible Responses endpoint. See `.env.example` for all optional settings.

```bash
export GENE_FUNCTION_LLM_BASE_URL='https://YOUR_ENDPOINT/v1'
export GENE_FUNCTION_LLM_API_KEY='YOUR_API_KEY'
export GENE_FUNCTION_LLM_MODEL='gpt-5.6-sol'
```

If no model variable is set, the client uses `gpt-5.6-sol`. Every Responses request uses reasoning effort `xhigh`. `GENE_FUNCTION_LLM_STRUCTURED_MODE=prompt` works with basic Responses-compatible servers. Set it to `schema` only when the endpoint supports Responses `text.format` JSON schema.

## Run

Gene IDs are used exactly as provided. The pipeline does not convert them to another naming system or remove transcript suffixes. Use the representative transcript IDs present in the homolog tables when homolog evidence is required.

```text
DM8.2_chr01G42450.1
DM8.2_chr02G04100.1
```

Check inputs and target counts without performing network or LLM calls:

```bash
python3 gene_function_prediction/run_pipeline.py \
  --genes genes.txt \
  --output-dir work/gene_function_prediction_v2 \
  --preflight
```

Run the workflow:

```bash
python3 gene_function_prediction/run_pipeline.py \
  --genes genes.txt \
  --output-dir work/gene_function_prediction_v2
```

Use `--force-sources` to refresh database/RAG/PubMed calls and `--force-llm` to regenerate summaries. Otherwise matching cached requests are reused. Transient source request failures are retried three times after the initial attempt by default; change this with `--source-retries`.

## Fixed Retrieval Plan

- Potato names: Potato Knowledge Hub gene search queried directly with the user-provided gene ID.
- Potato RAG: one query per deduplicated gene name, `top_k_retrieve=20`, `top_k_rerank=5`, RAG-only.
- Arabidopsis: TAIR first. Zero names skip both downstream sources; one name is used directly; two or more names are reduced by an LLM that may only select from the TAIR-provided names. Each retained name is queried sequentially in PlantConnectome and PubMed, and its evidence remains in a separate group. PlantConnectome retrieves up to 10 entities and 5 edges per entity, so up to 50 relationships per retained name are sent to the summary LLM without a second edge cap. Each relationship contains only `entity_1`, `relationship`, `entity_2`, and `citation`; no species-string filter is applied in code. PlantConnectome HTML is requested with gzip compression.
- Rice: RiceData HTML query using the gene-level target ID. All exact-match rows are retained, their non-empty gene symbols are deduplicated, and PubMed is queried once per symbol. PubMed is skipped when no exact row or no non-empty symbol is returned.
- Maize: local `Zm00001eb.1.fulldata.txt`, joined by column 2, with gene name from column 11.
- Literature: PubMed only, up to 20 records per query by default. `--pubmed-limit` may be increased but cannot be set below 20.

These values can be changed explicitly with command-line options. L1/L2/L3 is retained as provenance but the final prompt forbids using it for reliability grading.

## Outputs

- `results/predictions.tsv`: compact user-facing table.
- `results/predictions.jsonl`: complete compact evidence packet and prediction per gene.
- `results/genes/*.json`: one inspectable final record per gene.
- `evidence/{potato,arabidopsis,rice,maize}/*.json`: temporary source summaries.
- `evidence/expression.json`: tissue-level `mean`, `SD`, source count, and run count.
- `cache/sources/`: raw source responses keyed by query parameters.
- `cache/llm/`: model results keyed by prompt, model, schema, and evidence payload.
- `run_report.json`: predicted and blocked gene counts, per-gene blocking reasons, and source/LLM errors.

The pipeline is fail-closed per gene. If a required source, parser, or LLM request fails after all retries, the affected gene is written as a diagnostic `blocked` record without a `prediction`; its species-summary and/or final LLM call is not attempted. Blocked genes are excluded from `predictions.tsv` and `predictions.jsonl`, while unrelated genes with complete evidence can still finish. The run report is `complete`, `incomplete`, or `failed`, and any non-complete run exits with status 2.

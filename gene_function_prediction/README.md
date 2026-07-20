# DMv8.2 Gene Function Prediction

This directory contains a script-controlled replacement for the Agent-orchestrated workflow. The main process has four fixed stages:

1. Resolve potato gene names, query name-deduplicated Potato RAG, and summarize the returned potato evidence.
2. Resolve the single Arabidopsis, rice, and maize homolog recorded for each potato gene, query the fixed official source and PubMed, and summarize each homolog independently. When TAIR returns two or more deduplicated names, a small LLM step selects retrieval symbols; every selected name then drives its own sequential PlantConnectome and PubMed queries. Rice PubMed retrieval runs once per deduplicated non-empty symbol from all exact RiceData matches.
3. Summarize Expression Atlas TPM by tissue as `mean +/- sample SD`. Technical runs are averaged within each `(sample_name, tissue)` before tissue statistics are calculated.
4. Send the compact potato, homolog, and expression summaries to the LLM for final function prediction and reliability grading.

No main Agent or child Agent is involved. Raw source calls and LLM responses are cached under the selected output directory, so an interrupted run can be restarted with the same command.

## Isolation Contract

`gene_function_prediction/` is an independent code ownership boundary. The following rules are mandatory for every change in this directory:

- Pipeline-owned retrieval, parsing, validation, prompting, caching, and orchestration logic must be implemented and tested inside this directory.
- Do not import implementation code from `skills/`, `interface/`, `hermes-lite/`, `hermes-agent/`, or another repository module.
- Do not fix this pipeline by editing a similarly named skill. A skill may be read as a reference, but its files and this directory must evolve independently and changes on either side must not alter the other side's runtime behavior.
- External databases, HTTP APIs, LLM endpoints, and reference datasets are inputs, not shared implementation code. Their URLs, paths, formats, timeouts, and retry behavior must be explicit and testable here.
- New source adapters must live in this directory. Do not introduce new default paths or subprocess calls to scripts elsewhere in the repository.
- `run_pipeline.py`, its prompts, requirements, and tests must remain directly runnable without Agent or skill dispatch.

The Potato gene search, Potato RAG, TAIR, PlantConnectome, RiceData, PubMed, and local maize adapters are all owned by `run_pipeline.py`. The pipeline does not import or execute any skill script. Copying this directory does not copy behavior from another repository module, and changes to a similarly named skill cannot alter this pipeline.

## Install

Use an environment that can read the reference data files. From the parent directory containing `gene_function_prediction/`:

```bash
cd gene_function_prediction
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Configure an OpenAI-compatible Responses endpoint. See `.env.example` for all optional settings.

```bash
export GENE_FUNCTION_LLM_BASE_URL='https://YOUR_ENDPOINT/v1'
export GENE_FUNCTION_LLM_API_KEY='YOUR_API_KEY'
export GENE_FUNCTION_LLM_MODEL='gpt-5.6-sol'
```

`.env.example` is documentation only and is not loaded automatically. Export the variables in the shell before starting the pipeline.

If no model variable is set, the client uses `gpt-5.6-sol`. Every Responses request uses reasoning effort `xhigh`. `GENE_FUNCTION_LLM_STRUCTURED_MODE=prompt` works with basic Responses-compatible servers. Set it to `schema` only when the endpoint supports Responses `text.format` JSON schema.

## Run

Gene IDs are used exactly as provided. The pipeline does not convert them to another naming system or remove transcript suffixes. Use the representative transcript IDs present in the homolog tables when homolog evidence is required.

```text
DM8.2_chr01G42450.1
DM8.2_chr02G04100.1
```

Check inputs and target counts without performing network or LLM calls:

```bash
.venv/bin/python run_pipeline.py \
  --genes /path/to/genes.txt \
  --output-dir /path/to/work/gene_function_prediction_v2 \
  --homolog-dir /path/to/ref_homlogs \
  --expression-matrix /path/to/transcript_tpm_matrix_merged.tsv \
  --expression-metadata /path/to/sample_tissue_list.tsv \
  --maize-data /path/to/Zm00001eb.1.fulldata.txt \
  --preflight
```

Run the workflow:

```bash
.venv/bin/python run_pipeline.py \
  --genes /path/to/genes.txt \
  --output-dir /path/to/work/gene_function_prediction_v2 \
  --homolog-dir /path/to/ref_homlogs \
  --expression-matrix /path/to/transcript_tpm_matrix_merged.tsv \
  --expression-metadata /path/to/sample_tissue_list.tsv \
  --maize-data /path/to/Zm00001eb.1.fulldata.txt
```

Use `--force-sources` to refresh database/RAG/PubMed calls and `--force-llm` to regenerate summaries. Otherwise matching cached requests are reused. Transient source request failures are retried three times after the initial attempt by default; change this with `--source-retries`.

## Fixed Retrieval Plan

- Potato names: Potato Knowledge Hub gene search queried directly with the user-provided gene ID.
- Potato RAG: one query per deduplicated gene name, `top_k_retrieve=20`, `top_k_rerank=5`, RAG-only.
- Arabidopsis: the TAIR and PlantConnectome clients are implemented directly in `run_pipeline.py`; no Arabidopsis skill script is called. TAIR runs first. Zero names skip both downstream sources; one name is used directly; two or more names are reduced by an LLM that may only select from the TAIR-provided names. Each retained name is queried sequentially in PlantConnectome and PubMed, and its evidence remains in a separate group. PlantConnectome retrieves up to 10 entities and 5 edges per entity, so up to 50 relationships per retained name are sent to the summary LLM without a second edge cap. Each relationship contains only `entity_1`, `relationship`, `entity_2`, and `citation`; no species-string filter is applied in code. PlantConnectome HTML is requested with gzip compression.
- Rice: RiceData HTML query using the gene-level target ID. All exact-match rows are retained, their non-empty gene symbols are deduplicated, and PubMed is queried once per symbol. PubMed is skipped when no exact row or no non-empty symbol is returned.
- Maize: local `Zm00001eb.1.fulldata.txt`, joined by column 2, with gene name from column 11.
- Literature: PubMed only, up to 20 records per query by default. `--pubmed-limit` may be increased but cannot be set below 20.

These values can be changed explicitly with command-line options. L1/L2/L3 is retained as provenance but the final prompt forbids using it for reliability grading.

## External Inputs

Independent execution means no dependency on code elsewhere in this repository; it does not mean offline execution. The pipeline requires:

- Three homolog tables under `--homolog-dir`: `DMv82_Arabidopsis_homologs_L1_L2_L3.tsv`, `DMv82_OsMSU7_homologs_L1_L2_L3.tsv`, and `DMv82_ZmNAM5_homologs_L1_L2_L3.tsv`.
- The Expression Atlas matrix and metadata supplied by `--expression-matrix` and `--expression-metadata`.
- The maize annotation file supplied by `--maize-data`.
- An OpenAI-compatible Responses endpoint configured with `GENE_FUNCTION_LLM_*` variables.
- Network access to the Potato gene API, Potato RAG, TAIR, PlantConnectome, RiceData, NCBI PubMed, and the configured LLM endpoint.

The Potato and PubMed endpoints can be redirected without changing code:

```text
--potato-gene-base-url / GENE_FUNCTION_POTATO_GENE_BASE_URL
--potato-rag-base-url  / GENE_FUNCTION_POTATO_RAG_BASE_URL
--pubmed-api-base-url  / GENE_FUNCTION_PUBMED_API_BASE_URL
```

## Test

From inside `gene_function_prediction/`, run the complete self-contained test suite with the parent directory as the unittest import root:

```bash
.venv/bin/python -m unittest discover -s tests -t ..
```

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

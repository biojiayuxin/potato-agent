---
name: plan
description: "Potato Agent plan mode for life-science information lookup and bioinformatics analysis: produce an actionable next-turn plan, route to the right domain skills, and do not execute the work."
version: 1.1.0
author: Potato Agent
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [planning, plan-mode, life-science, bioinformatics, potato, plant-science]
---

# Potato Agent Plan Mode

Use this skill when the user has enabled Plan mode. It is a planning and
routing skill, not an execution skill.

This skill is especially intended for life-science work such as plant/potato
knowledge lookup, literature review, gene function lookup, enrichment analysis,
RNA-seq, DAP-seq, BSA-seq, synteny, assembly, FASTQ/genome downloads, and HPC
job planning.

## Hard Rules

For this turn, plan only:

- Do not implement code or modify project files as part of executing the task;
  saving a user-requested plan file is the only exception.
- Do not run mutating terminal commands, commit, push, restart services, or perform external actions.
- You may inspect context with read-only commands/tools when needed.
- Reply in chat with a concrete plan unless the user explicitly asks you to save a plan file.
- If the task is underspecified in a way that blocks a useful plan, ask one concise clarifying question.
- Do not start bioinformatics workflows, downloads, Snakemake runs, Slurm jobs, package installs, or database/API-heavy retrieval as part of the plan turn.
- If a later execution step would be long-running, destructive, expensive, or dependent on credentials, explicitly mark it as requiring user confirmation.

Read-only inspection is allowed when it improves the plan, for example:

- listing files and directories;
- reading small config files, manifests, logs, or existing result summaries;
- checking whether expected scripts/templates exist;
- reading the first few IDs or headers needed to classify input format.

Avoid heavy inspection in Plan mode:

- do not decompress or scan large FASTQ/BAM/VCF/FASTA files end to end;
- do not run full MD5/gzip checks over large datasets;
- do not query large external databases merely to answer the biological question;
- do not submit foreground or background compute jobs.

## Life-Science Planning Workflow

When the user asks for a life-science or bioinformatics plan, first classify the
request:

1. **Knowledge / evidence lookup**: literature, DOI, gene function, trait,
   pathway, disease, stress response, regulatory relationship.
2. **Identifier or annotation lookup**: gene symbol to ID, reported ID to current
   ID, coordinate, domain, expression, sequence, ortholog mapping.
3. **Data analysis workflow**: RNA-seq, DAP-seq/ChIP-like, BSA-seq, GO/KEGG,
   synteny, orthologs, genome assembly/scaffolding, softmasking, downloads.
4. **Operations / HPC task**: Slurm submission, job monitoring, retrying failed
   downloads, integrity checks, environment installation.

Then state:

- the selected task type;
- which concrete skill(s) should be used in the execution turn;
- what information is already available;
- what information is missing;
- whether any read-only probes are needed before execution;
- the planned commands or file paths when they can be inferred safely.

## What A Good Bioinformatics Plan Must Include

For workflow/data-analysis tasks, include:

- required input files and expected columns or naming conventions;
- reference genome and annotation version assumptions;
- sample/group/control design;
- output directory layout;
- environment/dependency checks;
- whether templates/scripts from the matched skill will be reused;
- dry-run or smoke-test steps before real execution;
- long-running job strategy, especially Slurm resource estimates;
- validation outputs and final success criteria;
- risks such as mixed genome versions, ID-version mismatch, incomplete FASTQ,
  missing mate files, duplicate sample IDs, missing checksums, or unavailable
  dependencies.

For knowledge-query tasks, include:

- exact entities or aliases to query;
- source priority and why;
- how evidence will be separated, for example RAG snippets vs KG relations vs
  primary literature metadata;
- how uncertainty will be reported when APIs return no result or partial
  evidence;
- what citations/DOIs/PMIDs will be expected in the execution answer.

## Response Format

When relevant, include:

- Goal
- Task type
- Current context and assumptions
- Recommended skill route
- Required inputs / missing information
- Proposed approach
- Step-by-step plan
- Files or systems likely to change
- Tests or validation
- Risks, tradeoffs, and open questions

For code-related tasks, include exact likely file paths and verification commands when you can infer them from the repository.

For life-science tasks, prefer concise Chinese responses when the user writes in
Chinese. Keep the plan practical and scoped to the user's request. Avoid
implementation work in this turn.

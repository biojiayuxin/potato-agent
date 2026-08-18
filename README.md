# Potato Agent

Potato Agent is an AI-native platform for potato data and knowledge exploration. A live
deployment is available at
[https://potato-agent.ynnu.edu.cn/](https://potato-agent.ynnu.edu.cn/).

## Overview

As research enters the era of AI agents, we are developing potato multi-omics databases
that agents can query and use directly. These resources support agent-driven workflows
for connecting and interpreting potato genomic, transcriptomic, and other omics data.

Alongside these databases, we develop specialized skills for potato data and knowledge
exploration, including database queries, gene and expression analysis, comparative
genomics, and bioinformatics workflows. See
[`skills/potato-knowledge-bioinformatics/`](skills/potato-knowledge-bioinformatics/)
for the available skills.

Potato Agent is built on [Hermes Agent](hermes-agent/). We substantially streamlined the
original codebase by removing components that are unnecessary for this project while
preserving the core agent loop. The resulting [Hermes Lite](hermes-lite/) runtime powers
Potato Agent as a multi-user platform designed for concurrent use on HPC systems.

## Repository Layout

- [`interface/`](interface/) contains the web application and data exploration interfaces.
- [`hermes-lite/`](hermes-lite/) is the primary Potato-owned agent runtime and development tree.
- [`skills/potato-knowledge-bioinformatics/`](skills/potato-knowledge-bioinformatics/) contains skills for potato data and knowledge exploration.
- [`hermes-agent/`](hermes-agent/) is retained as the upstream reference and rollback baseline.

## HPC Deployment

For installation and deployment on an HPC system, see the [HPC Deployment Guide](HPC_DEPLOYMENT.md).

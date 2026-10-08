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

Potato Agent is a multi-user agent platform developed for research communities and HPC
environments. Its agent core adapts selected open-source components from
[Hermes Agent](hermes-agent/), including the agent loop, tool and skill use, contextual
memory, and sub-agent collaboration. Around this core, we developed the multi-user
architecture—including access control, isolated user workspaces, centralized model
proxying, and the agent gateway—and packaged the streamlined runtime as
[Hermes Lite](hermes-lite/).

## Repository Layout

- [`interface/`](interface/) contains the web application and data exploration interfaces.
- [`hermes-lite/`](hermes-lite/) is the primary Potato-owned agent runtime and development tree.
- [`skills/potato-knowledge-bioinformatics/`](skills/potato-knowledge-bioinformatics/) contains skills for potato data and knowledge exploration.
- [`hermes-agent/`](hermes-agent/) is retained as the upstream reference and rollback baseline.

## HPC Deployment

For installation and deployment on an HPC system, see the [HPC Deployment Guide](HPC_DEPLOYMENT.md).
For this model upgrade on YNNU, follow the [production upgrade runbook](docs/YNNU_MODEL_UPGRADE.md),
including the separate transition artifact when upgrading an older installation.

## Conversation Models

Each chat saves its own model choice. A response or approval in one chat does not
prevent choosing a model in another. New chats use the catalog's default option;
branches inherit their source chat's choice.

Model labels, upstream model names, API modes, reasoning effort, and context
windows are defined in one protected runtime catalog, `model_proxy.yaml`.
SQLite saves stable option IDs and separate execution snapshots. Upstream API
addresses and credentials stay private to the model proxy. See the
[model configuration contract](interface/MODEL_CATALOG.md) and the
[catalog deployment prerequisites](HPC_DEPLOYMENT.md#model-catalog-migration).

Deploy Interface and Hermes Lite together. Deployment versions and configuration
are specific to each host; a local test deployment does not update the public
YNNU service.

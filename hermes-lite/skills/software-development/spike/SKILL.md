---
name: spike
description: "Throwaway experiments to validate an idea before build."
version: 1.0.0
author: Hermes Agent (adapted from gsd-build/get-shit-done)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [spike, prototype, experiment, feasibility, throwaway, exploration, research, planning, mvp, proof-of-concept]
    related_skills: [sketch, subagent-driven-development, plan]
---

# Spike

Use this skill when the user wants to **feel out an idea** before committing to a real build — validating feasibility, comparing approaches, or resolving unknowns through experiments. Keep implementation focused on the feasibility question; production hardening is separate work.

Load this when the user says things like "let me try this", "I want to see if X works", "spike this out", "before I commit to Y", "quick prototype of Z", "is this even possible?", or "compare A vs B".

## When NOT to use this

- The answer is knowable from docs or reading code — just do research, don't build
- The work is production path — follow the project's implementation workflow
- The idea is already validated — jump straight to implementation

## If the user has the full GSD system installed

If `gsd-spike` shows up as a sibling skill (installed via `npx get-shit-done-cc --hermes`), prefer **`gsd-spike`** when the user wants the full GSD workflow: persistent `.planning/spikes/` state, MANIFEST tracking across sessions, Given/When/Then verdict format, and commit patterns that integrate with the rest of GSD. This skill is the lightweight standalone version for users who don't have (or don't want) the full system.

## Core method

Use these stages to answer the feasibility question, adapting the work to the actual unknowns:

```
question  →  research  →  experiment  →  verdict
```

### 1. Define the Question

Identify the unresolved feasibility question and the observable evidence that would answer it. Split the work when distinct unknowns need separate experiments. For multiple questions, a table can help track their scope and priority:

| # | Spike | Validates (Given/When/Then) | Risk |
|---|-------|----------------------------|------|
| 001 | websocket-streaming | Given a WS connection, when LLM streams tokens, then client receives chunks < 100ms | High |
| 002a | pdf-parse-pdfjs | Given a multi-page PDF, when parsed with pdfjs, then structured text is extractable | Medium |
| 002b | pdf-parse-camelot | Given a multi-page PDF, when parsed with camelot, then structured text is extractable | Medium |

**Spike types:**
- **standard** — one approach answering one question
- **comparison** — same question, different approaches (shared number, letter suffix `a`/`b`/`c`)

**Good spike questions:** specific feasibility with observable output.
**Bad spike questions:** too broad, no observable output, or just "read the docs about X".

**Order by risk.** The spike most likely to kill the idea runs first. No point prototyping the easy parts if the hard part doesn't work.

One experiment is sufficient when it answers the user's question.

### 2. Resolve Scope When Needed

Use the request, conversation context, and existing authorization to choose the experiment. Ask for clarification when unresolved choices materially affect the intended outcome or scope. For multiple experiments, explain their relationship to the user's question.

### 3. Research (per spike, before building)

Spikes are not research-free — you research enough to pick the right approach, then you build. Per spike:

1. **Identify the key unknown.** State what the experiment will establish.
2. **Surface competing approaches** if there's real choice:

   | Approach | Tool/Library | Pros | Cons | Status |
   |----------|-------------|------|------|--------|
   | ... | ... | ... | ... | maintained / abandoned / beta |

3. **Pick an approach.** State why. Build additional variants when comparing them helps answer the user's feasibility question and falls within the authorized scope.
4. **Skip research** for pure logic with no external dependencies.

Use Hermes tools for the research step:

- `browser_navigate(url="https://websockets.readthedocs.io/")` — open relevant documentation or search pages
- `browser_snapshot()` — inspect the current page
- `terminal("pip show websockets | grep Version")` — check what's installed in the project's venv

For libraries without docs pages, clone and read their `README.md` / `examples/` via `read_file`.

### 4. Build

Use the repository's conventions for experiment files. For separate experiments that need their own files, a layout like this can keep them independent:

```
spikes/
├── 001-websocket-streaming/
│   ├── README.md
│   └── main.py
├── 002a-pdf-parse-pdfjs/
│   ├── README.md
│   └── parse.js
└── 002b-pdf-parse-camelot/
    ├── README.md
    └── parse.py
```

Choose an observable experiment suited to the question. Possible forms include:

1. A runnable CLI that takes input and prints observable output
2. A minimal HTML page that demonstrates the behavior
3. A small web server with one endpoint
4. A unit test that exercises the question with recognizable assertions

Verify the behavior the verdict depends on, including relevant edge cases and unexpected results that could change the conclusion. Once the question is answered and necessary verification is complete, use the task-completion guidance to decide whether further work is warranted.

**Avoid** unless the spike specifically requires it: complex package management, build tools/bundlers, Docker, env files, config systems. Hardcode everything — it's a spike.

**Building one spike** — a typical tool sequence:

```
terminal("mkdir -p spikes/001-websocket-streaming")
write_file("spikes/001-websocket-streaming/README.md", "# 001: websocket-streaming\n\n...")
write_file("spikes/001-websocket-streaming/main.py", "...")
terminal("cd spikes/001-websocket-streaming && python3 main.py")
# Observe output, iterate.
```

**Parallel comparison spikes (002a / 002b).** When the task calls for comparing independently runnable experiments, `delegate_task` can separate substantial work:

```
delegate_task(tasks=[
    {"goal": "Build 002a-pdf-parse-pdfjs: ...", "toolsets": ["terminal", "file", "browser"]},
    {"goal": "Build 002b-pdf-parse-camelot: ...", "toolsets": ["terminal", "file", "browser"]},
])
```

Each subagent returns its own verdict; you write the head-to-head.

### 5. Verdict

Report the question, evidence, conclusion, and remaining uncertainty. When the experiment has a `README.md` or another requested record, a verdict can use this format:

```markdown
## Verdict: VALIDATED | PARTIAL | INVALIDATED

### What worked
- ...

### What didn't
- ...

### Surprises
- ...

### Recommendation for the real build
- ...
```

**VALIDATED** = the core question was answered yes, with evidence.
**PARTIAL** = it works under constraints X, Y, Z — document them.
**INVALIDATED** = doesn't work, for this reason. This is a successful spike.

## Comparison spikes

When comparing approaches is part of the task, run the relevant experiments and compare the dimensions that determine the user's choice:

```markdown
## Head-to-head: pdfjs vs camelot

| Dimension | pdfjs (002a) | camelot (002b) |
|-----------|--------------|----------------|
| Extraction quality | 9/10 structured | 7/10 table-only |
| Setup complexity | npm install, 1 line | pip + ghostscript |
| Perf on 100-page PDF | 3s | 18s |
| Handles rotated text | no | yes |

**Winner:** pdfjs for our use case. Camelot if we need table-first extraction later.
```

## Frontier mode (picking what to spike next)

If spikes already exist and the user says "what should I spike next?", walk the existing directories and look for:

- **Integration risks** — two validated spikes that touch the same resource but were tested independently
- **Data handoffs** — spike A's output was assumed compatible with spike B's input; never proven
- **Gaps in the vision** — capabilities assumed but unproven
- **Alternative approaches** — different angles for PARTIAL or INVALIDATED spikes

Propose candidates supported by the remaining unknowns, with the question each would answer. Let the user choose which to pursue.

## Output

- Report the experiment's evidence and verdict
- Keep any experiment files in the agreed location or the repository's established experiment directory
- Document results alongside retained code when needed to interpret or rerun it

## Attribution

Adapted from the GSD (Get Shit Done) project's `/gsd-spike` workflow — MIT © 2025 Lex Christopherson ([gsd-build/get-shit-done](https://github.com/gsd-build/get-shit-done)). The full GSD system offers persistent spike state, MANIFEST tracking, and integration with a broader spec-driven development pipeline; install with `npx get-shit-done-cc --hermes --global`.

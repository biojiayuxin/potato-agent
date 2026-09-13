---
name: hermes-agent-skill-authoring
description: "Author in-repo SKILL.md: frontmatter, validator, structure."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skills, authoring, hermes-agent, conventions, skill-md]
    related_skills: [plan, requesting-code-review]
---

# Authoring Hermes-Agent Skills (in-repo)

## Overview

There are two places a SKILL.md can live:

1. **User-local:** the active Hermes profile's `skills/<maybe-category>/<name>/SKILL.md`. Created via `skill_manage(action='create')`.
2. **In-repo (this skill is about this case):** `skills/<category>/<name>/SKILL.md` under the runtime source root. Use file tools to edit this source tree. `skill_manage(action='create')` targets the active profile instead.

Resolve the runtime source root from the current repository. The relative paths below assume the directory containing `tools/skill_manager_tool.py` and `skills/`.

## When to Use

- User asks you to add a skill "in this branch / repo / commit"
- You're adding a reusable workflow that should ship with the runtime
- You're editing an existing skill in the runtime source tree

## Required Frontmatter

Source of truth: `tools/skill_manager_tool.py::_validate_frontmatter`. Hard requirements:

- Starts with `---` as the first bytes (no leading blank line).
- Closes with `\n---\n` before the body.
- Parses as a YAML mapping.
- `name` field present.
- `description` field present, ≤ **1024 chars** (`MAX_DESCRIPTION_LENGTH`).
- Non-empty body after the closing `---`.

Example frontmatter with optional repository metadata:

```yaml
---
name: my-skill-name               # lowercase, hyphens, ≤64 chars (MAX_NAME_LENGTH)
description: Use when <trigger>. <one-line behavior>.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [short, descriptive, tags]
    related_skills: [other-skill, another-skill]
---
```

`version` / `author` / `license` / `metadata` are not enforced by the validator. Preserve existing metadata when editing and follow explicit repository requirements for new skills.

## Size Limits

- Description: ≤ 1024 chars (enforced).
- The system skill index truncates descriptions longer than 60 characters. Put the distinguishing purpose and trigger within that space.
- Full SKILL.md: ≤ 100,000 chars (enforced as `MAX_SKILL_CONTENT_CHARS`, ~36k tokens).

These are format limits, not target lengths. Include the guidance needed to perform the task; add examples and supporting files when they clarify the workflow.

## Structure

Choose sections that make the workflow clear. A possible structure is:

```
# <Title>

## Overview
One or two paragraphs: what and why.

## When to Use
- Bulleted triggers
- "Don't use for:" counter-triggers

## <Topic sections specific to the skill>
- Quick-reference tables are common
- Code blocks with exact commands
- Commands and examples verified against the target runtime

## Common Pitfalls
Numbered list of mistakes and their fixes.

## Verification Checklist
- [ ] Checkbox list of post-action verifications

## One-Shot Recipes (optional)
Named scenarios → concrete command sequences.
```

The section list is optional. Keep the purpose, applicable tasks, and actionable guidance easy to find. Move substantial conditional detail into supporting files and link it from the relevant section.

## Directory Placement

```
skills/<category>/<skill-name>/SKILL.md
```

Inspect the repository's `skills/` directory and pick the closest existing category.

## Workflow

1. **Check existing coverage** in the target category:
   ```
   ls skills/<category>/
   ```
   Read relevant skills to identify overlap and applicable repository conventions. Prefer extending an existing skill when it covers the workflow.
2. **Check validator constraints** in `tools/skill_manager_tool.py` if unsure.
3. **Draft** with `write_file` to `skills/<category>/<name>/SKILL.md`.
4. **Validate locally**:
   ```python
   from pathlib import Path
   from agent.skill_utils import parse_frontmatter
   from tools.skill_manager_tool import (
       _validate_content_size,
       _validate_frontmatter,
       _validate_name,
   )

   content = Path("skills/<category>/<name>/SKILL.md").read_text(encoding="utf-8")
   assert _validate_frontmatter(content) is None
   frontmatter, _ = parse_frontmatter(content)
   assert _validate_name(frontmatter["name"]) is None
   assert _validate_content_size(content) is None
   ```
5. **Report the change and verification.** Commit only when the user's existing authorization includes committing; stage the intended changes and inspect the staged diff first.

## Cross-Referencing Other Skills

Reference skills available in the target distribution. Treat optional user-installed skills as conditional guidance; their presence in one profile does not make them available to other users.

## Editing Existing In-Repo Skills

- **Small fix (typo, added pitfall, tightened trigger):** use `patch` on the resolved source path.
- **Major rewrite:** use `write_file` on that path.
- **Adding supporting files:** write the needed reference, template, script, or asset under the skill directory and link it from SKILL.md.
- `skill_manage` finds existing skills through the active profile's configured skill roots. Use it for repository edits only when the resolved skill is the intended source file.

## Common Pitfalls

1. **Using `skill_manage(action='create')` for an in-repo skill.** It writes to `~/.hermes/skills/`, not the repo tree. Use `write_file` for in-repo creation.

2. **Leading whitespace before `---`.** The validator checks `content.startswith("---")`; any leading blank line or BOM fails validation.

3. **Description too generic.** Describe the workflow and when it applies so the agent can distinguish it from adjacent skills.

4. **Changing unrelated metadata.** Preserve supported fields already present unless the requested change requires an update.

5. **Writing a skill that duplicates a peer.** Check relevant existing skills before creating another one.

6. **Confusing source edits with installation.** Repository edits appear in `skills_list` and `skill_view` only when that directory is a configured skill root or the skill has been installed. The current system prompt's skill index may remain cached; inspect the edited source file directly when validating a repository change.

7. **Linking to unavailable skills.** Check required references against the target distribution and mark optional integrations as conditional.

## Verification Checklist

- [ ] File is at the intended repository source path
- [ ] Frontmatter starts at byte 0 with `---`, closes with `\n---\n`
- [ ] Required frontmatter is valid and applicable repository metadata is preserved
- [ ] Name ≤ 64 chars, lowercase + hyphens
- [ ] Description meets the format limit and remains useful in the compact skill index
- [ ] Total file ≤ 100,000 chars
- [ ] Guidance supports the intended workflow without unnecessary sections or examples
- [ ] Required references resolve; optional integrations are identified

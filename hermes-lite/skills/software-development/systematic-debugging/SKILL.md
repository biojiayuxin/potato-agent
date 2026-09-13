---
name: systematic-debugging
description: "Diagnose faults and verify scoped fixes or mitigations."
version: 1.1.0
author: Hermes Agent (adapted from obra/superpowers)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [debugging, troubleshooting, problem-solving, root-cause, investigation]
    related_skills: [test-driven-development, plan, subagent-driven-development]
---

# Systematic Debugging

## Overview

Use evidence to explain failures, choose a scoped response, and verify the result.

**Core principle:** The investigation and remedy should serve the user's requested outcome. Base changes on a testable explanation of why they address that outcome.

## Diagnosis, Repair, and Recovery

For diagnosis or a lasting fix, investigate the cause and test the proposed correction.

For an authorized rollback or temporary recovery, establish the evidence and checks needed for that action, perform it, and verify recovery. Report any unresolved cause or remaining limitation. A recovery request can be complete before the full root cause is known.

## When to Use

Use when investigating or resolving technical faults such as:
- Test failures
- Bugs in production
- Unexpected behavior
- Performance problems
- Build failures
- Integration issues

Choose the investigative depth from the requested outcome and the uncertainty that affects the next action. Time pressure may make an authorized recovery the immediate goal while diagnosis remains a separate follow-up.

## The Four Phases

Use the phases to organize diagnosis and lasting fixes. Apply the steps relevant to the
current fault, and keep diagnostic changes and reference reading within that
scope. Expand the investigation when evidence points to another component or
an unresolved dependency.

---

## Phase 1: Root Cause Investigation

Gather evidence for a testable root-cause hypothesis.

### 1. Read Error Messages Carefully

- Don't skip past errors or warnings
- They often contain the exact solution
- Read stack traces completely
- Note line numbers, file paths, error codes

**Action:** Use `read_file` on the relevant source files. Use `search_files` to find the error string in the codebase.

### 2. Reproduce Consistently

- Can you trigger it reliably?
- What are the exact steps?
- Does it happen every time?
- If not reproducible → gather more data, don't guess

**Action:** Use the `terminal` tool to run the failing test or trigger the bug:

```bash
# Run specific failing test
pytest tests/test_module.py::test_name -v

# Run with verbose output
pytest tests/test_module.py -v --tb=long
```

### 3. Check Recent Changes

- What changed that could cause this?
- Git diff, recent commits
- New dependencies, config changes

**Action:**

```bash
# Recent commits
git log --oneline -10

# Uncommitted changes
git diff

# Changes in specific file
git log -p --follow src/problematic_file.py | head -100
```

### 4. Gather Evidence in Multi-Component Systems

**WHEN the failure may cross component boundaries (API → service → database,
CI → build → deploy):**

Identify the boundaries involved in the failing path. Inspect logs, state, and
configuration at those boundaries. When more evidence is needed, add focused
diagnostic instrumentation to answer a specific question about the failure:

- Trace relevant inputs and outputs
- Check environment/config propagation used by the failing operation
- Inspect state in the implicated components

Use the results to locate the fault and investigate that component. Add other
boundaries when the evidence points to them. Remove temporary instrumentation
introduced for the investigation when it is no longer needed.

### 5. Trace Data Flow

**WHEN error is deep in the call stack:**

- Where does the bad value originate?
- What called this function with the bad value?
- Keep tracing upstream until you find the source
- For a lasting fix, address the source of the failure

**Action:** Use `search_files` to trace references:

```python
# Find where the function is called
search_files("function_name(", path="src/", file_glob="*.py")

# Find where the variable is set
search_files("variable_name\\s*=", path="src/", file_glob="*.py")
```

### Phase 1 Completion Checklist

- [ ] Error messages fully read and understood
- [ ] Failure characterized through reproduction or observed evidence
- [ ] Recent changes identified and reviewed
- [ ] Evidence gathered (logs, state, data flow)
- [ ] Problem isolated to specific component/code
- [ ] Root cause hypothesis formed

Use the evidence to form and test a hypothesis. Identify gaps that affect the next diagnostic step without treating an untested explanation as an established cause.

---

## Phase 2: Pattern Analysis

**Find the pattern before fixing:**

### 1. Find Working Examples

- Locate similar working code in the same codebase
- What works that's similar to what's broken?

**Action:** Use `search_files` to find comparable patterns:

```python
search_files("similar_pattern", path="src/", file_glob="*.py")
```

### 2. Compare Against References

- Read the reference sections that define the relevant behavior, interfaces,
  and assumptions.
- Follow dependencies when needed to understand how the pattern applies to the
  failing code.

### 3. Identify Differences

- What's different between working and broken?
- Identify differences that could explain the observed failure.
- Test uncertain differences against the hypothesis before dismissing them.

### 4. Understand Dependencies

- What other components does this need?
- What settings, config, environment?
- What assumptions does it make?

---

## Phase 3: Hypothesis and Testing

**Scientific method:**

### 1. Form a Single Hypothesis

- State clearly: "I think X is the root cause because Y"
- Write it down
- Be specific, not vague

### 2. Test Minimally

- Make the SMALLEST possible change to test the hypothesis
- One variable at a time
- Don't fix multiple things at once

### 3. Verify Before Continuing

- Did it work? → Phase 4
- Didn't work? → Form NEW hypothesis
- DON'T add more fixes on top

### 4. When You Don't Know

- State what remains uncertain
- Identify the missing information needed to evaluate the hypothesis
- Retrieve it with available tools, or ask for information only the user can provide
- Use the task-completion guidance to decide whether further investigation is warranted

---

## Phase 4: Implementation

Implement the remedy supported by the evidence and the requested outcome.

### 1. Choose the Verification

- Establish the failing behavior and the result that would demonstrate the remedy worked
- Use an automated regression test when practical and consistent with repository requirements
- Otherwise use a focused reproduction, health check, or manual verification suited to the fault
- Apply the `test-driven-development` skill when the work involves automated test coverage

### 2. Implement Single Fix

- Address the identified cause for a lasting fix, or the recovery condition for an authorized mitigation
- ONE change at a time
- No "while I'm here" improvements
- No bundled refactoring

### 3. Verify Fix

Run the chosen checks and report the observed result. For a recovery action, distinguish restored service from a verified root-cause fix. Example commands for automated regression tests:

```bash
# Run the specific regression test
pytest tests/test_module.py::test_regression -v

# Run broader checks when shared behavior or repository requirements call for them
pytest tests/ -q
```

### 4. If the Fix Does Not Work

- Compare the result with the hypothesis and identify what remains unexplained.
- Revisit the relevant investigation steps before choosing the next change.
- Use the task-completion guidance to decide whether further work is warranted.

### 5. When Evidence Points to an Architectural Problem

**Pattern indicating an architectural problem:**
- Each fix reveals new shared state/coupling in a different place
- Fixes require "massive refactoring" to implement
- Each fix creates new symptoms elsewhere

Consider the implicated design:
- Is this pattern fundamentally sound?
- Are we "sticking with it through sheer inertia"?
- Should we refactor the architecture vs. continue fixing symptoms?

If the needed architectural change exceeds the task's existing authorization,
explain the evidence and proposed scope to the user. A failed hypothesis or
number of failed attempts alone does not establish an architectural defect.

---

## Revisit the Evidence When Needed

Reconsider the relevant evidence or hypothesis when:
- A proposed change has no explanation of how it addresses the observed failure
- Several simultaneous changes make it unclear which one affected the result
- An unsuccessful change is repeated without a revised hypothesis
- The observed result contradicts the proposed cause or claimed recovery

When failures indicate shared-state or coupling problems, evaluate the relevant
design as described in Phase 4 step 5.

## Applying the Workflow

| Situation | Approach |
|-----------|----------|
| Simple fault with a clear cause | Make the focused correction and verify the affected behavior. |
| User requests temporary recovery | Perform the authorized recovery, verify it, and report remaining uncertainty. |
| Automated reproduction is impractical | Use a focused observation or manual check that can establish the requested result. |
| Reference implementation is large | Read the relevant interfaces, assumptions, and dependencies. |
| A fix attempt fails | Identify what the next attempt would test; investigate architecture when the evidence points there. |

## Quick Reference

| Phase | Key Activities | Success Criteria |
|-------|---------------|------------------|
| **1. Root Cause** | Read errors, reproduce, check changes, gather evidence, trace data flow | Failure characterized; testable hypothesis formed |
| **2. Pattern** | Find working examples, compare, identify differences | Know what's different |
| **3. Hypothesis** | Form theory, test minimally, one variable at a time | Confirmed or new hypothesis |
| **4. Implementation** | Apply the scoped remedy and chosen verification | Requested outcome established by relevant checks |

## Hermes Agent Integration

### Investigation Tools

Use these Hermes tools during Phase 1:

- **`search_files`** — Find error strings, trace function calls, locate patterns
- **`read_file`** — Read source code with line numbers for precise analysis
- **`terminal`** — Run tests, check git history, reproduce bugs
- **`browser_navigate`/`browser_snapshot`** — Research error messages and read library docs

### With delegate_task

For complex multi-component debugging, dispatch investigation subagents:

```python
delegate_task(
    goal="Investigate why [specific test/behavior] fails",
    context="""
    Follow systematic-debugging skill:
    1. Read the error message carefully
    2. Reproduce the issue
    3. Trace the data flow to find root cause
    4. Report findings — do NOT fix yet

    Error: [paste full error]
    File: [path to failing code]
    Test command: [exact command]
    """,
    toolsets=['terminal', 'file']
)
```

### With test-driven-development

When implementing a lasting code fix with automated regression coverage:
1. Write a test that reproduces the bug (RED)
2. Debug systematically to find root cause
3. Fix the root cause (GREEN)
4. The test proves the fix and prevents regression

Use focused reproduction or manual verification when automated coverage is impractical, and report what was checked.

## Completion

Finish when the requested diagnosis, fix, or recovery is delivered and necessary verification is complete. Report unresolved causes or limitations accurately. Use the task-completion guidance to decide whether further work is warranted.

---
name: test-driven-development
description: "Test-first changes and regression tests for existing code."
version: 1.1.0
author: Hermes Agent (adapted from obra/superpowers)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [testing, tdd, development, quality, red-green-refactor]
    related_skills: [systematic-debugging, plan, subagent-driven-development]
---

# Test-Driven Development (TDD)

## Overview

For new behavior, write the test first, verify the expected failure, and write
minimal code to pass. For existing behavior, add tests that check its contract.

**Core principle:** Tests should demonstrate the requested behavior and detect
the failures they are intended to prevent.

## When to Use

Use this workflow for:
- New features
- Bug fixes
- Refactoring
- Behavior changes
- Regression or characterization tests for existing code

Adapt verification for prototypes, generated code, and configuration changes
to the task and repository conventions.

## New and Existing Implementations

For a new feature or an unfixed bug, establish the expected failing behavior
before changing the implementation.

When adding tests to existing code, user-provided code, or an implementation
already written during this task, inspect the behavior and add focused tests.
A characterization or regression test can legitimately pass on its first run.
Check its assertions against the intended behavior. Do not delete or rewrite
an implementation solely because tests were written afterward or passed
immediately; change implementation code when the tests reveal a defect or the
user's task requires a behavior change.

## Red-Green-Refactor Cycle

### RED — Write Failing Test

Write one minimal test showing what should happen.

**Good test:**
```python
def test_retries_failed_operations_3_times():
    attempts = 0
    def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Exception('fail')
        return 'success'

    result = retry_operation(operation)

    assert result == 'success'
    assert attempts == 3
```
Clear name, tests real behavior, one thing.

**Bad test:**
```python
def test_retry_works():
    mock = MagicMock()
    mock.side_effect = [Exception(), Exception(), 'success']
    result = retry_operation(mock)
    assert result == 'success'  # What about retry count? Timing?
```
Vague name, tests mock not real code.

**Requirements:**
- One behavior per test
- Clear descriptive name ("and" in name? Split it)
- Real code, not mocks (unless truly unavoidable)
- Name describes behavior, not implementation

### Verify RED — Watch It Fail

For new behavior or an unfixed bug, run the test before changing the implementation.

```bash
# Use terminal tool to run the specific test
pytest tests/test_feature.py::test_specific_behavior -v
```

Confirm:
- Test fails (not errors from typos)
- Failure message is expected
- Fails because the feature is missing

**Test passes immediately?** Determine whether the intended behavior already
exists. That is valid when adding coverage for existing code. For an unfixed bug
or missing feature, check that the test exercises the intended case.

**Test errors?** Fix the error, re-run until it fails correctly.

### GREEN — Minimal Code

Write the simplest code to pass the test. Nothing more.

**Good:**
```python
def add(a, b):
    return a + b  # Nothing extra
```

**Bad:**
```python
def add(a, b):
    result = a + b
    logging.info(f"Adding {a} + {b} = {result}")  # Extra!
    return result
```

Don't add features, refactor other code, or "improve" beyond the test.

**Cheating is OK in GREEN:**
- Hardcode return values
- Copy-paste
- Duplicate code
- Skip edge cases

We'll fix it in REFACTOR.

### Verify GREEN — Watch It Pass

**MANDATORY.**

```bash
# Run the specific test
pytest tests/test_feature.py::test_specific_behavior -v

# Broader checks when the change affects shared behavior or the repo requires them
pytest tests/ -q
```

Confirm:
- Test passes
- Relevant regression checks pass
- New failures or warnings introduced by the change are investigated

**Test fails?** Fix the code, not the test.

**Other tests fail?** Distinguish regressions from pre-existing failures and
address regressions within the task's scope.

### REFACTOR — Clean Up

After green, refactor when it improves the implementation needed for this task:
- Remove duplication
- Improve names
- Extract helpers
- Simplify expressions

Keep tests green throughout. Don't add behavior.

**If tests fail during refactor:** Identify the responsible change and correct
or revert that change while preserving unrelated work.

### Repeat

Next failing test for next behavior. One cycle at a time.

## Why Order Matters

**"I'll write tests after to verify it works"**

Tests written after code can mirror the implementation instead of the intended
behavior. Derive assertions from requirements and observable results. Test-first
development helps demonstrate a missing behavior before implementing it; tests
added afterward can still provide regression coverage for existing code.

**"I already manually tested all the edge cases"**

Manual testing is ad-hoc. You think you tested everything but:
- No record of what you tested
- Can't re-run when code changes
- Easy to forget cases under pressure
- "It worked when I tried it" ≠ comprehensive

Automated tests are systematic. They run the same way every time.

## Test Quality Checks

- If a test fails for an unrelated setup error, correct the setup so it exercises
  the intended behavior.
- If a test only repeats implementation details, revise its assertions around
  observable behavior.
- If the implementation already exists, add coverage without recreating it.
- If a test exposes a defect, make the focused correction and verify it.

## Verification Checklist

Before marking work complete:

- [ ] Tests cover the new or changed behavior and the requested regression cases
- [ ] For new behavior or unfixed bugs, the test demonstrated the expected failure
- [ ] For existing behavior, assertions check the intended contract
- [ ] Implementation changes are limited to what the task requires
- [ ] Relevant tests pass; unresolved failures or warnings are reported
- [ ] Tests use real code (mocks only if unavoidable)
- [ ] Edge cases and errors covered

Address applicable gaps in coverage or verification without restarting the
implementation solely to reproduce a test-first sequence.

## When Stuck

| Problem | Solution |
|---------|----------|
| Don't know how to test | Write the wished-for API. Write the assertion first. Ask the user. |
| Test too complicated | Check whether a smaller behavior or an existing test fixture can exercise the case. |
| Must mock everything | Identify the external interactions the test needs to isolate before changing the design. |
| Test setup huge | Use relevant fixtures or helpers; change production interfaces only when the task calls for it. |

## Hermes Agent Integration

### Running Tests

Use the `terminal` tool to run the applicable tests:

```python
# RED — verify failure
terminal("pytest tests/test_feature.py::test_name -v")

# GREEN — verify pass
terminal("pytest tests/test_feature.py::test_name -v")

# Broader suite when warranted by the change or repository requirements
terminal("pytest tests/ -q")
```

### With delegate_task

When dispatching subagents for implementation, include the relevant TDD steps
and the task's scope:

```python
delegate_task(
    goal="Implement [feature] using test-first development",
    context="""
    Follow test-driven-development skill:
    1. Write failing test FIRST
    2. Run test to verify it fails
    3. Write minimal code to pass
    4. Run test to verify it passes
    5. Refactor if needed
    6. Report changes and verification results

    Task scope: [requested behavior and files]
    For existing implementations or test-only tasks, preserve the implementation
    and add the requested coverage. Passing tests do not authorize a commit.

    Project test command: pytest tests/ -q
    Project structure: [describe relevant files]
    """,
    toolsets=['terminal', 'file']
)
```

### With systematic-debugging

Bug found? Write failing test reproducing it. Follow TDD cycle. The test proves the fix and prevents regression.

Use an automated regression test when practical; otherwise verify with a focused
reproduction and report the validation performed.

## Testing Anti-Patterns

- **Testing mock behavior instead of real behavior** — mocks should verify interactions, not replace the system under test
- **Testing implementation details** — test behavior/results, not internal method calls
- **Happy path only** — always test edge cases, errors, and boundaries
- **Brittle tests** — tests should verify behavior, not structure; refactoring shouldn't break them

## Completion

Finish when the requested behavior or coverage is implemented and necessary
verification is complete. Use the task-completion guidance to decide whether
further work is warranted.

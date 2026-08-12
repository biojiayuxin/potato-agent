from __future__ import annotations

from pathlib import Path


LITE_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_BUNDLED_SKILLS = {
    "productivity/ocr-and-documents",
    "software-development/hermes-agent-skill-authoring",
    "software-development/node-inspect-debugger",
    "software-development/python-debugpy",
    "software-development/requesting-code-review",
    "software-development/simplify-code",
    "software-development/spike",
    "software-development/systematic-debugging",
    "software-development/test-driven-development",
}

EXPECTED_OPTIONAL_SKILLS = {
    "research/bioinformatics",
    "research/duckduckgo-search",
    "research/searxng-search",
    "software-development/code-wiki",
    "software-development/rest-graphql-debug",
    "software-development/subagent-driven-development",
}


def test_bundled_skills_match_potato_allowlist() -> None:
    skills_dir = LITE_ROOT / "skills"
    actual = {
        skill_md.parent.relative_to(skills_dir).as_posix()
        for skill_md in skills_dir.rglob("SKILL.md")
    }

    assert actual == EXPECTED_BUNDLED_SKILLS


def test_optional_skills_match_potato_allowlist() -> None:
    skills_dir = LITE_ROOT / "optional-skills"
    actual = {
        skill_md.parent.relative_to(skills_dir).as_posix()
        for skill_md in skills_dir.rglob("SKILL.md")
    }

    assert actual == EXPECTED_OPTIONAL_SKILLS

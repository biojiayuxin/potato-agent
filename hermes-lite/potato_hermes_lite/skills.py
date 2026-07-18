"""Skill catalog helpers without banner or update-check side effects."""

from __future__ import annotations


def available_skills() -> dict[str, list[str]]:
    try:
        from tools.skills_tool import _find_all_skills

        skills = _find_all_skills()
    except Exception:
        return {}
    grouped: dict[str, list[str]] = {}
    for skill in skills:
        category = str(skill.get("category") or "general")
        grouped.setdefault(category, []).append(str(skill["name"]))
    return grouped

"""Skill runtime for the loom agent.

Public surface:
  loom.skills.discovery    scan project-local + user-global skill dirs
  loom.skills.registry     Skill / SkillIndex dataclasses
  loom.skills.loader       parse SKILL.md (markdown format)

Per Q2 (docs/harness-roadmap.md::10. Decisions):
  Both project-local .minicode/skills/ and user-global ~/.minicode/skills/
  are supported. Project-local wins on conflict (Python-import style).

A skill is a directory containing SKILL.md with this layout:

  # skill-name

  Short description (one paragraph).

  ## Triggers

  - phrase the agent hears
  - another trigger

  ## Steps

  1. Step one
  2. Step two

  ## Notes

  - Optional reference notes
"""

from loom.skills.discovery import discover_skills
from loom.skills.registry import Skill, SkillIndex, build_skill_index, parse_skill_md

__all__ = [
    "Skill",
    "SkillIndex",
    "build_skill_index",
    "discover_skills",
    "parse_skill_md",
]

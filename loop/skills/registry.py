from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loop.skills.discovery import list_skill_dirs

_TRIGGER_LINE = re.compile(r"^\s*-\s+(.+?)\s*$")


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    body: str = ""
    directory: Path | None = None
    skill_md: Path | None = None

    @property
    def has_body(self) -> bool:
        return bool(self.body.strip())


@dataclass
class SkillIndex:
    skills: dict[str, Skill] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.skills)

    def __len__(self) -> int:
        return len(self.skills)

    def names(self) -> list[str]:
        return sorted(self.skills.keys())

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def list_for_prompt(self) -> str:
        if not self.skills:
            return ""
        lines = ["# Available Skills (load with `load_skill <name>`)"]
        for name in self.names():
            skill = self.skills[name]
            trigger_str = (
                f" — triggers: {', '.join(skill.triggers)}" if skill.triggers else ""
            )
            lines.append(f"- **{skill.name}**: {skill.description}{trigger_str}")
        return "\n".join(lines)

    def body(self, name: str) -> str | None:
        skill = self.skills.get(name)
        return skill.body if skill else None


def parse_skill_md(skill_md: Path) -> Skill | None:
    """Parse a SKILL.md file (markdown format).

    Format:
      # skill-name
      <description paragraphs>

      ## Triggers
      - trigger 1
      - trigger 2

      <body sections>

    The directory is taken as the parent of SKILL.md.
    """
    if not skill_md.is_file():
        return None
    raw = skill_md.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not lines:
        return None

    name = None
    i = 0
    if lines[0].startswith("# "):
        name = lines[0][2:].strip()
        i = 1
    if not name:
        return None

    description_lines: list[str] = []
    triggers: list[str] = []
    body_lines: list[str] = []
    state = "description"

    while i < len(lines):
        line = lines[i]
        if line.startswith("## Triggers"):
            state = "triggers"
            i += 1
            continue
        if line.startswith("## "):
            state = "body"
        if state == "description":
            if line.strip():
                description_lines.append(line.strip())
        elif state == "triggers":
            m = _TRIGGER_LINE.match(line)
            if m:
                triggers.append(m.group(1).strip())
        elif state == "body":
            body_lines.append(line)
        i += 1

    description = " ".join(description_lines)
    body = "\n".join(body_lines).strip()

    return Skill(
        name=name,
        description=description,
        triggers=triggers,
        body=body,
        directory=skill_md.parent,
        skill_md=skill_md,
    )


def build_skill_index(workdir: Path) -> SkillIndex:
    """Scan both project-local and user-global skill directories.

    Per Q2: project-local wins on conflict.
    """
    index = SkillIndex()
    for directory in list_skill_dirs(workdir):
        if not directory.exists():
            continue
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            skill = parse_skill_md(skill_md)
            if skill is not None:
                index.skills[skill.name] = skill
    return index

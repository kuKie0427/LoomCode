from __future__ import annotations

from pathlib import Path

PROJECT_SKILLS_SUBDIR = ".minicode/skills"


def user_global_skills_dir() -> Path:
    return Path.home() / ".minicode" / "skills"


def list_skill_dirs(workdir: Path) -> list[Path]:
    return [
        user_global_skills_dir(),
        workdir / PROJECT_SKILLS_SUBDIR,
    ]


def discover_skills(workdir: Path):
    from loop.skills.registry import build_skill_index
    return build_skill_index(workdir)

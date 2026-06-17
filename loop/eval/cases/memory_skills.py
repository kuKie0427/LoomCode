from loop.eval.runner import EvalCase, EvalResult


class MemoryStoreRoundtrip(EvalCase):
    name = "memory-store-roundtrip"
    description = "MemoryStore append + read returns the same content"

    def run(self) -> EvalResult:
        import shutil

        from loop.eval._util import make_empty_workdir
        from loop.memory import MemoryStore
        wd = make_empty_workdir("memory-roundtrip")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(wd)
        store.append("User prefers tabs over spaces.")
        store.append("Project: loop test consumer.")
        text = store.read()
        if "tabs over spaces" not in text:
            return EvalResult(name=self.name, passed=False, detail="entry 1 missing")
        if "loop test consumer" not in text:
            return EvalResult(name=self.name, passed=False, detail="entry 2 missing")
        return EvalResult(name=self.name, passed=True, detail="both entries persisted")


class MemoryStoreEnforcesByteCap(EvalCase):
    name = "memory-store-enforces-byte-cap"
    description = "MemoryStore raises ValueError on append exceeding 25 KB"

    def run(self) -> EvalResult:
        from loop.eval._util import make_empty_workdir
        from loop.memory import MemoryStore
        wd = make_empty_workdir("memory-cap")
        store = MemoryStore(wd)
        big = "x" * (26 * 1024)
        try:
            store.append(big)
        except ValueError as exc:
            if "bytes" not in str(exc).lower():
                return EvalResult(name=self.name, passed=False, detail=f"wrong exception: {exc}")
            return EvalResult(name=self.name, passed=True, detail="byte cap enforced")
        return EvalResult(name=self.name, passed=False, detail="no exception raised")


class MemoryQ3OwnProjectTrue(EvalCase):
    name = "memory-q3-own-project-true"
    description = "is_own_project returns True for memory under active workdir"

    def run(self) -> EvalResult:
        from loop.eval._util import make_empty_workdir
        wd = make_empty_workdir("memory-q3-own")
        from loop.memory.paths import is_own_project, memory_file
        mem = memory_file(wd)
        if not is_own_project(mem, workdir=wd):
            return EvalResult(name=self.name, passed=False, detail="expected True")
        return EvalResult(name=self.name, passed=True, detail="own=True")


class MemoryQ3ForeignProjectFalse(EvalCase):
    name = "memory-q3-foreign-project-false"
    description = "is_own_project returns False for memory outside workdir"

    def run(self) -> EvalResult:
        from loop.eval._util import make_empty_workdir
        wd = make_empty_workdir("memory-q3-foreign")
        from loop.memory.paths import is_own_project
        foreign = wd.parent / "totally_different_project" / ".minicode" / "memory" / "MEMORY.md"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("foreign memory")
        if is_own_project(foreign, workdir=wd):
            return EvalResult(name=self.name, passed=False, detail="expected False")
        return EvalResult(name=self.name, passed=True, detail="foreign=False")


class SkillsProjectOverridesUser(EvalCase):
    name = "skills-project-overrides-user"
    description = "Per Q2: project-local skill wins over user-global"

    def run(self) -> EvalResult:

        from loop.eval._util import make_empty_workdir
        wd = make_empty_workdir("skills-q2")
        (wd / ".minicode" / "skills" / "shared").mkdir(parents=True, exist_ok=True)
        (wd / ".minicode" / "skills" / "shared" / "SKILL.md").write_text(
            "# shared\n\nFrom project-local.\n",
            encoding="utf-8",
        )
        user_dir = wd / "fake_home" / ".minicode" / "skills" / "shared"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "SKILL.md").write_text(
            "# shared\n\nFrom user-global.\n",
            encoding="utf-8",
        )

        import loop.skills.discovery as d
        from loop.skills.registry import build_skill_index
        original_home = d.user_global_skills_dir

        def mock_home():
            return wd / "fake_home" / ".minicode" / "skills"

        d.user_global_skills_dir = mock_home
        try:
            idx = build_skill_index(wd)
        finally:
            d.user_global_skills_dir = original_home

        skill = idx.get("shared")
        if skill is None or "project-local" not in skill.description:
            return EvalResult(name=self.name, passed=False, detail=f"description={skill.description if skill else None}")
        return EvalResult(name=self.name, passed=True, detail="project-local wins")


class SkillsBodyLoad(EvalCase):
    name = "skills-body-load"
    description = "Skill.body contains the markdown body after the metadata sections"

    def run(self) -> EvalResult:
        from loop.eval._util import make_empty_workdir
        wd = make_empty_workdir("skills-body")
        skill_dir = wd / ".minicode" / "skills" / "demo"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "# demo\n\nDescription here.\n\n## Triggers\n\n- trigger one\n\n## Steps\n\n1. First step\n",
            encoding="utf-8",
        )
        from loop.skills.registry import build_skill_index
        idx = build_skill_index(wd)
        skill = idx.get("demo")
        if skill is None:
            return EvalResult(name=self.name, passed=False, detail="demo not found")
        if "First step" not in skill.body:
            return EvalResult(name=self.name, passed=False, detail=f"body missing step: {skill.body!r}")
        if "trigger one" not in skill.triggers:
            return EvalResult(name=self.name, passed=False, detail=f"trigger missing: {skill.triggers}")
        return EvalResult(name=self.name, passed=True, detail=f"triggers={skill.triggers}")


class SkillsEmptyIndexReturnsEmpty(EvalCase):
    name = "skills-empty-index-returns-empty"
    description = "SkillIndex on an empty workdir has no skills"

    def run(self) -> EvalResult:
        from loop.eval._util import make_empty_workdir
        wd = make_empty_workdir("skills-empty")
        from loop.skills.registry import build_skill_index
        idx = build_skill_index(wd)
        if idx.names():
            return EvalResult(name=self.name, passed=False, detail=f"unexpected skills: {idx.names()}")
        if idx.list_for_prompt() != "":
            return EvalResult(name=self.name, passed=False, detail="non-empty prompt string")
        return EvalResult(name=self.name, passed=True, detail="empty index")



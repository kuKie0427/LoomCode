from loop.eval._util import EXPECTED_HARNESS_FILES, run_loop_cli
from loop.eval.runner import EvalCase, EvalResult


class InitSmokeEmptyDir(EvalCase):
    name = "init-smoke-empty-dir"
    description = "loop init creates the 5-file minimum in an empty directory"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", target_name="init-smoke-empty")
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r.returncode}: {r.stderr[:200]}")
        assert r.workdir is not None
        missing = [n for n in EXPECTED_HARNESS_FILES if not (r.workdir / n).exists()]
        if missing:
            return EvalResult(name=self.name, passed=False, detail=f"missing: {missing}")
        return EvalResult(name=self.name, passed=True, detail=f"all {len(EXPECTED_HARNESS_FILES)} files present")


class InitScaffoldsPythonProject(EvalCase):
    name = "init-scaffolds-python-project"
    description = "loop init in a Python project uses pytest + python -m compileall"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", setup="pyproject.toml", target_name="init-py-scaffold")
        assert r.workdir is not None
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r.returncode}: {r.stderr[:200]}")
        init_sh = (r.workdir / "init.sh").read_text()
        if "pytest" not in init_sh:
            return EvalResult(name=self.name, passed=False, detail="init.sh missing pytest")
        if "compileall" not in init_sh:
            return EvalResult(name=self.name, passed=False, detail="init.sh missing compileall")
        return EvalResult(name=self.name, passed=True)


class InitSkipsExisting(EvalCase):
    name = "init-skips-existing"
    description = "loop init preserves pre-existing files when force is not set"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", target_name="init-skip-existing")
        assert r.workdir is not None
        existing = r.workdir / "AGENTS.md"
        existing.write_text("# EXISTING\n", encoding="utf-8")
        r2 = run_loop_cli("init", existing_workdir=str(r.workdir), target_name="init-skip-existing-2")
        if r2.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r2.returncode}: {r2.stderr[:200]}")
        if existing.read_text() != "# EXISTING\n":
            return EvalResult(name=self.name, passed=False, detail="AGENTS.md was overwritten without --force")
        return EvalResult(name=self.name, passed=True, detail="existing AGENTS.md preserved")


class InitForceOverwrites(EvalCase):
    name = "init-force-overwrites"
    description = "loop init --force overwrites existing files"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", target_name="init-force")
        assert r.workdir is not None
        (r.workdir / "AGENTS.md").write_text("# OLD\n", encoding="utf-8")
        r2 = run_loop_cli(
            "init", "--force",
            existing_workdir=str(r.workdir),
            target_name="init-force-2",
        )
        if r2.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r2.returncode}: {r2.stderr[:200]}")
        new_content = (r.workdir / "AGENTS.md").read_text()
        if "OLD" in new_content:
            return EvalResult(name=self.name, passed=False, detail="AGENTS.md not overwritten")
        return EvalResult(name=self.name, passed=True, detail="AGENTS.md overwritten")


class InitWithAgentFileClaude(EvalCase):
    name = "init-with-agent-file-claude"
    description = "loop init --agent-file CLAUDE.md creates CLAUDE.md instead of AGENTS.md"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", "--agent-file", "CLAUDE.md", target_name="init-claude")
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r.returncode}: {r.stderr[:200]}")
        assert r.workdir is not None
        if not (r.workdir / "CLAUDE.md").exists():
            return EvalResult(name=self.name, passed=False, detail="CLAUDE.md missing")
        if (r.workdir / "AGENTS.md").exists():
            return EvalResult(name=self.name, passed=False, detail="AGENTS.md should not exist")
        return EvalResult(name=self.name, passed=True, detail="CLAUDE.md created")


class InitCustomCommands(EvalCase):
    name = "init-custom-commands"
    description = "loop init --commands overrides stack detection"

    def run(self) -> EvalResult:
        r = run_loop_cli("init", "--commands", "make test,make lint", target_name="init-custom-cmd")
        assert r.workdir is not None
        if r.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"exit {r.returncode}: {r.stderr[:200]}")
        init_sh = (r.workdir / "init.sh").read_text()
        if "make test" not in init_sh or "make lint" not in init_sh:
            return EvalResult(name=self.name, passed=False, detail="custom commands not in init.sh")
        return EvalResult(name=self.name, passed=True)
"""Harness eval cases for f-session-mutable-prompt-p1."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class SessionMutablePromptModuleExists(EvalCase):
    name = "session-mutable-prompt-module-exists"
    description = "loom.agent.system_prompt module exists with public API"

    def run(self) -> EvalResult:
        try:
            import loom.agent.system_prompt as sp
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("get_system_prompt", "invalidate_system_prompt", "mark_dirty", "build_fresh"):
            if not hasattr(sp, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="all 4 public functions present")


class SessionMutablePromptCacheInvalidates(EvalCase):
    name = "session-mutable-prompt-cache-invalidates"
    description = "invalidate_system_prompt clears the cache so the next call rebuilds"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import loom.agent.system_prompt as sp
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            (wd / "AGENTS.md").write_text("rules")
            sp.invalidate_system_prompt()
            sp.get_system_prompt(wd)
            with patch("loom.agent.system_prompt.build_fresh", return_value="FRESH") as m:
                sp.get_system_prompt(wd)
                assert m.call_count == 0, "cache should have returned old value"
                sp.invalidate_system_prompt()
                sp.get_system_prompt(wd)
                assert m.call_count == 1, "after invalidation, must rebuild"
        return EvalResult(name=self.name, passed=True, detail="cache invalidation works")


class SessionMutablePromptMemoryWriteTriggersInvalidate(EvalCase):
    name = "session-mutable-prompt-memory-write-triggers-invalidate"
    description = "run_memory_write tool invalidates the prompt cache so the agent sees the new entry"

    def run(self) -> EvalResult:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import loom.agent.system_prompt as sp
        import loom.agent.tools as tools_mod

        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp)
            memory_dir = wd / ".minicode" / "memory"
            memory_dir.mkdir(parents=True)
            (memory_dir / "MEMORY.md").write_text("seed")
            original_workdir = tools_mod.WORKDIR
            tools_mod.WORKDIR = wd
            try:
                sp.invalidate_system_prompt()
                sp.get_system_prompt(wd)
                with patch("loom.agent.system_prompt.build_fresh", return_value="REBUILT") as m:
                    tools_mod.run_memory_write("a new entry to memory")
                    sp.get_system_prompt(wd)
                    if m.call_count == 0:
                        return EvalResult(name=self.name, passed=False, detail="memory_write did not invalidate cache")
            finally:
                tools_mod.WORKDIR = original_workdir
        return EvalResult(name=self.name, passed=True, detail="memory_write invalidates prompt cache")

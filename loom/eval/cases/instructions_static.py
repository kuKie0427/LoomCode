"""Eval cases for Task 1: AGENTS.md → SystemPrompt.static.

Three cases exercise the real path: write AGENTS.md to a tmpdir, call
``build_system_prompt(tmpdir)``, and assert against ``sp.static`` /
the rendered prompt.

- ``instructions-agents-md-loaded-into-static`` — small AGENTS.md → in static
- ``instructions-large-agents-md-falls-back-to-tier2`` — large AGENTS.md → not in static, still in Tier 2
- ``instructions-no-agents-md-no-static-rules-section`` — no AGENTS.md → no static section
"""
from __future__ import annotations

from loom.eval._util import make_empty_workdir
from loom.eval.runner import EvalCase, EvalResult

SMALL_AGENTS_MD = "# Project\n\n" + ("A working-rule line. " * 16)  # ~200 chars


class InstructionsAgentsMdLoadedIntoStatic(EvalCase):
    name = "instructions-agents-md-loaded-into-static"
    description = "AGENTS.md (≤ AGENTS_MD_STATIC_LIMIT) → injected into SystemPrompt.static under 'Project Working Rules'"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("instr-small")
        (wd / "AGENTS.md").write_text(SMALL_AGENTS_MD, encoding="utf-8")

        from loom.agent.loop import build_system_prompt
        sp = build_system_prompt(wd)
        rendered_static = "".join(sp.static)

        if "Project Working Rules" not in rendered_static:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"'Project Working Rules' header not found in sp.static. static chunks: {len(sp.static)}",
            )
        if SMALL_AGENTS_MD.strip() not in rendered_static:
            return EvalResult(
                name=self.name, passed=False,
                detail="AGENTS.md body not present in sp.static (only header found)",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"AGENTS.md ({len(SMALL_AGENTS_MD)} chars) injected into static",
        )


class InstructionsLargeAgentsMdFallsBackToTier2(EvalCase):
    name = "instructions-large-agents-md-falls-back-to-tier2"
    description = "AGENTS.md (> AGENTS_MD_STATIC_LIMIT) → NOT in static; still reachable via Tier 2 loader"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("instr-large")
        large_md = "# Long AGENTS\n\n" + ("x" * 16000)
        (wd / "AGENTS.md").write_text(large_md, encoding="utf-8")

        from loom.agent.loop import build_system_prompt
        sp = build_system_prompt(wd)
        rendered_static = "".join(sp.static)

        if "Project Working Rules" in rendered_static:
            return EvalResult(
                name=self.name, passed=False,
                detail="large AGENTS.md should NOT be in static (>12000 chars)",
            )
        from loom.memory.context import load_tier2
        tier2 = load_tier2(wd)
        if "Long AGENTS" not in tier2:
            return EvalResult(
                name=self.name, passed=False,
                detail="Tier 2 fallback missing AGENTS.md content",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"AGENTS.md ({len(large_md)} chars) bypassed static; Tier 2 contains it ({len(tier2)} chars)",
        )


class InstructionsNoAgentsMdNoStaticRulesSection(EvalCase):
    name = "instructions-no-agents-md-no-static-rules-section"
    description = "No AGENTS.md → 'Project Working Rules' header absent from sp.static"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("instr-none")
        assert not (wd / "AGENTS.md").exists(), "precondition: workdir must not have AGENTS.md"

        from loom.agent.loop import build_system_prompt
        sp = build_system_prompt(wd)
        rendered_static = "".join(sp.static)

        if "Project Working Rules" in rendered_static:
            return EvalResult(
                name=self.name, passed=False,
                detail="'Project Working Rules' should not appear when no AGENTS.md exists",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="no AGENTS.md → no static-rules section (None case handled)",
        )
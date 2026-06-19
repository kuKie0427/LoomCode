"""Eval cases for Task 4: cold-start continuity (Tier 1.5).

Four cases write real ``progress.md`` and ``session-handoff.md`` files into a
tmpdir, then call ``build_system_prompt(tmpdir)`` and assert against the
rendered prompt's memory section:

- ``continuity-progress-md-tail-loaded`` — 200-line progress.md → last line in prompt, first line NOT
- ``continuity-session-handoff-loaded-when-present`` — substantive handoff → full text in prompt
- ``continuity-empty-handoff-template-skipped`` — empty-template handoff → NOT in prompt
- ``continuity-no-files-no-section`` — neither file → no ``Tier 1.5`` header
"""
from __future__ import annotations

from loom.eval._util import make_empty_workdir
from loom.eval.runner import EvalCase, EvalResult


def _render(sp) -> str:
    return sp.build()


class ContinuityProgressMdTailLoaded(EvalCase):
    name = "continuity-progress-md-tail-loaded"
    description = "200-line progress.md → last line injected into prompt; first line NOT (last-80-lines window)"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("cont-progress")
        first_line = "## FIRST_LINE_MARKER_XYZ progress begins"
        last_line = "## LAST_LINE_MARKER_XYZ progress ends here"

        lines = [f"line {i:03d}" for i in range(200)]
        lines[0] = first_line
        lines[-1] = last_line
        progress_text = "\n".join(lines) + "\n"
        (wd / "progress.md").write_text(progress_text, encoding="utf-8")

        from loom.agent.loop import build_system_prompt
        sp = build_system_prompt(wd)
        rendered = _render(sp)

        if last_line not in rendered:
            return EvalResult(
                name=self.name, passed=False,
                detail="last line of progress.md not found in rendered prompt",
            )
        if first_line in rendered:
            return EvalResult(
                name=self.name, passed=False,
                detail="first line of progress.md should NOT be in rendered prompt (last-80-lines window)",
            )
        if "Tier 1.5" not in rendered:
            return EvalResult(
                name=self.name, passed=False,
                detail="'Tier 1.5' section header not in prompt",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="last line injected, first line excluded (window: last 80 of 200)",
        )


class ContinuitySessionHandoffLoadedWhenPresent(EvalCase):
    name = "continuity-session-handoff-loaded-when-present"
    description = "Substantive session-handoff.md → full text injected into Tier 1.5"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("cont-handoff")
        handoff_text = "PICKUP_TOKEN_HANDOFF_XYZ: Resuming the auth module from where we left off — refactored the JWT validator, blocked on refresh-token rotation test."
        (wd / "session-handoff.md").write_text("# Handoff\n\n" + handoff_text, encoding="utf-8")

        from loom.agent.loop import build_system_prompt
        sp = build_system_prompt(wd)
        rendered = _render(sp)

        if handoff_text not in rendered:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"handoff text not in prompt; substring len={len(handoff_text)}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="substantive handoff injected in full into Tier 1.5",
        )


class ContinuityEmptyHandoffTemplateSkipped(EvalCase):
    name = "continuity-empty-handoff-template-skipped"
    description = "session-handoff.md containing only headers + empty bullets → NOT injected (substantive threshold)"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("cont-empty-handoff")
        empty_template = (
            "# Session Handoff\n\n"
            "## Last task\n\n"
            "- \n\n"
            "## Next steps\n\n"
            "- \n\n"
            "## Blockers\n\n"
            "- \n\n"
        )
        (wd / "session-handoff.md").write_text(empty_template, encoding="utf-8")

        from loom.memory.context import load_session_continuity
        result = load_session_continuity(wd)

        if result and "PICKUP_TOKEN_HANDOFF_XYZ" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"empty template should yield '' (got {len(result)} chars)",
            )
        if result != "":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"empty template should yield '' (got {result[:120]!r})",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="empty-template handoff correctly skipped (substantive threshold not met)",
        )


class ContinuityNoFilesNoSection(EvalCase):
    name = "continuity-no-files-no-section"
    description = "Neither progress.md nor session-handoff.md → no 'Tier 1.5' header in prompt"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("cont-none")
        assert not (wd / "progress.md").exists(), "precondition"
        assert not (wd / "session-handoff.md").exists(), "precondition"

        from loom.memory.context import load_session_continuity
        result = load_session_continuity(wd)
        if result != "":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected '' with no files, got {result[:120]!r}",
            )

        from loom.agent.loop import build_system_prompt
        sp = build_system_prompt(wd)
        rendered = _render(sp)
        if "Tier 1.5" in rendered:
            return EvalResult(
                name=self.name, passed=False,
                detail="'Tier 1.5' header should NOT appear when neither file exists",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="no files → no Tier 1.5 section (clean cold-start)",
        )
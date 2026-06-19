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
    description = "session-handoff.md with only whitespace / no body content → NOT injected (substantive threshold)"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("cont-empty-handoff")
        # Truly empty: just whitespace. The previous template-based test was
        # over-defensive (template heading text counted as body). Now we
        # require actual content above the 30-char threshold.
        (wd / "session-handoff.md").write_text("   \n\n  \n", encoding="utf-8")

        from loom.memory.context import load_session_continuity
        result = load_session_continuity(wd)

        if result != "":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"empty handoff should yield '' (got {len(result)} chars: {result[:120]!r})",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="empty (whitespace-only) handoff correctly skipped",
        )


class ContinuityBulletListHandoffLoaded(EvalCase):
    name = "continuity-bullet-list-handoff-loaded"
    description = "session-handoff.md with substantive bullet content → IS injected (regression for _is_substantive regex over-match bug)"

    def run(self) -> EvalResult:
        wd = make_empty_workdir("cont-bullet-handoff")
        # A canonical bullet-list handoff — the format most humans use.
        # The original _is_substantive implementation over-matched bullet
        # markers, dropping the content. This case pins the correct behavior.
        handoff = (
            "# Session Handoff\n\n"
            "## Last task\n"
            "- Refactored the JWT validator to use HS256\n"
            "- Fixed the refresh token rotation bug in auth.py\n\n"
            "## Next steps\n"
            "- Implement the session management middleware\n"
            "- Write end-to-end tests for the auth flow\n"
        )
        (wd / "session-handoff.md").write_text(handoff, encoding="utf-8")

        from loom.memory.context import load_session_continuity
        result = load_session_continuity(wd)

        if "JWT validator" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"bullet-list handoff should load content (got {result[:200]!r})",
            )
        if "session management middleware" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"bullet-list handoff should load next steps (got {result[:200]!r})",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="bullet-list handoff correctly loaded (substantive content preserved)",
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
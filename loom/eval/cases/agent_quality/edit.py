from __future__ import annotations

from loom.eval.agent_quality import (
    AgentQualityCase,
    AgentRunOutcome,
    file_contains,
    file_lacks,
)

CALC_PY = (
    "def add(a, b):\n    return a + b\n\n"
    "def subtract(a, b):\n    return a - b\n\n"
    "def multiply(a, b):\n    return a * b\n\n"
    "def divide(a, b):\n    return a / b\n"
)


class AQEditChangeConstant(AgentQualityCase):
    name = "aq-edit-change-constant"
    description = "Change a single constant value at a known location."
    files = {
        "config.py": "TIMEOUT_SECONDS = 30\nMAX_RETRIES = 3\nDEBUG = False\n",
    }
    user_prompt = "Edit config.py: change TIMEOUT_SECONDS from 30 to 60. Leave the other lines alone."

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        ok, detail = file_contains(outcome, "config.py", "TIMEOUT_SECONDS = 60", "MAX_RETRIES = 3", "DEBUG = False")
        if not ok:
            return ok, detail
        return file_lacks(outcome, "config.py", "TIMEOUT_SECONDS = 30")


class AQEditAddFunction(AgentQualityCase):
    name = "aq-edit-add-function"
    description = "Append a new function definition to an existing file."
    files = {"calc.py": CALC_PY}
    user_prompt = (
        "Edit calc.py: add a new function `power(a, b)` that returns `a ** b`. "
        "Place it after the divide function. Don't change any other function."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        return file_contains(outcome, "calc.py", "def power(a, b)", "a ** b", "def divide(a, b)")


class AQEditFixDivideByZero(AgentQualityCase):
    name = "aq-edit-fix-divide-by-zero"
    description = "Add a guard against b==0 in calc.py's divide function."
    files = {"calc.py": CALC_PY}
    user_prompt = (
        "Edit calc.py: in the divide function, if b is 0, raise ValueError('cannot divide by zero') "
        "instead of dividing. Don't change other functions."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        return file_contains(
            outcome, "calc.py",
            "def divide(a, b)",
            "ValueError",
            "cannot divide by zero",
        )


class AQEditMultiEditAtomically(AgentQualityCase):
    name = "aq-edit-multi-edit-atomically"
    description = "Apply three non-overlapping edits to one file (multi_edit-requiring)."
    files = {
        "shapes.py": (
            "def area_rect(w, h):\n    return w * h\n\n"
            "def area_circle(r):\n    return 3.14 * r * r\n\n"
            "def area_triangle(b, h):\n    return 0.5 * b * h\n"
        ),
    }
    user_prompt = (
        "shapes.py has three area functions. Make THREE non-overlapping edits: "
        "(1) change area_rect to add a docstring `'''Return rectangle area.'''` as the first line of the body, "
        "(2) change area_circle to use 3.14159 instead of 3.14, "
        "(3) change area_triangle to use `base` instead of `b` as the parameter name. "
        "All three edits must apply, and no other lines should change."
    )
    timeout_s = 180

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        content = outcome.files_after.get("shapes.py", "")
        for needle in (
            "def area_rect(w, h):",
            "Return rectangle area.",
            "3.14159 * r * r",
            "def area_triangle(base, h):",
            "0.5 * base * h",
        ):
            if needle not in content:
                return False, f"shapes.py missing {needle!r}"
        if "0.5 * b * h" in content:
            return False, "area_triangle still references the old parameter name `b`"
        if "3.14 * r * r" in content:
            return False, "area_circle still uses 3.14 (should be 3.14159)"
        return True, "all three edits applied atomically"


class AQEditFuzzyWhitespaceMismatch(AgentQualityCase):
    name = "aq-edit-fuzzy-whitespace-mismatch"
    description = "Edit where old_text has different whitespace than file (fuzzy-requiring)."
    files = {
        "config.py": (
            "DATABASE_URL = 'postgres://localhost'\n"
            "DEBUG = True\n"
            "SECRET_KEY = 'abc'\n"
            "PORT = 8000\n"
        ),
    }
    user_prompt = (
        "config.py has a SECRET_KEY. Change its value to 'new-secret-value-2026'. "
        "Be careful: the existing line uses TAB indentation in some places; you should match "
        "the visible text. Use grep or read_file first to find the exact line if unsure."
    )
    timeout_s = 180

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        content = outcome.files_after.get("config.py", "")
        if "SECRET_KEY = 'new-secret-value-2026'" not in content:
            return False, "SECRET_KEY was not updated to the new value"
        if "'abc'" in content:
            return False, "old SECRET_KEY value 'abc' is still in the file"
        for preserved in (
            "DATABASE_URL = 'postgres://localhost'",
            "DEBUG = True",
            "PORT = 8000",
        ):
            if preserved not in content:
                return False, f"unrelated line modified or removed: {preserved!r}"
        return True, "SECRET_KEY updated, all other lines preserved"

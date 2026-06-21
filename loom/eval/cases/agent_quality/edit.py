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

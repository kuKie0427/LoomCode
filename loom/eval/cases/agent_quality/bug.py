from __future__ import annotations

from loom.eval.agent_quality import AgentQualityCase, AgentRunOutcome


class AQBugInvestigateKeyError(AgentQualityCase):
    name = "aq-bug-keyerror"
    description = "Diagnose a KeyError from a runtime error message and fix it."
    files = {
        "lookup.py": (
            "TABLE = {'a': 1, 'b': 2, 'c': 3}\n\n"
            "def get_value(key):\n"
            "    return TABLE[key]\n"
        ),
    }
    user_prompt = (
        "I'm calling lookup.get_value('d') and getting KeyError: 'd'. "
        "Fix the function in lookup.py so that missing keys return None instead of raising. "
        "Don't change the TABLE constant."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        content = outcome.files_after.get("lookup.py", "")
        if "TABLE = {'a': 1, 'b': 2, 'c': 3}" not in content:
            return False, "TABLE was modified or removed"
        unguarded = (
            "return TABLE[key]" in content
            and ".get(" not in content
            and "try" not in content
            and "in TABLE" not in content
        )
        if unguarded:
            return False, "function still does unguarded TABLE[key] lookup"
        try:
            ns: dict = {}
            exec(content, ns)
            fn = ns.get("get_value")
            if fn is None:
                return False, "get_value missing after edit"
            if fn("a") != 1:
                return False, f"get_value('a') = {fn('a')!r}, expected 1"
            if fn("d") is not None:
                return False, f"get_value('d') = {fn('d')!r}, expected None"
            return True, "missing-key path now returns None; valid keys still work"
        except Exception as exc:
            return False, f"running fixed impl raised {type(exc).__name__}: {exc}"


class AQBugInvestigateTypeError(AgentQualityCase):
    name = "aq-bug-typeerror"
    description = "Fix a TypeError caused by passing None to len()."
    files = {
        "count.py": (
            "def safe_len(items):\n"
            "    return len(items)\n"
        ),
    }
    user_prompt = (
        "safe_len(None) raises TypeError: object of type 'NoneType' has no len(). "
        "Fix count.py so safe_len(None) returns 0. Other inputs (lists, strings) "
        "should still return their real length."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        content = outcome.files_after.get("count.py", "")
        if "def safe_len(" not in content:
            return False, "safe_len missing after edit"
        try:
            ns: dict = {}
            exec(content, ns)
            fn = ns.get("safe_len")
            if fn is None:
                return False, "safe_len not defined after edit"
            if fn(None) != 0:
                return False, f"safe_len(None) = {fn(None)!r}, expected 0"
            if fn([1, 2, 3]) != 3:
                return False, f"safe_len([1,2,3]) = {fn([1, 2, 3])!r}, expected 3"
            if fn("hi") != 2:
                return False, f"safe_len('hi') = {fn('hi')!r}, expected 2"
            return True, "None → 0; list/str unchanged"
        except Exception as exc:
            return False, f"running fixed impl raised {type(exc).__name__}: {exc}"

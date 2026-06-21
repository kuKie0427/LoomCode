from __future__ import annotations

from loom.eval.agent_quality import AgentQualityCase, AgentRunOutcome


class AQTDDFixFailingAssertion(AgentQualityCase):
    name = "aq-tdd-fix-failing-assertion"
    description = "Read a failing test, locate the broken function, fix it."
    files = {
        "stringy.py": (
            "def reverse_words(sentence):\n"
            "    return sentence\n"
        ),
        "test_stringy.py": (
            "from stringy import reverse_words\n\n"
            "def test_reverse_words():\n"
            "    assert reverse_words('hello world') == 'world hello'\n"
            "    assert reverse_words('a b c') == 'c b a'\n"
        ),
    }
    user_prompt = (
        "test_stringy.py is failing. Read the test to understand the expected behavior, "
        "then fix the implementation in stringy.py. Don't modify the test."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        test_after = outcome.files_after.get("test_stringy.py", "")
        if "reverse_words('hello world') == 'world hello'" not in test_after:
            return False, "test file was modified — should be untouched"
        impl = outcome.files_after.get("stringy.py", "")
        try:
            ns: dict = {}
            exec(impl, ns)
            fn = ns.get("reverse_words")
            if fn is None:
                return False, "reverse_words missing after edit"
            if fn("hello world") != "world hello":
                return False, f"reverse_words('hello world') = {fn('hello world')!r}"
            if fn("a b c") != "c b a":
                return False, f"reverse_words('a b c') = {fn('a b c')!r}"
            return True, "both assertions now pass"
        except Exception as exc:
            return False, f"running fixed impl raised {type(exc).__name__}: {exc}"


class AQTDDImplementMissingFunction(AgentQualityCase):
    name = "aq-tdd-implement-missing-function"
    description = "Read a test that imports an undefined function and implement it."
    files = {
        "mathy.py": "def square(n):\n    return n * n\n",
        "test_mathy.py": (
            "from mathy import square, cube\n\n"
            "def test_square():\n"
            "    assert square(4) == 16\n\n"
            "def test_cube():\n"
            "    assert cube(2) == 8\n"
            "    assert cube(3) == 27\n"
        ),
    }
    user_prompt = (
        "test_mathy.py imports `cube` from mathy.py but cube is not implemented yet. "
        "Add the cube function to mathy.py so all tests pass. Don't modify the test or "
        "the existing square function."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        impl = outcome.files_after.get("mathy.py", "")
        if "def square(n)" not in impl or "return n * n" not in impl:
            return False, "existing square function was modified or removed"
        try:
            ns: dict = {}
            exec(impl, ns)
            cube = ns.get("cube")
            if cube is None:
                return False, "cube function not defined"
            if cube(2) != 8 or cube(3) != 27:
                return False, f"cube(2)={cube(2)!r}, cube(3)={cube(3)!r}"
            sq = ns.get("square")
            if sq is None or sq(4) != 16:
                return False, "square no longer works correctly"
            return True, "cube implemented; square preserved"
        except Exception as exc:
            return False, f"running fixed impl raised {type(exc).__name__}: {exc}"

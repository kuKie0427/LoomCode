from __future__ import annotations

from loom.eval.agent_quality import (
    AgentQualityCase,
    AgentRunOutcome,
    file_contains,
    file_lacks,
)


class AQSearchRenameSymbol(AgentQualityCase):
    name = "aq-search-rename-symbol"
    description = "Rename a function across two files."
    files = {
        "lib.py": "def old_name(x):\n    return x + 1\n",
        "main.py": "from lib import old_name\n\nprint(old_name(5))\n",
    }
    user_prompt = (
        "Rename the function `old_name` to `increment` everywhere in this project. "
        "Both lib.py (the definition) and main.py (the caller and the import) must be updated."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        ok, detail = file_contains(outcome, "lib.py", "def increment(x)")
        if not ok:
            return ok, detail
        ok, detail = file_lacks(outcome, "lib.py", "old_name")
        if not ok:
            return ok, detail
        ok, detail = file_contains(outcome, "main.py", "from lib import increment", "increment(5)")
        if not ok:
            return ok, detail
        return file_lacks(outcome, "main.py", "old_name")


class AQSearchAddImport(AgentQualityCase):
    name = "aq-search-add-import"
    description = "Find every file using os.path.join and add `import os` where missing."
    files = {
        "a.py": "result = os.path.join('a', 'b')\n",
        "b.py": "import os\nresult = os.path.join('c', 'd')\n",
        "c.py": "print('hello')\n",
    }
    user_prompt = (
        "Some files in this project use `os.path.join` but are missing `import os` at the top. "
        "Find and fix all such files. Don't touch files that don't need the import or already have it."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        ok, detail = file_contains(outcome, "a.py", "import os", "os.path.join")
        if not ok:
            return ok, detail
        b_after = outcome.files_after.get("b.py", "")
        if b_after.count("import os") != 1:
            return False, f"b.py should have exactly one `import os`, found {b_after.count('import os')}"
        c_after = outcome.files_after.get("c.py", "")
        if "import os" in c_after:
            return False, "c.py should NOT have been modified (no os.path.join usage)"
        return True, "a.py fixed; b.py untouched (already had import); c.py untouched"


class AQSearchDeleteDeadCode(AgentQualityCase):
    name = "aq-search-delete-dead-code"
    description = "Find and remove a function that has no callers."
    files = {
        "utils.py": (
            "def used_helper(x):\n    return x * 2\n\n"
            "def unused_helper(x):\n    return x + 100\n\n"
            "def another_used(y):\n    return y - 1\n"
        ),
        "app.py": (
            "from utils import used_helper, another_used\n"
            "print(used_helper(5))\n"
            "print(another_used(10))\n"
        ),
    }
    user_prompt = (
        "In utils.py, the function `unused_helper` is never called anywhere in this project. "
        "Delete it (and any redundant blank lines). Keep the other two functions intact."
    )

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        ok, detail = file_lacks(outcome, "utils.py", "unused_helper", "x + 100")
        if not ok:
            return ok, detail
        return file_contains(outcome, "utils.py", "def used_helper(x)", "def another_used(y)")


class AQSearchFindCallersAcrossManyFiles(AgentQualityCase):
    name = "aq-search-find-callers-across-many-files"
    description = "Find every file that calls a function and report them (grep-requiring)."
    files = {
        "lib.py": "def send_email(to, subject):\n    pass\n",
        "a.py": "from lib import send_email\nsend_email('a@x', 'A')\n",
        "b.py": "from lib import send_email\nsend_email('b@x', 'B')\n",
        "c.py": "import lib\nlib.send_email('c@x', 'C')\n",
        "d.py": "x = 1\n",
        "e.py": "from lib import send_email\n# send_email disabled in this module\n",
    }
    user_prompt = (
        "List every Python file in this project that ACTUALLY CALLS the function `send_email` "
        "(i.e. the call is not commented out). Write the list — one filename per line — to a "
        "new file called `callers.txt`. Exclude files that only import send_email or have the "
        "call commented out."
    )
    timeout_s = 180

    def judge(self, outcome: AgentRunOutcome) -> tuple[bool, str]:
        text = outcome.files_after.get("callers.txt", "")
        if not text:
            return False, "callers.txt was not created"
        expected = {"a.py", "b.py", "c.py"}
        unexpected_present = set()
        missing = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped in expected:
                missing.discard(stripped)
            elif stripped in {"d.py", "e.py"}:
                unexpected_present.add(stripped)
        missing = expected - {line.strip() for line in text.splitlines() if line.strip()}
        if missing:
            return False, f"callers.txt missing files: {sorted(missing)}"
        if unexpected_present:
            return False, f"callers.txt has unexpected: {sorted(unexpected_present)}"
        return True, "callers.txt lists exactly a.py, b.py, c.py"

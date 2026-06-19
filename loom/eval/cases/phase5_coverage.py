from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from loom.eval.runner import EvalCase, EvalResult

REPO_ROOT = Path(__file__).resolve().parents[3]


class PermissionDenyListBlocksSudo(EvalCase):
    name = "permission-deny-list-blocks-sudo"
    description = "Hooks PreToolUse blocks 'sudo' via the wider DENY_LIST (Phase 5 §2 permission denial)"

    def run(self) -> EvalResult:
        from types import SimpleNamespace

        from loom.agent.hooks import Hooks
        block = SimpleNamespace(name="bash", input={"command": "sudo apt install foo"})
        reason = Hooks().check_permission_hook("PreToolUse", block)
        if reason is None:
            return EvalResult(name=self.name, passed=False, detail="sudo not blocked by hook")
        return EvalResult(name=self.name, passed=True, detail=f"blocked: {reason[:80]}")


class PermissionDenyListBlocksDd(EvalCase):
    name = "permission-deny-list-blocks-dd"
    description = "Hooks PreToolUse blocks 'dd if=' via the wider DENY_LIST (separate from run_bash's hardcoded list)"

    def run(self) -> EvalResult:
        from types import SimpleNamespace

        from loom.agent.hooks import Hooks
        block = SimpleNamespace(name="bash", input={"command": "dd if=/dev/zero of=/tmp/x bs=1M"})
        reason = Hooks().check_permission_hook("PreToolUse", block)
        if reason is None:
            return EvalResult(name=self.name, passed=False, detail="dd not blocked by hook")
        return EvalResult(name=self.name, passed=True, detail=f"blocked: {reason[:80]}")


class PermissionWriteOutsideWorkspaceRejected(EvalCase):
    name = "permission-write-outside-workspace-rejected"
    description = "run_write / run_read reject paths that escape WORKDIR (path-escape rule)"

    def run(self) -> EvalResult:
        import loom.agent.tools as main
        write_result = main.run_write("/etc/passwd", "x")
        read_result = main.run_read("/etc/passwd")
        if "Path escapes workspace" not in write_result and "escapes" not in read_result.lower():
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"write={write_result[:80]} read={read_result[:80]}",
            )
        return EvalResult(name=self.name, passed=True, detail="path escape rejected")


class MicrocompactClearsOldToolResults(EvalCase):
    name = "microcompact-clears-old-tool-results"
    description = "microcompact zeros out old tool_result content; recent KEEP_RECENT rounds untouched"

    def run(self) -> EvalResult:
        from loom.agent.context import Context
        ctx = Context()
        messages: list = []
        for i in range(8):
            messages.append({"role": "user", "content": f"round {i} prompt"})
            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": f"id-{i}",
                    "name": "bash",
                    "input": {"command": "echo x"},
                }],
            })
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": f"id-{i}",
                    "content": f"OLD-RESULT-{i}",
                    "is_error": False,
                }],
            })
        ctx.microcompact("AgentStop", messages)
        cleared = sum(
            1 for m in messages
            if m["role"] == "user" and isinstance(m["content"], list)
            and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                and "cleared" in str(b.get("content", ""))
                for b in m["content"]
            )
        )
        if cleared < 1:
            return EvalResult(name=self.name, passed=False, detail=f"cleared={cleared}, expected >= 1")
        tail = messages[-1]["content"][0]["content"]
        if "cleared" in tail:
            return EvalResult(name=self.name, passed=False, detail="tail round got cleared (should be preserved)")
        return EvalResult(name=self.name, passed=True, detail=f"cleared {cleared} old, tail preserved")


class MicrocompactSkipsWhenBelowKeepRecent(EvalCase):
    name = "microcompact-skips-when-below-keep-recent"
    description = "messages shorter than KEEP_RECENT + 1 → microcompact is a no-op"

    def run(self) -> EvalResult:
        from loom.agent.context import KEEP_RECENT, Context
        ctx = Context()
        messages: list = []
        for i in range(KEEP_RECENT):
            messages.append({"role": "user", "content": f"round {i}"})
            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use", "id": f"id-{i}", "name": "bash",
                    "input": {"command": "x"},
                }],
            })
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result", "tool_use_id": f"id-{i}",
                    "content": f"PRESERVED-{i}", "is_error": False,
                }],
            })
        before = repr(messages)
        ctx.microcompact("AgentStop", messages)
        if repr(messages) != before:
            return EvalResult(name=self.name, passed=False, detail="microcompact mutated messages below KEEP_RECENT")
        return EvalResult(name=self.name, passed=True, detail=f"no-op for {KEEP_RECENT} rounds")


class ShouldCompactTriggersAtThreshold(EvalCase):
    name = "should-compact-triggers-at-threshold"
    description = "should_compact returns True when current_tokens >= 0.85 * context_window"

    def run(self) -> EvalResult:
        from loom.agent.context import Context
        ctx = Context()
        ctx.last_input_tokens = int(0.90 * 1000)
        messages: list = [{"role": "user", "content": "x" * 10}]
        if not ctx.should_compact(messages, context_window=1000):
            return EvalResult(name=self.name, passed=False, detail="did not trigger at 90% of window")
        return EvalResult(name=self.name, passed=True, detail="triggers at >= 85% of window")


class ShouldCompactSkipsBelowThreshold(EvalCase):
    name = "should-compact-skips-below-threshold"
    description = "should_compact returns False when current_tokens < 0.85 * context_window"

    def run(self) -> EvalResult:
        from loom.agent.context import Context
        ctx = Context()
        ctx.last_input_tokens = 0
        messages: list = [{"role": "user", "content": "x" * 10}]
        if ctx.should_compact(messages, context_window=200_000):
            return EvalResult(name=self.name, passed=False, detail="triggered well below threshold")
        return EvalResult(name=self.name, passed=True, detail="skipped below 85%")


class SubagentTurnCapEnforced(EvalCase):
    name = "subagent-turn-cap-enforced"
    description = "spawn_subagent stops after the hard 30-turn cap even if LLM keeps calling tools"

    def run(self) -> EvalResult:
        from loom.agent.tools import spawn_subagent

        mock_response = MagicMock()
        mock_response.stop_reason = "tool_use"
        mock_response.content = [
            MagicMock(type="tool_use", id="call-1", name="bash",
                      input={"command": "true"}),
        ]
        mock_response.usage.input_tokens = 5
        mock_response.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.client.messages.create.return_value = mock_response
        from loom.agent.hooks import Hooks
        hooks = Hooks()

        result = spawn_subagent("loop forever", llm_client=mock_client, hooks=hooks)
        if not result.startswith("[done: "):
            return EvalResult(name=self.name, passed=False, detail=f"missing prefix: {result[:80]}")
        turns_part = result.split("turns")[0].split("[done: ")[-1].strip()
        turns = int(turns_part)
        if turns > 30:
            return EvalResult(name=self.name, passed=False, detail=f"exceeded 30 turns: {turns}")
        return EvalResult(name=self.name, passed=True, detail=f"stopped at {turns} turns")


class SubagentSchemaExcludesTaskTool(EvalCase):
    name = "subagent-schema-excludes-task-tool"
    description = "SUB_TOOLS (subagent's allowed tool surface) does NOT include 'task' — recursion prevented at schema level"

    def run(self) -> EvalResult:
        from loom.agent.tools import SUB_TOOLS
        names = [t["name"] for t in SUB_TOOLS]
        if "task" in names:
            return EvalResult(name=self.name, passed=False, detail=f"task found in SUB_TOOLS: {names}")
        return EvalResult(name=self.name, passed=True, detail=f"SUB_TOOLS has no task ({len(names)} tools)")


class MemorySearchFindsPriorContent(EvalCase):
    name = "memory-search-finds-prior-content"
    description = "MemoryStore.search returns lines matching query (cross-session recovery)"

    def run(self) -> EvalResult:
        import shutil

        from loom.eval._util import make_empty_workdir
        from loom.memory import MemoryStore
        wd = make_empty_workdir("memory-search")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(wd)
        store.append("User prefers tabs over spaces.")
        store.append("Project: loop test consumer.")
        hits = store.search("tabs")
        if not any("tabs" in h for h in hits):
            return EvalResult(name=self.name, passed=False, detail=f"no hit: {hits}")
        hits_upper = store.search("LOOP TEST")
        if not any("loop test" in h.lower() for h in hits_upper):
            return EvalResult(name=self.name, passed=False, detail=f"case-insensitive failed: {hits_upper}")
        return EvalResult(name=self.name, passed=True, detail=f"{len(hits)}+{len(hits_upper)} hits")


class MemorySummaryTruncates(EvalCase):
    name = "memory-summary-truncates"
    description = "MemoryStore.summary returns at most max_lines lines"

    def run(self) -> EvalResult:
        import shutil

        from loom.eval._util import make_empty_workdir
        from loom.memory import MemoryStore
        wd = make_empty_workdir("memory-summary")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(wd)
        for i in range(15):
            store.append(f"entry {i}")
        summary = store.summary(max_lines=10)
        line_count = summary.count("\n") + (1 if summary else 0)
        if line_count > 10:
            return EvalResult(name=self.name, passed=False, detail=f"summary has {line_count} lines (>10)")
        if "entry 0" not in summary:
            return EvalResult(name=self.name, passed=False, detail="summary missing first entry")
        return EvalResult(name=self.name, passed=True, detail=f"summary={line_count} lines")
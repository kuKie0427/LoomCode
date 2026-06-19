"""Failure-mode eval cases (Phase P1: harness-eval self-verify loop).

Each case injects a controlled failure point via unittest.mock and asserts that
the harness degrades gracefully (no silent loss, no swallowed exception).

7 cases:
1. failure-mode-bash-tool-timeout              — run_bash handles subprocess.TimeoutExpired
2. failure-mode-llm-api-5xx                    — agent_loop propagates APIStatusError to caller
3. failure-mode-autocompact-fails-context-overflow — _generate_summary=None → no message loss
4. failure-mode-unexpected-stop-reason         — content_filtered treated as end_turn
5. failure-mode-permission-denied-mid-batch    — one denied block doesn't kill siblings
6. failure-mode-subagent-tool-error            — subagent surfaces tool failure gracefully
7. failure-mode-subagent-doesnt-trigger-session-end-init-sh
   — subagent's AgentStop must NOT fire SessionEnd init.sh progress.md write
     (locks the non-concurrent-write contract for task 3)
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

from anthropic.types import MessageParam

from loom.eval.runner import EvalCase, EvalResult

# ── Case 1: bash tool timeout ───────────────────────────────────────────────


class FailureModeBashToolTimeout(EvalCase):
    name = "failure-mode-bash-tool-timeout"
    description = "run_bash handles subprocess.TimeoutExpired gracefully, returns error string"

    def run(self) -> EvalResult:
        import loom.agent.tools as tools

        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="sleep 200", timeout=120)):
            result = tools.run_bash("sleep 200")
        if "Timeout" not in result or "120" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected timeout string, got: {result[:120]!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"timeout handled: {result[:80]!r}",
        )


# ── Case 2: LLM API 5xx propagates ──────────────────────────────────────────


class FailureModeLlmApi5xx(EvalCase):
    name = "failure-mode-llm-api-5xx"
    description = "agent_loop propagates APIStatusError-like exception to caller (does not swallow)"

    def run(self) -> EvalResult:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("anthropic.APIStatusError: 500 server_error")

        mock_llm = MagicMock()
        mock_llm.model = "fake-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 200000

        raised: Exception | None = None
        try:
            with patch("loom.agent.loop.configure_logging"), \
                 patch("loom.agent.loop.trace_mod"), \
                 patch("loom.agent.loop.checkpoint"), \
                 patch("loom.agent.loop.hooks"), \
                 patch("loom.agent.loop.context") as mock_ctx:
                mock_ctx.should_compact.return_value = False
                from loom.agent.loop import agent_loop
                agent_loop(
                    [{"role": "user", "content": "x"}],
                    llm_client=mock_llm,
                    callbacks={},
                )
        except Exception as exc:
            raised = exc

        if raised is None:
            return EvalResult(
                name=self.name, passed=False,
                detail="expected exception, agent_loop returned cleanly",
            )
        if "500" not in str(raised):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"got unexpected exception: {type(raised).__name__}: {raised}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"propagated: {type(raised).__name__}: {str(raised)[:80]!r}",
        )


# ── Case 3: autocompact fails on context overflow ────────────────────────────


class FailureModeAutocompactFailsContextOverflow(EvalCase):
    name = "failure-mode-autocompact-fails-context-overflow"
    description = "When _generate_summary returns None during context overflow, autocompact no-ops (no message loss)"

    def run(self) -> EvalResult:
        from loom.agent.context import Context

        ctx = Context()
        ctx.last_input_tokens = int(0.95 * 1000)

        messages = [
            {"role": "user", "content": "round 0 prompt"},
            {"role": "assistant", "content": [{"type": "text", "text": "OK"}]},
            {"role": "user", "content": "round 1 prompt"},
            {"role": "assistant", "content": [{"type": "text", "text": "OK2"}]},
        ]
        original_len = len(messages)

        fake_client = MagicMock()
        with patch.object(Context, "_generate_summary", return_value=None):
            ctx.autocompact(cast(list[MessageParam], messages), fake_client, "fake-model", context_window=1000)

        if len(messages) != original_len:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"messages mutated despite summary=None: was {original_len}, now {len(messages)}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"preserved {len(messages)} messages after summary=None (no silent truncation)",
        )


# ── Case 4: unexpected stop_reason ───────────────────────────────────────────


class FailureModeUnexpectedStopReason(EvalCase):
    name = "failure-mode-unexpected-stop-reason"
    description = "LLM stop_reason='content_filtered' is treated as end_turn (not tool_use)"

    def run(self) -> EvalResult:
        mock_response = MagicMock()
        mock_response.stop_reason = "content_filtered"
        mock_response.content = [MagicMock(type="text", text="I cannot help with that")]
        mock_response.usage.input_tokens = 5
        mock_response.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        mock_llm = MagicMock()
        mock_llm.model = "fake-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 200000

        messages = [{"role": "user", "content": "x"}]
        initial_len = len(messages)

        try:
            with patch("loom.agent.loop.configure_logging"), \
                 patch("loom.agent.loop.trace_mod"), \
                 patch("loom.agent.loop.checkpoint"), \
                 patch("loom.agent.loop.hooks"), \
                 patch("loom.agent.loop.context") as mock_ctx:
                mock_ctx.should_compact.return_value = False
                from loom.agent.loop import agent_loop
                agent_loop(messages, llm_client=mock_llm, callbacks={})
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"agent_loop raised on content_filtered: {type(exc).__name__}: {exc}",
            )

        if len(messages) <= initial_len:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"missing assistant message: {messages!r}",
            )
        if messages[-1]["role"] != "assistant":
            return EvalResult(
                name=self.name, passed=False,
                detail=f"last message not assistant: role={messages[-1]['role']}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="treated content_filtered as end_turn, appended assistant msg",
        )


# ── Case 5: permission denied mid-batch ──────────────────────────────────────


class FailureModePermissionDeniedMidBatch(EvalCase):
    name = "failure-mode-permission-denied-mid-batch"
    description = "When one of multiple tool_use blocks is denied mid-batch, other tools still run and denial is propagated"

    def run(self) -> EvalResult:
        import loom.agent.loop as loop_mod
        from loom.agent.permissions import DEFAULT_POLICY

        block1 = SimpleNamespace(id="call-1", name="bash", input={"command": "echo 1"})
        block2 = SimpleNamespace(id="call-2", name="bash", input={"command": "sudo rm /etc/passwd"})
        block3 = SimpleNamespace(id="call-3", name="bash", input={"command": "echo 3"})

        hooks = loop_mod.Hooks(policy=DEFAULT_POLICY)
        hooks.register_hook("PreToolUse", hooks.check_permission_hook)

        results = loop_mod._run_tool_turn([block1, block2, block3], hooks)

        if len(results) != 3:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"expected 3 results, got {len(results)}",
            )

        r1, r2, r3 = results
        # Middle block must be denied (is_error=True + content indicates denial)
        if not (r2 and r2.get("is_error") and "denied" in str(r2.get("content", "")).lower()):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"middle result not denied: {r2!r}",
            )
        if r1 is None or r3 is None:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"results 1 or 3 missing: {r1!r} {r3!r}",
            )
        if r1.get("is_error") or r3.get("is_error"):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"outer blocks should succeed: r1={r1!r} r3={r3!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="mid-batch denial: 1st and 3rd ran, 2nd reported as denied tool_result",
        )


# ── Case 6: subagent tool error ──────────────────────────────────────────────


class FailureModeSubagentToolError(EvalCase):
    name = "failure-mode-subagent-tool-error"
    description = "spawn_subagent returns gracefully when subagent's tool exits non-zero (no exception)"

    def run(self) -> EvalResult:
        from loom.agent.hooks import Hooks
        from loom.agent.tools import spawn_subagent

        mock_response_tool = MagicMock()
        mock_response_tool.stop_reason = "tool_use"
        mock_response_tool.content = [
            MagicMock(type="tool_use", id="call-1", name="bash",
                      input={"command": "false"}),
        ]
        mock_response_tool.usage.input_tokens = 5
        mock_response_tool.usage.output_tokens = 5

        mock_response_end = MagicMock()
        mock_response_end.stop_reason = "end_turn"
        mock_response_end.content = [MagicMock(type="text", text="Done with error")]
        mock_response_end.usage.input_tokens = 5
        mock_response_end.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.client.messages.create.side_effect = [mock_response_tool, mock_response_end]
        mock_client.model = "fake-model"

        hooks = Hooks()

        try:
            result = spawn_subagent("test task", llm_client=mock_client, hooks=hooks)
        except Exception as exc:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"spawn_subagent raised: {type(exc).__name__}: {exc}",
            )

        if not result.startswith("[done: "):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"missing structured prefix: {result[:80]!r}",
            )
        if "Done with error" not in result:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"summary missing: {result[:200]!r}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail=f"subagent handled error gracefully: {result[:80]!r}",
        )


# ── Case 7: subagent doesn't trigger SessionEnd init.sh ──────────────────────


class FailureModeSubagentDoesntTriggerSessionEndInitSh(EvalCase):
    name = "failure-mode-subagent-doesnt-trigger-session-end-init-sh"
    description = "Subagent AgentStop does NOT fire the SessionEnd init.sh progress.md write — contract for Task 3"

    def run(self) -> EvalResult:
        import builtins

        from loom.agent.hooks import Hooks
        from loom.agent.tools import spawn_subagent

        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [MagicMock(type="text", text="subagent done")]
        mock_response.usage.input_tokens = 5
        mock_response.usage.output_tokens = 5

        mock_client = MagicMock()
        mock_client.client.messages.create.return_value = mock_response
        mock_client.model = "fake-model"

        hooks = Hooks()

        # Track if any progress.md write happens via SessionEnd path
        progress_path_written: list[str] = []
        original_open = builtins.open

        def tracking_open(*args, **kwargs):
            if args:
                path_str = str(args[0])
                mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
                if "progress.md" in path_str and "a" in str(mode):
                    progress_path_written.append(path_str)
            return original_open(*args, **kwargs)

        with patch.object(builtins, "open", new=tracking_open):
            try:
                spawn_subagent("quick task", llm_client=mock_client, hooks=hooks)
            except Exception:
                pass

        if progress_path_written:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"subagent AgentStop triggered SessionEnd init.sh write: {progress_path_written}",
            )
        return EvalResult(
            name=self.name, passed=True,
            detail="subagent AgentStop does NOT fire SessionEnd init.sh — non-concurrent write contract preserved",
        )
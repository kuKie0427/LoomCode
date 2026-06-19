from pathlib import Path

from loom.eval.runner import EvalCase, EvalResult


class BashDenyListBlocksRmRf(EvalCase):
    name = "bash-deny-list-blocks-rm-rf"
    description = "run_bash blocks 'rm -rf /' (deny list)"

    def run(self) -> EvalResult:
        import loom.agent.tools as main
        output = main.run_bash("rm -rf /")
        if "Dangerous command blocked" not in output:
            return EvalResult(name=self.name, passed=False, detail=f"output: {output[:100]}")
        return EvalResult(name=self.name, passed=True, detail="blocked as expected")


class BashAllowsSafeCommand(EvalCase):
    name = "bash-allows-safe-command"
    description = "run_bash allows safe commands like echo"

    def run(self) -> EvalResult:
        import loom.agent.tools as main
        output = main.run_bash("echo hello-eval")
        if "hello-eval" not in output:
            return EvalResult(name=self.name, passed=False, detail=f"output: {output!r}")
        return EvalResult(name=self.name, passed=True, detail="safe command ran")


class Tier1WithinBudget(EvalCase):
    name = "tier1-within-budget"
    description = "load_tier1 stays within TIER1_TOKEN_BUDGET (500 tokens)"

    def run(self) -> EvalResult:
        from loom.eval._util import make_empty_workdir
        wd = make_empty_workdir("tier1-budget")
        (wd / "feature_list.json").write_text(
            '{"features":[{"id":"f1","name":"n","description":"d","status":"done","verification":"v","evidence":"e"}]}',
            encoding="utf-8",
        )
        from loom.memory.context import TIER1_TOKEN_BUDGET, load_tier1, token_count
        text = load_tier1(wd)
        used = token_count(text)
        if used > TIER1_TOKEN_BUDGET:
            return EvalResult(name=self.name, passed=False, detail=f"used {used} > {TIER1_TOKEN_BUDGET}")
        return EvalResult(name=self.name, passed=True, detail=f"used {used}/{TIER1_TOKEN_BUDGET}")


class CombinedTier1Tier2WithinBudget(EvalCase):
    name = "combined-tier1-tier2-within-budget"
    description = "combined_tier1_tier2 stays within COMBINED_BUDGET (2500 tokens)"

    def run(self) -> EvalResult:
        from loom.eval._util import make_empty_workdir
        wd = make_empty_workdir("tier12-budget")
        (wd / "AGENTS.md").write_text("Test project.\n" * 100)
        (wd / "feature_list.json").write_text(
            '{"features":[{"id":"f1","name":"n","description":"d","status":"done","verification":"v","evidence":"e"}]}',
            encoding="utf-8",
        )
        from loom.memory.context import COMBINED_BUDGET, combined_tier1_tier2, token_count
        text = combined_tier1_tier2(wd)
        used = token_count(text)
        if used > COMBINED_BUDGET:
            return EvalResult(name=self.name, passed=False, detail=f"used {used} > {COMBINED_BUDGET}")
        return EvalResult(name=self.name, passed=True, detail=f"used {used}/{COMBINED_BUDGET}")


class CheckpointIsDueAtThreshold(EvalCase):
    name = "checkpoint-is-due-at-threshold"
    description = "checkpoint.is_due returns True at 10 calls or 5000 tokens"

    def run(self) -> EvalResult:
        from loom.agent.checkpoint import (
            CHECKPOINT_EVERY_TOKENS,
            CHECKPOINT_EVERY_TOOL_CALLS,
            is_due,
        )
        if not is_due(tool_call_count=CHECKPOINT_EVERY_TOOL_CALLS, new_tokens_since_checkpoint=0):
            return EvalResult(name=self.name, passed=False, detail="not due at tool threshold")
        if not is_due(tool_call_count=0, new_tokens_since_checkpoint=CHECKPOINT_EVERY_TOKENS):
            return EvalResult(name=self.name, passed=False, detail="not due at token threshold")
        if is_due(tool_call_count=2, new_tokens_since_checkpoint=100):
            return EvalResult(name=self.name, passed=False, detail="due prematurely at low values")
        return EvalResult(name=self.name, passed=True, detail="thresholds correct")


class CheckpointAtomicSave(EvalCase):
    name = "checkpoint-atomic-save"
    description = "Checkpoint.save uses atomic .tmp + rename; no leftover .tmp"

    def run(self) -> EvalResult:
        from unittest.mock import MagicMock

        from loom.eval._util import make_empty_workdir
        wd = make_empty_workdir("ckpt-atomic")
        llm = MagicMock()
        llm.model = "test"
        ctx = MagicMock()
        ctx.last_input_tokens = 100
        ctx.checked_at_index = 1
        from loom.agent.checkpoint import default_path_for, save
        save(wd, [{"role": "user", "content": "x"}], llm, ctx, tool_call_count=5)
        path = default_path_for(wd)
        tmp = path.with_suffix(path.suffix + ".tmp")
        if tmp.exists():
            return EvalResult(name=self.name, passed=False, detail=f"leftover tmp: {tmp}")
        if not path.exists():
            return EvalResult(name=self.name, passed=False, detail="checkpoint not saved")
        return EvalResult(name=self.name, passed=True, detail="atomic write complete")


class SubagentReturnsStructuredMetadata(EvalCase):
    name = "subagent-returns-structured-metadata"
    description = "spawn_subagent result starts with [done: N turns, M tool calls]"

    def run(self) -> EvalResult:
        from unittest.mock import MagicMock

        from anthropic.types import TextBlock
        mock_response = MagicMock()
        mock_response.content = [TextBlock(type="text", text="done")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 50
        mock_client = MagicMock()
        mock_client.client.messages.create.return_value = mock_response
        from loom.agent.tools import spawn_subagent
        result = spawn_subagent("test", llm_client=mock_client)
        if not result.startswith("[done: "):
            return EvalResult(name=self.name, passed=False, detail=f"missing prefix: {result[:60]}")
        if "tool calls]" not in result:
            return EvalResult(name=self.name, passed=False, detail=f"missing tool calls suffix: {result!r}")
        return EvalResult(name=self.name, passed=True, detail=f"structured: {result[:40]}")


class LoopAuditScoresItself(EvalCase):
    name = "loop-audit-scores-itself"
    description = "loop audit . on this project scores ≥ 70 (success metric)"

    def run(self) -> EvalResult:
        repo_root = Path(__file__).resolve().parents[3]
        from subprocess import run as srun
        from sys import executable
        proc = srun(
            [executable, "-m", "loom.cli", "audit"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return EvalResult(name=self.name, passed=False, detail=f"audit exit {proc.returncode}")
        for line in proc.stdout.splitlines():
            if "Overall:" in line:
                score_str = line.split("Overall:")[-1].strip().split("/")[0]
                score = int(score_str)
                if score < 70:
                    return EvalResult(name=self.name, passed=False, detail=f"score {score} < 70")
                return EvalResult(name=self.name, passed=True, detail=f"score {score}")
        return EvalResult(name=self.name, passed=False, detail="Overall: line not found")

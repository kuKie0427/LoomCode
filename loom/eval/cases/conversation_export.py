"""Harness eval cases for f-conversation-export-p2."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class ConversationExportModuleDefined(EvalCase):
    name = "conversation-export-module-defined"
    description = "loom.agent.export module exists with public API"

    def run(self) -> EvalResult:
        try:
            import loom.agent.export as e
        except ImportError as exc:
            return EvalResult(name=self.name, passed=False, detail=f"import failed: {exc}")
        for name in ("to_markdown", "to_json", "write_export", "redact_pii", "ExportMetadata"):
            if not hasattr(e, name):
                return EvalResult(name=self.name, passed=False, detail=f"missing {name}")
        return EvalResult(name=self.name, passed=True, detail="all public API present")


class ConversationExportCliSubcommand(EvalCase):
    name = "conversation-export-cli-subcommand"
    description = "`loom export` CLI subcommand is registered with --format + --redact flags"

    def run(self) -> EvalResult:
        from pathlib import Path
        src = Path("loom/cli.py").read_text()
        if '"export"' not in src and "'export'" not in src:
            return EvalResult(name=self.name, passed=False, detail="export subcommand not registered")
        if '"markdown"' not in src and "'markdown'" not in src:
            return EvalResult(name=self.name, passed=False, detail="--format markdown missing")
        if "--redact" not in src:
            return EvalResult(name=self.name, passed=False, detail="--redact flag missing")
        return EvalResult(name=self.name, passed=True, detail="export subcommand wired with format + redact flags")


class ConversationExportMarkdownRenders(EvalCase):
    name = "conversation-export-markdown-renders"
    description = "to_markdown produces a transcript with role headers, tool calls, and cost summary"

    def run(self) -> EvalResult:
        from loom.agent.cost import SessionCostAccumulator, TokenUsage, compute_cost
        from loom.agent.export import ExportMetadata, to_markdown

        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Hi"},
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file.txt", "is_error": False}
            ]},
        ]
        sess = SessionCostAccumulator()
        sess.add(TokenUsage(input_tokens=1000, output_tokens=200), compute_cost("claude-sonnet-4-5", TokenUsage(1000, 200)))
        meta = ExportMetadata(
            model="claude-sonnet-4-5",
            session_id="s1",
            workdir="/tmp",
            tool_call_count=1,
            started_at="t1",
            ended_at="t2",
            session_cost=sess,
        )
        out = to_markdown(msgs, meta)
        if "Hello" not in out or "Hi" not in out or "bash" not in out or "Total cost" not in out:
            return EvalResult(name=self.name, passed=False, detail=f"missing sections: {out[:200]}")
        return EvalResult(name=self.name, passed=True, detail="markdown renders all required sections")

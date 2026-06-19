from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from loom.eval.runner import EvalCase, EvalResult

REPO_ROOT = Path(__file__).resolve().parents[3]


class CheckpointRoundtripPreservesToolUseBlocks(EvalCase):
    name = "checkpoint-roundtrip-preserves-tool-use-blocks"
    description = "Save + load roundtrip preserves full tool_use / tool_result blocks (resume needs LLM context)"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import load, save
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("ckpt-tooluse")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        messages = [
            {"role": "user", "content": "find all python files"},
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "t1", "name": "glob",
                "input": {"pattern": "*.py"},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "t1",
                "content": "main.py\ntools.py\n", "is_error": False,
            }]},
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "t2", "name": "read_file",
                "input": {"path": "main.py"},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "t2",
                "content": "x = 1\n", "is_error": False,
            }]},
        ]
        ctx = MagicMock(last_input_tokens=1000, checked_at_index=4)
        llm = MagicMock(model="claude-test")
        save(wd, messages, llm, ctx, tool_call_count=2)

        loaded = load(wd)
        if loaded is None:
            return EvalResult(name=self.name, passed=False, detail="load returned None")
        restored = loaded["messages"]
        if restored != messages:
            return EvalResult(name=self.name, passed=False, detail="messages diverged after roundtrip")
        if loaded["tool_call_count"] != 2:
            return EvalResult(name=self.name, passed=False, detail=f"tool_call_count={loaded['tool_call_count']}")
        if loaded["model"] != "claude-test":
            return EvalResult(name=self.name, passed=False, detail=f"model={loaded['model']}")
        return EvalResult(name=self.name, passed=True, detail=f"{len(messages)} messages preserved")


class CheckpointLoadReturnsNoneForCorruptJson(EvalCase):
    name = "checkpoint-load-returns-none-for-corrupt-json"
    description = "load() returns None (not raises) when checkpoint.json is corrupt — restart survives bad state"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import default_path_for, load
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("ckpt-corrupt")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        path = default_path_for(wd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json {{{", encoding="utf-8")

        try:
            result = load(wd)
        except Exception as exc:
            return EvalResult(name=self.name, passed=False, detail=f"raised {type(exc).__name__}: {exc}")
        if result is not None:
            return EvalResult(name=self.name, passed=False, detail=f"expected None, got {result}")
        return EvalResult(name=self.name, passed=True, detail="corrupt JSON → None")


class CheckpointLoadReturnsNoneForMissingFile(EvalCase):
    name = "checkpoint-load-returns-none-for-missing-file"
    description = "load() returns None when no checkpoint exists (fresh start contract)"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import load
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("ckpt-missing")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        result = load(wd)
        if result is not None:
            return EvalResult(name=self.name, passed=False, detail=f"expected None, got {result}")
        return EvalResult(name=self.name, passed=True, detail="missing file → None")


class CheckpointSavedAtIsIsoTimestamp(EvalCase):
    name = "checkpoint-saved-at-is-iso-timestamp"
    description = "saved_at is a parseable ISO 8601 timestamp (so humans can read the file)"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import load, save
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("ckpt-iso")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        ctx = MagicMock(last_input_tokens=0, checked_at_index=0)
        llm = MagicMock(model="m")
        save(wd, [{"role": "user", "content": "x"}], llm, ctx, tool_call_count=0)
        loaded = load(wd)
        if loaded is None:
            return EvalResult(name=self.name, passed=False, detail="load returned None")
        ts = loaded["saved_at"]
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            return EvalResult(name=self.name, passed=False, detail=f"unparseable: {ts!r} ({exc})")
        return EvalResult(name=self.name, passed=True, detail=f"saved_at={ts}")


class CheckpointMessagesPreserveOrder(EvalCase):
    name = "checkpoint-messages-preserve-order"
    description = "Roundtrip preserves message order (LLM context contract — sequence matters)"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import load, save
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("ckpt-order")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        messages = [
            {"role": "user", "content": f"msg-{i}"}
            for i in range(20)
        ]
        ctx = MagicMock(last_input_tokens=0, checked_at_index=0)
        llm = MagicMock(model="m")
        save(wd, messages, llm, ctx, tool_call_count=0)
        loaded = load(wd)
        if loaded is None:
            return EvalResult(name=self.name, passed=False, detail="load returned None")
        restored = loaded["messages"]
        if [m["content"] for m in restored] != [f"msg-{i}" for i in range(20)]:
            return EvalResult(name=self.name, passed=False, detail="order diverged")
        return EvalResult(name=self.name, passed=True, detail="20 messages in original order")


class CheckpointMaybeSaveFiresAtToolThreshold(EvalCase):
    name = "checkpoint-maybe-save-fires-at-tool-threshold"
    description = "maybe_save returns a path exactly when is_due is True (tool-call threshold hit)"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import (
            CHECKPOINT_EVERY_TOOL_CALLS,
            maybe_save,
        )
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("ckpt-maybe-tool")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        ctx = MagicMock(last_input_tokens=0, checked_at_index=0)
        llm = MagicMock(model="m")
        msgs = [{"role": "user", "content": "x"}]

        result_before = maybe_save(wd, msgs, llm, ctx, tool_call_count=CHECKPOINT_EVERY_TOOL_CALLS - 1, new_tokens_since_checkpoint=0)
        result_at = maybe_save(wd, msgs, llm, ctx, tool_call_count=CHECKPOINT_EVERY_TOOL_CALLS, new_tokens_since_checkpoint=0)

        if result_before is not None:
            return EvalResult(name=self.name, passed=False, detail=f"saved before threshold: {result_before}")
        if result_at is None:
            return EvalResult(name=self.name, passed=False, detail="did not save at threshold")
        return EvalResult(name=self.name, passed=True, detail=f"saved at N={CHECKPOINT_EVERY_TOOL_CALLS}")


class CheckpointMaybeSaveFiresAtTokenThreshold(EvalCase):
    name = "checkpoint-maybe-save-fires-at-token-threshold"
    description = "maybe_save fires when token threshold is hit even if tool-call threshold isn't"

    def run(self) -> EvalResult:
        import shutil

        from loom.agent.checkpoint import (
            CHECKPOINT_EVERY_TOKENS,
            maybe_save,
        )
        from loom.eval._util import make_empty_workdir

        wd = make_empty_workdir("ckpt-maybe-tokens")
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True, exist_ok=True)

        ctx = MagicMock(last_input_tokens=0, checked_at_index=0)
        llm = MagicMock(model="m")
        msgs = [{"role": "user", "content": "x"}]

        result = maybe_save(wd, msgs, llm, ctx, tool_call_count=1, new_tokens_since_checkpoint=CHECKPOINT_EVERY_TOKENS)
        if result is None:
            return EvalResult(name=self.name, passed=False, detail="did not save at token threshold")
        return EvalResult(name=self.name, passed=True, detail=f"saved at tokens={CHECKPOINT_EVERY_TOKENS}")


class CheckpointResumeCliRestoresHistory(EvalCase):
    name = "checkpoint-resume-cli-restores-history"
    description = "End-to-end: plant checkpoint → `loom run --resume` (stdin=exit) → history logged as restored"

    def run(self) -> EvalResult:
        import shutil
        wd = Path(tempfile.mkdtemp(prefix="loom-eval-resume-"))
        shutil.rmtree(wd, ignore_errors=True)
        wd.mkdir(parents=True)

        ckpt_path = wd / ".minicode" / "checkpoint.json"
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        ckpt = {
            "saved_at": "2026-06-17T00:00:00+00:00",
            "workdir": str(wd),
            "model": "claude-resume-test",
            "messages": [
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
                {"role": "user", "content": "follow-up"},
            ],
            "tool_call_count": 7,
            "last_input_tokens": 1234,
            "checked_at_index": 2,
        }
        ckpt_path.write_text(json.dumps(ckpt), encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "loom.cli", "run", "--resume"],
            cwd=wd,
            input="exit\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        if "Resumed from checkpoint" not in combined:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"no 'Resumed from checkpoint' in output (rc={proc.returncode}); out={combined[:300]!r}",
            )
        if "3 messages" not in combined:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"missing '3 messages' (history size) in output: {combined[:300]!r}",
            )
        if "7 tool calls" not in combined:
            return EvalResult(
                name=self.name, passed=False,
                detail=f"missing '7 tool calls' (count) in output: {combined[:300]!r}",
            )
        shutil.rmtree(wd, ignore_errors=True)
        return EvalResult(name=self.name, passed=True, detail="resume restored 3 messages / 7 tool calls")
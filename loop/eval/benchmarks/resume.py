"""Cross-session resume success-rate benchmark.

Roadmap §6 success metric: 'Cross-session resume success rate ≥ 90%
(10× kill-and-restart)'.

This benchmark is a SYNTHETIC proxy — it does not measure real-world
LLM-tied resume success. What it actually measures: whether the harness
checkpoint path works end-to-end when the LLM is replaced with a scripted
fixture. It is a canary against regressions in checkpoint save / load /
agent_loop integration, not a measurement of production user success rate.

Trial shape:
  1. Fresh tmpdir + scripted LLM (5 tool_use responses + 1 end_turn).
  2. Run `agent_loop` until 3 tool calls done; simulate kill by snapshotting
     the `messages` list and discarding the in-flight loop.
  3. Save messages via `checkpoint.save` (mirrors what the auto-checkpoint
     would have done if the threshold had fired).
  4. Fresh `agent_loop` invocation with the saved messages + same fixture
     (continues from response #4).
  5. Verify: resumed loop saw the prior 3 tool_use+tool_result blocks in
     its first LLM call; final messages contain all 5 tool calls.

Returns `{"trials": 10, "successes": N, "rate_pct": X, "per_trial": [...]}`.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from anthropic.types import TextBlock

from loop.agent import checkpoint
from loop.agent.context import Context
from loop.agent.loop import agent_loop


@dataclass
class TrialResult:
    trial: int
    success: bool
    reason: str = ""
    detail: str = ""


@dataclass
class BenchmarkReport:
    trials: int
    successes: int
    per_trial: list[TrialResult] = field(default_factory=list)

    @property
    def rate_pct(self) -> int:
        return int(self.successes * 100 / self.trials) if self.trials else 0

    def passed(self, threshold_pct: int = 90) -> bool:
        return self.rate_pct >= threshold_pct


def _build_mock_response(stop_reason: str, blocks: list) -> MagicMock:
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = blocks
    resp.usage = SimpleNamespace(input_tokens=50, output_tokens=20)
    return resp


def _tool_use_block(call_id: str, name: str, input: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = call_id
    block.name = name
    block.input = input
    return block


def _text_block(text: str) -> TextBlock:
    return TextBlock(type="text", text=text)


def _make_llm(script: list) -> MagicMock:
    """Build a mock LLMClient whose `client.messages.create` replays `script`."""
    client = MagicMock()
    responses = [_build_mock_response(*step) for step in script]
    client.messages.create.side_effect = responses
    llm = MagicMock()
    llm.client = client
    llm.model = "fixture-test"
    llm.get_context_window.return_value = 200_000
    return llm


def _make_5_step_script() -> list:
    """5 bash tool_use calls (each prints its step), then end_turn."""
    script = []
    for i in range(1, 6):
        script.append((
            "tool_use",
            [_tool_use_block(f"call-{i}", "bash", {"command": f"echo step-{i}"})],
        ))
    script.append(("end_turn", [_text_block("All 5 steps done.")]))  # type: ignore[list-item]
    return script


def _kill_at_step(workdir: Path, llm, context_obj, messages: list, kill_after: int) -> None:
    """Snapshot messages + write checkpoint, mirroring what auto-checkpoint would do."""
    checkpoint.save(workdir, messages, llm, context_obj, tool_call_count=kill_after)


def _verify_resume_preserved_history(
    resumed_messages: list, pre_kill_messages_count: int, kill_after: int
) -> tuple[bool, str]:
    """The resumed agent_loop's first LLM call should have received the full pre-kill history."""
    if len(resumed_messages) <= pre_kill_messages_count:
        return False, f"resumed loop produced {len(resumed_messages)} msgs, expected > {pre_kill_messages_count}"
    tool_results = sum(
        1 for m in resumed_messages[:pre_kill_messages_count]
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
    )
    if tool_results < kill_after:
        return False, f"resumed messages have only {tool_results} tool_results (need ≥{kill_after})"
    return True, f"{tool_results} tool_results preserved across resume boundary"


def run_one_trial(trial_idx: int, workdir: Path) -> TrialResult:
    """Run a single kill-restart trial; return TrialResult."""
    script = _make_5_step_script()
    context_obj = Context()
    llm_first = _make_llm(script)
    initial_messages = [{"role": "user", "content": "do 5 steps"}]
    try:
        agent_loop(initial_messages, llm_client=llm_first)
    except Exception as exc:
        return TrialResult(trial_idx, False, "first run raised", f"{type(exc).__name__}: {exc}")

    if llm_first.client.messages.create.call_count != 6:
        return TrialResult(
            trial_idx, False,
            "first run made wrong LLM call count",
            f"got {llm_first.client.messages.create.call_count} calls (expected 6 = 5 tool + 1 end_turn)",
        )

    kill_after = 3
    _kill_at_step(workdir, llm_first, context_obj, initial_messages, kill_after)
    pre_kill_count = len(initial_messages)

    llm_second = _make_llm(script[5:])
    loaded = checkpoint.load(workdir)
    if loaded is None:
        return TrialResult(trial_idx, False, "checkpoint disappeared", "load returned None after save")
    second_messages = loaded["messages"]
    try:
        agent_loop(second_messages, llm_client=llm_second)
    except Exception as exc:
        return TrialResult(trial_idx, False, "resumed run raised", f"{type(exc).__name__}: {exc}")

    if llm_second.client.messages.create.call_count != 1:
        return TrialResult(
            trial_idx, False,
            "resumed run made wrong LLM call count",
            f"got {llm_second.client.messages.create.call_count} calls (expected 1 = end_turn)",
        )

    second_call_kwargs = llm_second.client.messages.create.call_args.kwargs
    sent_messages = second_call_kwargs.get("messages", [])
    if len(sent_messages) <= pre_kill_count:
        return TrialResult(
            trial_idx, False,
            "resumed LLM did not receive pre-kill history",
            f"sent {len(sent_messages)} messages, expected > {pre_kill_count}",
        )

    ok, why = _verify_resume_preserved_history(sent_messages, pre_kill_count, kill_after)
    if not ok:
        return TrialResult(trial_idx, False, "resume did not preserve history", why)

    final_tool_calls = sum(
        1 for m in second_messages
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
    )
    if final_tool_calls != 5:
        return TrialResult(
            trial_idx, False,
            "final state wrong",
            f"got {final_tool_calls} tool_results total (expected 5)",
        )

    return TrialResult(trial_idx, True, "5-step kill-resume succeeded", f"{final_tool_calls}/5 tool calls")


def run_resume_benchmark(trials: int = 10) -> BenchmarkReport:
    """Run N trials of the kill-resume benchmark. Returns a BenchmarkReport."""
    per_trial: list[TrialResult] = []
    for i in range(trials):
        wd = Path(tempfile.mkdtemp(prefix=f"loop-resume-trial-{i}-"))
        try:
            per_trial.append(run_one_trial(i, wd))
        finally:
            shutil.rmtree(wd, ignore_errors=True)
    successes = sum(1 for t in per_trial if t.success)
    return BenchmarkReport(trials=trials, successes=successes, per_trial=per_trial)
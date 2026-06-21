"""Harness eval cases for f-max-turns-guard-p0."""

from __future__ import annotations

from loom.eval.runner import EvalCase, EvalResult


class MaxTurnsGuardDefault(EvalCase):
    name = "max-turns-guard-default-100"
    description = "HarnessConfig default max_turns is 100"

    def run(self) -> EvalResult:
        from loom.agent.config import HarnessConfig
        cfg = HarnessConfig.from_defaults()
        if cfg.max_turns != 100:
            return EvalResult(name=self.name, passed=False, detail=f"max_turns={cfg.max_turns}, expected 100")
        return EvalResult(name=self.name, passed=True, detail="max_turns=100 default confirmed")


class MaxTurnsGuardTraceEventDefined(EvalCase):
    name = "max-turns-guard-trace-event-defined"
    description = "agent_loop records a 'loop_limit_reached' trace event on exhaustion"

    def run(self) -> EvalResult:
        import inspect

        from loom.agent import loop as loop_mod
        src = inspect.getsource(loop_mod.agent_loop)
        if '"loop_limit_reached"' not in src and "'loop_limit_reached'" not in src:
            return EvalResult(name=self.name, passed=False, detail="loop_limit_reached not in agent_loop source")
        return EvalResult(name=self.name, passed=True, detail="loop_limit_reached trace event present")


class MaxTurnsGuardLoopIsBounded(EvalCase):
    name = "max-turns-guard-loop-is-bounded"
    description = "agent_loop main loop is bounded (no unbounded while True)"

    def run(self) -> EvalResult:
        import inspect

        from loom.agent import loop as loop_mod
        src = inspect.getsource(loop_mod.agent_loop)
        if "while True" in src and "max_turns" not in src:
            return EvalResult(name=self.name, passed=False, detail="while True without max_turns guard")
        if "for turn in range" not in src and "for _ in range" not in src:
            return EvalResult(name=self.name, passed=False, detail="no bounded range loop in agent_loop")
        return EvalResult(name=self.name, passed=True, detail="agent_loop main loop is bounded")

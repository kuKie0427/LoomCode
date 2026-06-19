from __future__ import annotations

import copy
import io

from loguru import logger

from loom.agent.hooks import HOOKS, Hooks
from loom.eval.runner import EvalCase, EvalResult


class HooksDictHasSessionStartAndEndKeys(EvalCase):
    name = "hooks-dict-has-session-start-and-end-keys"
    description = "HOOKS dict contains SessionStart as first key and SessionEnd as last key"

    def run(self) -> EvalResult:
        keys = list(HOOKS.keys())
        if "SessionStart" not in HOOKS:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="HOOKS dict missing SessionStart key",
            )
        if "SessionEnd" not in HOOKS:
            return EvalResult(
                name=self.name,
                passed=False,
                detail="HOOKS dict missing SessionEnd key",
            )
        if keys[0] != "SessionStart":
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"SessionStart is not first key; first key is {keys[0]!r}",
            )
        if keys[-1] != "SessionEnd":
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"SessionEnd is not last key; last key is {keys[-1]!r}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"SessionStart first, SessionEnd last ({len(keys)} hook keys total)",
        )


def _save_hooks() -> dict:
    return copy.deepcopy(HOOKS)


def _restore_hooks(saved: dict) -> None:
    HOOKS.clear()
    HOOKS.update(saved)


class SessionStartTriggerNoArgs(EvalCase):
    name = "session-start-trigger-no-args"
    description = "trigger_hooks('SessionStart') with noop callback does not raise"

    _saved_hooks: dict

    def setup(self) -> None:
        self._saved_hooks = _save_hooks()

    def teardown(self) -> None:
        _restore_hooks(self._saved_hooks)

    def run(self) -> EvalResult:
        Hooks().register_hook("SessionStart", lambda event, *args: None)
        try:
            Hooks().trigger_hooks("SessionStart")
        except Exception as e:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"trigger_hooks raised {type(e).__name__}: {e}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="trigger_hooks('SessionStart') completed without error",
        )


class SessionEndTriggerWithArgs(EvalCase):
    name = "session-end-trigger-with-args"
    description = "trigger_hooks('SessionEnd', [], 0) with noop callback does not raise"

    _saved_hooks: dict

    def setup(self) -> None:
        self._saved_hooks = _save_hooks()

    def teardown(self) -> None:
        _restore_hooks(self._saved_hooks)

    def run(self) -> EvalResult:
        Hooks().register_hook("SessionEnd", lambda event, *args: None)
        try:
            Hooks().trigger_hooks("SessionEnd", [], 0)
        except Exception as e:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"trigger_hooks raised {type(e).__name__}: {e}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="trigger_hooks('SessionEnd', [], 0) completed without error",
        )


class LogHookSessionStartLogged(EvalCase):
    name = "log-hook-session-start-logged"
    description = "log_hook logs '[Session started]' on SessionStart event"

    _saved_hooks: dict

    def setup(self) -> None:
        self._saved_hooks = _save_hooks()

    def teardown(self) -> None:
        _restore_hooks(self._saved_hooks)

    def run(self) -> EvalResult:
        hooks = Hooks()
        hooks.register_hook("SessionStart", hooks.log_hook)
        buf = io.StringIO()
        handler_id = logger.add(buf, format="{message}", level="INFO")
        try:
            hooks.trigger_hooks("SessionStart")
            output = buf.getvalue()
        finally:
            logger.remove(handler_id)
        if "[Session started]" not in output:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"Expected '[Session started]' in log, got: {output!r}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail="[Session started] found in log output",
        )


class LogHookSessionEndLogged(EvalCase):
    name = "log-hook-session-end-logged"
    description = "log_hook logs '[Session ended: N tool calls, M messages]' on SessionEnd event"

    _saved_hooks: dict

    def setup(self) -> None:
        self._saved_hooks = _save_hooks()

    def teardown(self) -> None:
        _restore_hooks(self._saved_hooks)

    def run(self) -> EvalResult:
        hooks = Hooks()
        hooks.register_hook("SessionEnd", hooks.log_hook)
        buf = io.StringIO()
        handler_id = logger.add(buf, format="{message}", level="INFO")
        try:
            hooks.trigger_hooks("SessionEnd", ["msg1", "msg2"], 5)
            output = buf.getvalue()
        finally:
            logger.remove(handler_id)
        expected = "[Session ended: 5 tool calls, 2 messages]"
        if expected not in output:
            return EvalResult(
                name=self.name,
                passed=False,
                detail=f"Expected {expected!r} in log, got: {output!r}",
            )
        return EvalResult(
            name=self.name,
            passed=True,
            detail=f"Found {expected!r} in log output",
        )
